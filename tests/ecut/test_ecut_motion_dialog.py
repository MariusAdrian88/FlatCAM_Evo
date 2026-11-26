"""Pure UI configuration and calculators never access a motion controller."""
from copy import deepcopy
import importlib
import math
import sys
from types import SimpleNamespace

from PyQt6 import QtCore, QtWidgets
import pytest


@pytest.fixture
def module():
    return importlib.import_module('appPlugins.ToolGcodeSender.SenderMotion')


@pytest.fixture
def snapshot():
    return {'revision': 'saved-1', 'interpolation_factor': 64,
            'axes': [dict(steps_per_mm=400, max_velocity_mm_s=5,
                          acceleration_mm_s2=40, deceleration_mm_s2=None,
                          soft_limits_enabled=False, soft_limit_negative_mm=-100,
                          soft_limit_positive_mm=100, backlash_mm=0.0,
                          backlash_compensation_enabled=False) for _ in range(4)],
            'motors': [dict(steps_per_revolution=200, microsteps=8,
                            gear_ratio=1, travel_per_revolution_mm=4) for _ in range(4)]}


@pytest.fixture
def dialog(module, snapshot):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = module.EcutMotionDialog(snapshot)
    yield widget
    widget.set_busy(False)
    widget.close()
    widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def test_defaults_conversion_dirty_and_successful_reload(dialog, snapshot):
    assert dialog.motion_fields[0]['max_velocity_mm_s'].value() == 300
    assert dialog.values() == {key: snapshot[key] for key in ('revision', 'axes', 'motors')}
    assert not dialog.is_dirty()
    dialog.motion_fields[1]['max_velocity_mm_s'].setValue(600)
    assert dialog.values()['axes'][1]['max_velocity_mm_s'] == 10
    assert dialog.is_dirty()
    updated = deepcopy(snapshot)
    updated['axes'][1]['max_velocity_mm_s'] = 10
    updated['revision'] = 'saved-2'
    dialog.load_settings(updated)
    assert not dialog.is_dirty()
    assert dialog.values()['revision'] == 'saved-2'


def test_native_integral_float_steps_load_without_truncating_fractions(module, snapshot):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = deepcopy(snapshot)
    settings['axes'][0]['steps_per_mm'] = 800.0
    settings['motors'][0]['microsteps'] = 8.0
    widget = module.EcutMotionDialog(settings)
    try:
        assert widget.values()['axes'][0]['steps_per_mm'] == 800
        assert isinstance(widget.values()['axes'][0]['steps_per_mm'], int)
        assert widget.values()['motors'][0]['microsteps'] == 8
        settings['axes'][0]['steps_per_mm'] = 800.5
        with pytest.raises(ValueError, match='whole'):
            widget.load_settings(settings)
        assert widget.values()['axes'][0]['steps_per_mm'] == 800
        assert not widget.is_dirty()
        settings['axes'][0]['steps_per_mm'] = 800
        settings['motors'][0]['microsteps'] = 8.5
        with pytest.raises(ValueError, match='whole'):
            widget.load_settings(settings)
        assert widget.values()['motors'][0]['microsteps'] == 8
    finally:
        widget.set_busy(False)
        widget.close()
        widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_calculators_use_physical_microsteps_and_round_explicitly(module):
    assert module.motor_steps_per_mm(200, 8, 1, 4) == 400
    result = module.steps_candidate(400.4)
    assert result['exact'] == 400.4 and result['supported'] == 400
    assert result['error_percent'] == pytest.approx((400 / 400.4 - 1) * 100)
    assert module.calibrated_steps_per_mm(400, 1, 0.8) == 500


@pytest.mark.parametrize('value', [0, math.nan, 2147483648])
def test_candidate_rejects_unsupported_values(module, value):
    with pytest.raises(ValueError):
        module.steps_candidate(value)


@pytest.mark.parametrize('args', [(200, 8, 1, 0), (200, 1.5, 1, 4), (200, 8, math.nan, 4)])
def test_motor_calculator_rejects_invalid_values(module, args):
    with pytest.raises(ValueError):
        module.motor_steps_per_mm(*args)


@pytest.mark.parametrize('args', [(400, 1, 0), (400, 0, 1), (0, 1, 1),
                                  (400, 1, math.inf),
                                  (400, 1, 1e-12)])
def test_calibration_calculator_rejects_invalid_results(module, args):
    with pytest.raises(ValueError):
        module.calibrated_steps_per_mm(*args)


def test_independent_deceleration_and_limits(dialog):
    fields = dialog.motion_fields[0]
    fields['follow_acceleration'].setChecked(False)
    fields['deceleration_mm_s2'].setValue(25)
    assert dialog.values()['axes'][0]['deceleration_mm_s2'] == 25
    fields['soft_limits_enabled'].setChecked(True)
    fields['soft_limit_negative_mm'].setValue(10)
    fields['soft_limit_positive_mm'].setValue(5)
    with pytest.raises(ValueError):
        dialog.values()
    emitted = []
    dialog.apply_requested.connect(emitted.append)
    dialog.apply_button.click()
    assert not emitted and dialog.status_label.text()


def test_calculation_only_changes_pending_values_on_explicit_use(dialog):
    emitted = []
    dialog.apply_requested.connect(emitted.append)
    dialog.motor_fields[0]['microsteps'].setValue(16)
    dialog.motor_calculate_buttons[0].click()
    assert dialog.values()['axes'][0]['steps_per_mm'] == 400
    dialog.motor_use_buttons[0].click()
    assert dialog.values()['axes'][0]['steps_per_mm'] == 800
    assert not emitted


def test_calibration_move_uses_applied_revision_and_refuses_dirty(dialog):
    moves = []
    saves = []
    dialog.calibration_requested.connect(lambda *args: moves.append(args))
    dialog.apply_requested.connect(saves.append)
    dialog.calibration_axis.setCurrentIndex(2)
    dialog.calibration_measured.setValue(0.8)
    dialog.calibration_calculate_button.click()
    assert not moves and not saves
    dialog.calibration_move_button.click()
    assert moves == [(2, 1.0, 60.0, 'saved-1')]
    dialog.calibration_finished({'message': 'Test completed'})
    dialog.calibration_use_button.click()
    assert dialog.values()['axes'][2]['steps_per_mm'] == 500
    assert dialog.calibration_current.text() == '400'
    assert not dialog.calibration_move_button.isEnabled()
    dialog._request_calibration()
    assert len(moves) == 1


def test_busy_blocks_mutation_actions_and_close(dialog):
    saves = []
    moves = []
    dialog.apply_requested.connect(saves.append)
    dialog.calibration_requested.connect(lambda *args: moves.append(args))
    dialog.show()
    dialog.set_busy(True)
    assert not dialog.tabs.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    assert not dialog.close()
    dialog._request_apply()
    dialog._request_calibration()
    assert not saves and not moves
    dialog.set_busy(False)
    dialog.reject()
    assert not dialog.isVisible()


def test_actions_require_explicit_click_and_apply_does_not_mark_saved(dialog):
    for button in dialog.findChildren(QtWidgets.QPushButton):
        assert not button.autoDefault()
        assert not button.isDefault()
    saves = []
    dialog.apply_requested.connect(saves.append)
    dialog.motion_fields[0]['steps_per_mm'].setValue(800)
    dialog.apply_button.click()
    assert saves[0]['axes'][0]['steps_per_mm'] == 800
    assert dialog.is_dirty()
    assert not dialog.apply_button.isEnabled()
    dialog.show_error('Save failed')
    assert dialog.is_dirty() and dialog.apply_button.isEnabled()


def test_stop_only_available_for_running_calibration_and_never_automatic(dialog):
    stops = []
    dialog.stop_requested.connect(lambda: stops.append(True))
    assert not dialog.calibration_stop_button.isEnabled()
    dialog.set_busy(True)
    assert not dialog.calibration_stop_button.isEnabled()
    dialog.set_calibration_running(True)
    assert dialog.calibration_stop_button.isEnabled()
    assert not dialog.tabs.isEnabled() and not stops
    dialog.calibration_stop_button.click()
    assert stops == [True]
    dialog.calibration_finished({'message': 'Stopped'})
    assert not dialog.calibration_stop_button.isEnabled()
    dialog.calibration_stop_button.click()
    assert stops == [True]
    dialog.set_busy(True)
    assert not dialog.calibration_stop_button.isEnabled()
    dialog.show_error('Save failed')
    assert not dialog.calibration_stop_button.isEnabled()


def test_cancel_has_no_side_effect_and_defaults_are_assumptions(module, snapshot):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = deepcopy(snapshot)
    del source['motors']
    widget = module.EcutMotionDialog(source)
    try:
        assert widget.values()['motors'][0] == dict(steps_per_revolution=200, microsteps=1,
                                                   gear_ratio=1, travel_per_revolution_mm=1)
        assert 'assumptions' in widget.motor_note.text().lower()
        saves = []
        widget.apply_requested.connect(saves.append)
        widget.motion_fields[0]['steps_per_mm'].setValue(800)
        widget.reject()
        assert not saves and source == {key: snapshot[key] for key in snapshot if key != 'motors'}
    finally:
        widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_optional_hardware_tabs_follow_settings_and_forward_signals(module, snapshot, monkeypatch):
    class Hardware(QtWidgets.QWidget):
        changed = QtCore.pyqtSignal()
        output_requested = QtCore.pyqtSignal(int, bool)

        def __init__(self, settings, parent=None):
            super().__init__(parent)
            self.pages = [('I/O', QtWidgets.QWidget())]
            self.load_settings(settings)
            self.busy = False
            self.state = None

        def values(self):
            return deepcopy(self.settings)

        def load_settings(self, settings):
            self.settings = deepcopy(settings)

        def set_busy(self, busy):
            self.busy = busy

        def update_io(self, state):
            self.state = state

        def io_idle(self):
            return False

    monkeypatch.setitem(sys.modules, 'appPlugins.ToolGcodeSender.SenderEcutIO',
                        SimpleNamespace(EcutHardwareTabs=Hardware))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = deepcopy(snapshot)
    settings['hardware'] = {'inputs': [1, 2]}
    widget = module.EcutMotionDialog(settings)
    try:
        assert widget.tabs.count() == 4
        assert widget.values()['hardware'] == {**settings['hardware'], 'soft_limit_tolerance_mm': 0.05}
        assert not widget.is_dirty()
        outputs = []
        widget.output_requested.connect(lambda *args: outputs.append(args))
        widget.hardware_tabs.output_requested.emit(2, True)
        assert outputs == [(2, True)]
        widget.hardware_tabs.settings['inputs'] = [3]
        widget.hardware_tabs.changed.emit()
        assert widget.is_dirty() and not widget.calibration_move_button.isEnabled()
        widget.set_busy(True)
        assert widget.hardware_tabs.busy
        widget.update_io({'inputs': 7})
        assert widget.hardware_tabs.state == {'inputs': 7}
        widget.load_settings(settings)
        assert not widget.is_dirty() and not widget.hardware_tabs.busy
        assert widget.tabs.count() == 4
    finally:
        widget.set_busy(False)
        widget.close()
        widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def _profile_with_axes():
    from appCommon.machine_profile.profile import MachineProfile
    return MachineProfile(name='Bench', axis_velocity_max={'X': 1200.0}, axis_accel_max={'Z': 80.0},
                          soft_limit_min={'Z': -40.0}, soft_limit_max={'Z': 0.0})


def _answer(monkeypatch, button):
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return button
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', staticmethod(question))
    return asked


def test_profile_axis_values_reach_the_fields_only_after_the_user_confirms(dialog, monkeypatch):
    Button = QtWidgets.QMessageBox.StandardButton
    assert not dialog.profile_button.isEnabled()
    dialog.set_machine_profile(_profile_with_axes())
    assert dialog.profile_button.isEnabled()
    before = deepcopy(dialog.values())
    asked = _answer(monkeypatch, Button.No)
    dialog.profile_button.click()
    assert len(asked) == 1 and 'X' in asked[0] and '1200' in asked[0] and '300' in asked[0]
    assert dialog.values() == before
    asked = _answer(monkeypatch, Button.Yes)
    dialog.profile_button.click()
    values = dialog.values()['axes']
    assert values[0]['max_velocity_mm_s'] == 20 and values[1] == before['axes'][1]
    assert values[2]['acceleration_mm_s2'] == 80
    assert (values[2]['soft_limit_negative_mm'], values[2]['soft_limit_positive_mm']) == (-40, 0)
    assert values[2]['soft_limits_enabled'] and not values[0]['soft_limits_enabled']
    assert dialog.is_dirty()


def test_profile_without_ecut_axis_values_changes_nothing_and_asks_nothing(dialog, monkeypatch):
    from appCommon.machine_profile.profile import MachineProfile
    asked = _answer(monkeypatch, QtWidgets.QMessageBox.StandardButton.Yes)
    dialog.set_machine_profile(MachineProfile(name='Bare'))
    assert not dialog.profile_button.isEnabled()
    dialog.set_machine_profile(None)
    assert not dialog.profile_button.isEnabled() and asked == []
