# ##########################################################
# FlatCAM Evo: E-Cut motion settings dialog                #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/20/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

"""E-Cut motion settings UI; controller access belongs to the sender workers."""
from copy import deepcopy
import math
import gettext

from PyQt6 import QtCore, QtWidgets
from appGUI.GUIElements import FCLabel, FCButton, FCCheckBox, FCSpinner, FCDoubleSpinner, FCComboBox

_ = gettext.gettext
MAX_STEPS_PER_MM = 2147483647
MOTOR_DEFAULTS = dict(steps_per_revolution=200, microsteps=1,
                      gear_ratio=1, travel_per_revolution_mm=1)


def _positive(value, name):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(_('%s must be a positive finite number.') % name) from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(_('%s must be a positive finite number.') % name)
    return result


def _whole(value, name):
    result = _positive(value, name)
    if not result.is_integer() or result > MAX_STEPS_PER_MM:
        raise ValueError(_('%s must be a whole number between 1 and 2147483647.') % name)
    return int(result)


def steps_candidate(exact):
    """Return the nearest native whole calibration and its relative error."""
    exact = _positive(exact, _('Steps/mm'))
    if not 1 <= exact <= MAX_STEPS_PER_MM:
        raise ValueError(_('Steps/mm must be between 1 and 2147483647.'))
    supported = int(math.floor(exact + 0.5))
    return dict(exact=exact, supported=supported,
                error_percent=(supported / exact - 1) * 100)


def motor_steps_per_mm(steps_per_revolution, microsteps, gear_ratio, travel_per_revolution_mm):
    """Physical driver pulses per mm; board interpolation is unrelated."""
    steps = _positive(steps_per_revolution, _('Full steps per motor revolution'))
    microsteps = _positive(microsteps, _('Microsteps'))
    if not steps.is_integer() or not microsteps.is_integer():
        raise ValueError(_('Motor steps and microsteps must be whole numbers.'))
    result = steps * microsteps * _positive(gear_ratio, _('Gear ratio')) / _positive(
        travel_per_revolution_mm, _('Travel per screw revolution'))
    steps_candidate(result)
    return result


def calibrated_steps_per_mm(old_steps, commanded_mm, measured_mm):
    result = (_positive(old_steps, _('Applied steps/mm')) *
              _positive(commanded_mm, _('Requested travel')) /
              _positive(measured_mm, _('Measured travel')))
    steps_candidate(result)
    return result


class EcutMotionDialog(QtWidgets.QDialog):
    apply_requested = QtCore.pyqtSignal(object)
    calibration_requested = QtCore.pyqtSignal(int, float, float, str)
    output_requested = QtCore.pyqtSignal(int, bool)
    stop_requested = QtCore.pyqtSignal()
    reference_requested = QtCore.pyqtSignal(list)
    sensor_home_requested = QtCore.pyqtSignal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_('E-Cut machine settings'))
        self.resize(760, 650)
        self.setMinimumSize(650, 540)
        self._busy = False
        self._machine_profile = None
        self._calibration_running = False
        self._loading = True
        self._snapshot = {}
        self._baseline = {}
        self._motor_candidates = [None] * 4
        self._calibration_candidate = None
        self.motion_fields = []
        self.motor_fields = []
        self.motor_calculate_buttons = []
        self.motor_use_buttons = []
        self.motor_result_labels = []
        self.hardware_tabs = None
        self._homed_axes = set()
        self.home_buttons = []
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)
        self._build_motion()
        self._build_motors()
        self._build_calibration()
        self.status_label = FCLabel('')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QHBoxLayout()
        self.calibration_stop_button = FCButton(_('Stop test move'))
        self.calibration_stop_button.setEnabled(False)
        self.calibration_stop_button.clicked.connect(self._request_stop)
        buttons.addWidget(self.calibration_stop_button)
        buttons.addStretch()
        self.calibration_stop_button.setToolTip(_('Stops the running test move.'))
        self.cancel_button = FCButton(_('Cancel'))
        self.cancel_button.setToolTip(_('Closes the dialog; pending edits are discarded.'))
        self.apply_button = FCButton(_('Apply'))
        self.apply_button.setToolTip(_('Saves the settings of all tabs in the E-Cut.'))
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self._request_apply)
        # Enter in a number field must not start a move or apply settings.
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.load_settings(settings)

    def _spin(self, integer=False, minimum=0.000000001, maximum=1e12):
        if integer:
            widget = FCSpinner(policy=False)
            widget.setRange(int(minimum), int(maximum))
        else:
            # set_precision() before set_range(): the input validator is rebuilt from both (minus sign, 9 decimals).
            widget = FCDoubleSpinner(policy=False)
            widget.set_precision(9)
            widget.set_range(minimum, maximum)
            widget.setSingleStep(1)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(self._changed)
        return widget

    def _page(self, title):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return layout

    def _axis_pages(self, layout):
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)
        forms = []
        for name in 'XYZA':
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            tabs.addTab(page, name)
            forms.append(form)
        return forms

    def _note(self, text, layout):
        label = FCLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def _build_motion(self):
        layout = self._page(_('Motion'))
        self._note(_('Axis values use the sender\'s linear mm convention, including A. '
                     'Soft limits use machine coordinates, not the work zero.'), layout)
        labels = [('steps_per_mm', _('Steps/mm (whole number)')),
                  ('max_velocity_mm_s', _('Maximum speed (mm/min)')),
                  ('acceleration_mm_s2', _('Acceleration (mm/s²)')),
                  ('deceleration_mm_s2', _('Deceleration (mm/s²)')),
                  ('soft_limit_negative_mm', _('Minimum machine position (mm)')),
                  ('soft_limit_positive_mm', _('Maximum machine position (mm)')),
                  ('backlash_mm', _('Backlash (mm)'))]
        tips = {
            'steps_per_mm': _('Driver pulses per mm of travel of this axis, a whole number. '
                              'Saved in the E-Cut when you click Apply.'),
            'max_velocity_mm_s': _('Highest feed speed the E-Cut plans for this axis, in mm/min. '
                                   'Saved when you click Apply.'),
            'acceleration_mm_s2': _('Rate at which this axis speeds up, in mm/s². Saved when you click Apply.'),
            'deceleration_mm_s2': _('Rate at which this axis slows down, in mm/s². '
                                    'Not used while \"Use acceleration for deceleration\" is checked.'),
            'soft_limit_negative_mm': _('Lowest allowed machine position of this axis in mm '
                                        '(machine coordinates, not the work zero).'),
            'soft_limit_positive_mm': _('Highest allowed machine position of this axis in mm '
                                        '(machine coordinates, not the work zero).'),
            'backlash_mm': _('Play of this axis when its direction reverses, 0 to 1 mm. '
                             'Used when \\"Compensate backlash\\" is checked.')}
        for form in self._axis_pages(layout):
            fields = {}
            for key, label in labels:
                if key == 'steps_per_mm':
                    widget = self._spin(True, 1, MAX_STEPS_PER_MM)
                elif key.startswith('soft_limit_'):
                    widget = self._spin(minimum=-1e12)
                elif key == 'backlash_mm':
                    widget = self._spin(minimum=0, maximum=1.0)
                    widget.setSingleStep(0.01)
                else:
                    widget = self._spin()
                widget.setToolTip(tips[key])
                fields[key] = widget
                form.addRow(label, widget)
                if key == 'acceleration_mm_s2':
                    checkbox = FCCheckBox(_('Use acceleration for deceleration'))
                    checkbox.setToolTip(_('The axis slows down at the acceleration rate; '
                                          'the deceleration field is ignored.'))
                    checkbox.toggled.connect(self._changed)
                    fields['follow_acceleration'] = checkbox
                    form.addRow(checkbox)
                elif key == 'deceleration_mm_s2':
                    fields['follow_acceleration'].toggled.connect(
                        lambda checked, target=widget: target.setEnabled(not checked))
            checkbox = FCCheckBox(_('Enable soft limits'))
            checkbox.setToolTip(_('Moves outside the minimum and maximum machine positions of this axis '
                                  'are refused. Saved when you click Apply.'))
            checkbox.toggled.connect(self._changed)
            fields['soft_limits_enabled'] = checkbox
            form.addRow(checkbox)
            checkbox = FCCheckBox(_('Compensate backlash'))
            checkbox.setToolTip(_('Adds the backlash distance to each move that reverses this axis. '
                                  'Needs \\"Backlash compensation\\" in the Not verified tab. '
                                  'A change needs the machine home again.'))
            checkbox.toggled.connect(self._changed)
            fields['backlash_compensation_enabled'] = checkbox
            form.addRow(checkbox)
            self.motion_fields.append(fields)
        self._build_motion_extras(layout)

    def _build_motion_extras(self, layout):
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        # Kept as the Qt spin box (also for bed_size): with FCDoubleSpinner a typed '-0.5' in a 3-decimal field
        # with minimum 0 silently became 0.5 (the Qt box gives -0.0 / keeps the old value).
        self.soft_limit_tolerance = QtWidgets.QDoubleSpinBox()
        self.soft_limit_tolerance.setDecimals(3)
        self.soft_limit_tolerance.setRange(0, 1)
        self.soft_limit_tolerance.setSingleStep(0.01)
        self.soft_limit_tolerance.setValue(0.05)
        self.soft_limit_tolerance.setKeyboardTracking(False)
        self.soft_limit_tolerance.setToolTip(_(
            'The board moves in whole counts, so a target can end this far outside a limit.'))
        self.soft_limit_tolerance.valueChanged.connect(self._changed)
        form.addRow(_('Soft limit tolerance (mm)'), self.soft_limit_tolerance)
        group = QtWidgets.QGroupBox(_('Machine travel (bed size)'))
        group_form = QtWidgets.QFormLayout(group)
        self.bed_size = {}
        for key, text in (('x', _('X size (mm)')), ('y', _('Y size (mm)')), ('z', _('Z travel (mm)'))):
            widget = QtWidgets.QDoubleSpinBox()
            widget.setDecimals(3)
            widget.setRange(0, 1e6)
            widget.setKeyboardTracking(False)
            widget.setToolTip(_('Size of the machine travel of this axis in mm. '
                                '\"Set soft limits from bed size\" uses it to fill the soft limits.'))
            self.bed_size[key] = widget
            group_form.addRow(text, widget)
        self.bed_size_button = FCButton(_('Set soft limits from bed size'))
        self.bed_size_button.setToolTip(_(
            'Fills the minimum and maximum machine positions: X 0 to X size, Y 0 to Y size, '
            'Z from -Z travel to 0 (Z homes at the top). A is ignored. Click Apply to save.'))
        self.bed_size_button.clicked.connect(self._set_limits_from_bed)
        group_form.addRow(self.bed_size_button)
        layout.addWidget(group)
        self.profile_button = FCButton(_('Apply machine profile values'))
        self.profile_button.setToolTip(_(
            'Shows the maximum speed, acceleration and soft limits that the active machine profile sets for X, Y and Z, '
            'next to the current values. After you confirm, the fields are filled; click Apply to save them. '
            'Values that the profile does not set stay as they are.'))
        self.profile_button.setEnabled(False)
        self.profile_button.clicked.connect(self._apply_machine_profile)
        layout.addWidget(self.profile_button)
        home_group = QtWidgets.QGroupBox(_('Machine home'))
        home_layout = QtWidgets.QVBoxLayout(home_group)
        row = QtWidgets.QHBoxLayout()
        home_layout.addLayout(row)
        tip = _('No switch: the current position becomes the machine home of the axis '
                '(its home position, normally 0). Jog to the home corner first; Z homes at the top.')
        for index, name in enumerate(list('XYZA') + [None]):
            button = FCButton(_('Home all') if name is None else _('Home %s') % name)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, axis=index: self._request_reference(axis))
            row.addWidget(button)
            self.home_buttons.append(button)
        sensor_button = FCButton(_('Home (sensor)'))
        sensor_button.setToolTip(_('Runs the home switch cycle ($H): Z first, then X and Y. '
                                   'Needs "Homing" under Not verified on hardware.'))
        sensor_button.clicked.connect(lambda checked=False: self.sensor_home_requested.emit())
        row.addWidget(sensor_button)
        self.home_buttons.append(sensor_button)
        self.home_label = FCLabel('')
        home_layout.addWidget(self.home_label)
        layout.addWidget(home_group)
        self._update_home()

    def set_machine_profile(self, profile):
        """The active machine profile (or None). The button is on only if the profile sets an E-Cut axis value."""
        self._machine_profile = profile
        self.profile_button.setEnabled(bool(profile is not None and self._profile_updates()))

    def _profile_current(self):
        return {name: {'axis_velocity_max': fields['max_velocity_mm_s'].value(),
                       'axis_accel_max': fields['acceleration_mm_s2'].value(),
                       'soft_limit_min': fields['soft_limit_negative_mm'].value(),
                       'soft_limit_max': fields['soft_limit_positive_mm'].value()}
                for name, fields in zip('XYZ', self.motion_fields)}

    def _profile_updates(self):
        from appCommon.machine_profile.ecut_binding import ecut_axis_updates
        return ecut_axis_updates(self._machine_profile, self._profile_current())

    def _apply_machine_profile(self):
        """Fill the fields from the profile after the user confirms the old and new values. Nothing is saved here."""
        if self._machine_profile is None:
            return
        updates = self._profile_updates()
        if not updates:
            return
        current = self._profile_current()
        names = (('axis_velocity_max', _('Maximum speed (mm/min)')), ('axis_accel_max', _('Acceleration (mm/s²)')),
                 ('soft_limit_min', _('Minimum machine position (mm)')),
                 ('soft_limit_max', _('Maximum machine position (mm)')))
        lines = [_('Machine profile "%s" sets these E-Cut values:') % self._machine_profile.name]
        for axis, changes in updates.items():
            for key, label in names:
                if key in changes:
                    lines.append('%s  %s: %.6g -> %.6g' % (axis, label, current[axis][key], changes[key]))
        lines.append(_('The fields are filled. Click Apply to save them in the E-Cut.'))
        answer = QtWidgets.QMessageBox.question(self, _('Apply machine profile values'), '\n'.join(lines))
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        keys = {'axis_velocity_max': 'max_velocity_mm_s', 'axis_accel_max': 'acceleration_mm_s2',
                'soft_limit_min': 'soft_limit_negative_mm', 'soft_limit_max': 'soft_limit_positive_mm'}
        for axis, changes in updates.items():
            fields = self.motion_fields['XYZ'.index(axis)]
            for key, value in changes.items():
                fields[keys[key]].setValue(value)
            if 'soft_limit_min' in changes or 'soft_limit_max' in changes:
                fields['soft_limits_enabled'].setChecked(True)
        self.status_label.setText(_('Machine profile values are pending. Click Apply to save them.'))

    def _set_limits_from_bed(self):
        sizes = {key: widget.value() for key, widget in self.bed_size.items()}
        ranges = {0: (0, sizes['x']), 1: (0, sizes['y']), 2: (-sizes['z'], 0)}
        skipped = []
        for axis, (low, high) in ranges.items():
            if low >= high:
                skipped.append('XYZ'[axis])
                continue
            fields = self.motion_fields[axis]
            fields['soft_limit_negative_mm'].setValue(low)
            fields['soft_limit_positive_mm'].setValue(high)
            fields['soft_limits_enabled'].setChecked(True)
        self.status_label.setText(
            (_('Enter a size above 0 for: %s.') % ', '.join(skipped)) if skipped else
            _('Soft limits are pending. Click Apply to save them.'))

    def _home_axes(self, axis):
        if axis < 4:
            return [axis]
        axes = self._snapshot.get('hardware', {}).get('axes', [])
        return [index for index in range(4)
                if index >= len(axes) or axes[index].get('enabled', True)]

    def _home_allowed(self):
        return not self._busy and self.hardware_tabs is not None and self.hardware_tabs.io_idle()

    def _request_reference(self, axis):
        if not self._home_allowed():
            return
        axes = self._home_axes(axis)
        if not axes:
            self.status_label.setText(_('No axis is enabled.'))
            return
        names = ', '.join('XYZA'[index] for index in axes)
        answer = QtWidgets.QMessageBox.question(
            self, _('Set machine home'),
            _('Set the current position as machine home of %s? The machine coordinates change.') % names)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes or not self._home_allowed():
            return
        self.set_busy(True)
        self.status_label.setText(_('Setting machine home…'))
        self.reference_requested.emit(axes)

    def _update_home(self):
        allowed = self._home_allowed()
        for button in self.home_buttons:
            button.setEnabled(allowed)
        self.home_label.setText(_('Machine home: ') + ' '.join(
            '%s %s' % (name, '✓' if index in self._homed_axes else '—') for index, name in enumerate('XYZA')))

    def _build_motors(self):
        layout = self._page(_('Motor setup'))
        self.motor_note = self._note(
            _('Microsteps are set manually on the driver DIP switches. Enter their actual setting here. '
              'Default values (200 steps, 1 microstep, ratio 1, travel 1 mm) are editable assumptions, '
              'not verified hardware settings. Applying saves this information; it does not change the driver.'), layout)
        self.interpolation_label = self._note('', layout)
        for axis, form in enumerate(self._axis_pages(layout)):
            fields = {}
            for key, label, tip in [
                    ('steps_per_revolution', _('Full steps per motor revolution (often 200 or 400)'),
                     _('Full steps of the motor for one revolution, without microstepping.')),
                    ('microsteps', _('Microsteps (driver DIP setting)'),
                     _('Microstep setting of the driver, as set on its DIP switches. It is not changed by this dialog.')),
                    ('gear_ratio', _('Motor revolutions per screw revolution'),
                     _('Motor revolutions needed for one revolution of the screw (1 = direct drive).')),
                    ('travel_per_revolution_mm', _('Travel per screw revolution (mm)'),
                     _('Axis travel for one screw revolution (the screw lead), in mm.'))]:
                widget = self._spin(True, 1, MAX_STEPS_PER_MM) if key in (
                    'steps_per_revolution', 'microsteps') else self._spin()
                widget.setToolTip(tip)
                widget.valueChanged.connect(lambda value, index=axis: self._invalidate_motor(index))
                fields[key] = widget
                form.addRow(label, widget)
            formula = FCLabel(_('Steps/mm = full steps × microsteps × motor/screw ratio ÷ travel'))
            formula.setWordWrap(True)
            form.addRow(formula)
            calculate = FCButton(_('Calculate steps/mm'))
            calculate.setToolTip(_('Calculates steps/mm from the motor values above. '
                                   'It does not move the machine and saves nothing.'))
            calculate.clicked.connect(lambda checked=False, index=axis: self._calculate_motor(index))
            form.addRow(calculate)
            result = FCLabel('')
            result.setWordWrap(True)
            form.addRow(result)
            use = FCButton(_('Use calculated value'))
            use.setToolTip(_('Copies the calculated whole steps/mm into the Motion tab. '
                             'Nothing is saved until you click Apply.'))
            use.clicked.connect(lambda checked=False, index=axis: self._use_motor(index))
            form.addRow(use)
            self.motor_fields.append(fields)
            self.motor_calculate_buttons.append(calculate)
            self.motor_use_buttons.append(use)
            self.motor_result_labels.append(result)

    def _build_calibration(self):
        layout = self._page(_('Calibration'))
        self._note(_('Measure a short test move, then calculate a corrected steps/mm value. '
                     'A test move uses the applied settings. Apply pending edits before moving. '
                     'Calculating and using a value do not move or save anything.'), layout)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.calibration_axis = FCComboBox(policy=False)
        self.calibration_axis.setToolTip(_('Axis for the test move and the steps/mm calculation.'))
        self.calibration_axis.addItems(list('XYZA'))
        self.calibration_axis.currentIndexChanged.connect(self._calibration_axis_changed)
        form.addRow(_('Axis'), self.calibration_axis)
        self.calibration_current = FCLabel('')
        form.addRow(_('Applied steps/mm'), self.calibration_current)
        self.calibration_distance = self._spin(maximum=10)
        self.calibration_distance.setValue(1)
        self.calibration_distance.setToolTip(_('Distance the test move commands, at most 10 mm. '
                                               'It is also used in the steps/mm calculation.'))
        form.addRow(_('Requested travel (mm, maximum 10)'), self.calibration_distance)
        self.calibration_direction = FCComboBox(policy=False)
        self.calibration_direction.setToolTip(_('Direction of the test move.'))
        self.calibration_direction.addItems([_('Forward (+)'), _('Reverse (−)')])
        form.addRow(_('Direction'), self.calibration_direction)
        self.calibration_feed = self._spin()
        self.calibration_feed.setValue(60)
        self.calibration_feed.setToolTip(_('Feed speed of the test move in mm/min.'))
        form.addRow(_('Test speed (mm/min)'), self.calibration_feed)
        self.calibration_move_button = FCButton(_('Move test distance'))
        self.calibration_move_button.setToolTip(_(
            'MOVES THE MACHINE: the selected axis moves by the requested travel with the applied settings. '
            'Measure the real travel afterwards. Disabled while there are unapplied changes.'))
        self.calibration_move_button.clicked.connect(self._request_calibration)
        form.addRow(self.calibration_move_button)
        self.calibration_warning = FCLabel('')
        self.calibration_warning.setWordWrap(True)
        form.addRow(self.calibration_warning)
        self.calibration_measured = self._spin()
        self.calibration_measured.setValue(1)
        self.calibration_measured.setToolTip(_('Distance the axis really moved in the test move, '
                                               'measured with a ruler or gauge, in mm.'))
        form.addRow(_('Measured travel (mm)'), self.calibration_measured)
        for widget in (self.calibration_distance, self.calibration_measured):
            widget.valueChanged.connect(self._invalidate_calibration)
        formula = FCLabel(_('Corrected steps/mm = applied steps/mm × requested travel ÷ measured travel'))
        formula.setWordWrap(True)
        form.addRow(formula)
        self.calibration_calculate_button = FCButton(_('Calculate steps/mm'))
        self.calibration_calculate_button.setToolTip(_('Calculates the corrected steps/mm from the requested and '
                                                       'measured travel. It does not move the machine and '
                                                       'saves nothing.'))
        self.calibration_calculate_button.clicked.connect(self._calculate_calibration)
        form.addRow(self.calibration_calculate_button)
        self.calibration_result = FCLabel('')
        self.calibration_result.setWordWrap(True)
        form.addRow(self.calibration_result)
        self.calibration_use_button = FCButton(_('Use calculated value'))
        self.calibration_use_button.setToolTip(_('Copies the calculated whole steps/mm into the Motion tab. '
                                                 'Nothing is saved until you click Apply.'))
        self.calibration_use_button.clicked.connect(self._use_calibration)
        form.addRow(self.calibration_use_button)
        layout.addStretch()

    def load_settings(self, snapshot):
        snapshot = deepcopy(snapshot)
        motors = snapshot.get('motors', [MOTOR_DEFAULTS.copy() for _ in range(4)])
        if len(snapshot['axes']) != 4 or len(motors) != 4:
            raise ValueError(_('Settings must contain four axes and four motors.'))
        for settings, motor in zip(snapshot['axes'], motors):
            settings['steps_per_mm'] = _whole(settings['steps_per_mm'], _('Steps/mm'))
            for key, label in (('steps_per_revolution', _('Full steps per motor revolution')),
                               ('microsteps', _('Microsteps'))):
                motor[key] = _whole(motor.get(key, MOTOR_DEFAULTS[key]), label)
        self._loading = True
        self._snapshot = snapshot
        try:
            for axis, fields in enumerate(self.motion_fields):
                settings = snapshot['axes'][axis]
                for key, widget in fields.items():
                    if key == 'follow_acceleration':
                        widget.setChecked(settings['deceleration_mm_s2'] is None)
                    elif key == 'soft_limits_enabled':
                        widget.setChecked(settings[key])
                    elif key == 'backlash_compensation_enabled':
                        widget.setChecked(settings.get(key, False))
                    elif key == 'backlash_mm':
                        widget.setValue(settings.get(key, 0.0))
                    else:
                        value = settings[key]
                        if key == 'max_velocity_mm_s':
                            value *= 60
                        elif value is None:
                            value = settings['acceleration_mm_s2']
                        widget.setValue(value)
                fields['deceleration_mm_s2'].setEnabled(not fields['follow_acceleration'].isChecked())
                for key, widget in self.motor_fields[axis].items():
                    widget.setValue(motors[axis].get(key, MOTOR_DEFAULTS[key]))
                self._invalidate_motor(axis)
            factor = snapshot.get('interpolation_factor', 1)
            self.interpolation_label.setText(
                _('Board interpolation: %s (read only). It is not a driver microstep setting '
                  'and is not used in the steps/mm calculation.') % factor)
            self.soft_limit_tolerance.setValue(
                snapshot.get('hardware', {}).get('soft_limit_tolerance_mm', 0.05))
            if 'hardware' in snapshot:
                if self.hardware_tabs is None:
                    from .SenderEcutIO import EcutHardwareTabs
                    self.hardware_tabs = EcutHardwareTabs(snapshot['hardware'], self)
                    for title, page in self.hardware_tabs.pages:
                        self.tabs.addTab(page, title)
                        for button in page.findChildren(QtWidgets.QPushButton):
                            button.setAutoDefault(False)
                            button.setDefault(False)
                    self.hardware_tabs.changed.connect(self._changed)
                    self.hardware_tabs.output_requested.connect(self.output_requested.emit)
                else:
                    self.hardware_tabs.load_settings(snapshot['hardware'])
            self._baseline = self.values()
            self.status_label.setText('')
            self._calibration_axis_changed()
        finally:
            self._loading = False
        self.set_busy(False)

    def values(self):
        axes = []
        motors = []
        for axis, fields in enumerate(self.motion_fields):
            values = {key: widget.value() for key, widget in fields.items()
                      if key not in ('follow_acceleration', 'soft_limits_enabled', 'backlash_compensation_enabled')}
            values['backlash_compensation_enabled'] = fields['backlash_compensation_enabled'].isChecked()
            values['max_velocity_mm_s'] /= 60
            if fields['follow_acceleration'].isChecked():
                values['deceleration_mm_s2'] = None
            values['soft_limits_enabled'] = fields['soft_limits_enabled'].isChecked()
            if values['soft_limits_enabled'] and values['soft_limit_negative_mm'] >= values['soft_limit_positive_mm']:
                raise ValueError(_('%s: minimum machine position must be below maximum.') % 'XYZA'[axis])
            axes.append(values)
            motors.append({key: widget.value() for key, widget in self.motor_fields[axis].items()})
        payload = dict(revision=self._snapshot['revision'], axes=axes, motors=motors)
        if self.hardware_tabs is not None:
            payload['hardware'] = self.hardware_tabs.values()
            payload['hardware']['soft_limit_tolerance_mm'] = self.soft_limit_tolerance.value()
        return payload

    def is_dirty(self):
        try:
            return self.values() != self._baseline
        except ValueError:
            return True

    def _changed(self, *args):
        if self._loading:
            return
        dirty = self.is_dirty()
        self.calibration_warning.setText(_('Apply pending changes before a test move.') if dirty else '')
        self.calibration_move_button.setEnabled(not dirty and not self._busy)

    def _candidate_text(self, candidate):
        return _('Calculated: %.12g steps/mm\nSupported whole value: %d steps/mm\nRounding error: %+.8g%%') % (
            candidate['exact'], candidate['supported'], candidate['error_percent'])

    def _invalidate_motor(self, axis):
        self._motor_candidates[axis] = None
        self.motor_use_buttons[axis].setEnabled(False)
        self.motor_result_labels[axis].setText('')

    def _calculate_motor(self, axis):
        if self._busy:
            return
        try:
            candidate = steps_candidate(motor_steps_per_mm(
                **{key: widget.value() for key, widget in self.motor_fields[axis].items()}))
        except ValueError as error:
            self.show_error(str(error))
            return
        self._motor_candidates[axis] = candidate
        self.motor_result_labels[axis].setText(self._candidate_text(candidate))
        self.motor_use_buttons[axis].setEnabled(True)

    def _use_motor(self, axis):
        candidate = self._motor_candidates[axis]
        if candidate and not self._busy:
            self.motion_fields[axis]['steps_per_mm'].setValue(candidate['supported'])
            self.status_label.setText(_('Calculated value is pending. Click Apply to save it.'))

    def _invalidate_calibration(self, *args):
        self._calibration_candidate = None
        self.calibration_use_button.setEnabled(False)
        self.calibration_result.setText('')

    def _calibration_axis_changed(self, *args):
        self._invalidate_calibration()
        if self._snapshot:
            axis = self.calibration_axis.currentIndex()
            self.calibration_current.setText(str(self._snapshot['axes'][axis]['steps_per_mm']))

    def _calculate_calibration(self):
        if self._busy:
            return
        axis = self.calibration_axis.currentIndex()
        try:
            candidate = steps_candidate(calibrated_steps_per_mm(
                self._snapshot['axes'][axis]['steps_per_mm'],
                self.calibration_distance.value(), self.calibration_measured.value()))
        except ValueError as error:
            self.show_error(str(error))
            return
        self._calibration_candidate = (axis, candidate)
        self.calibration_result.setText(self._candidate_text(candidate))
        self.calibration_use_button.setEnabled(True)

    def _use_calibration(self):
        if self._calibration_candidate and not self._busy:
            axis, candidate = self._calibration_candidate
            self.motion_fields[axis]['steps_per_mm'].setValue(candidate['supported'])
            self.status_label.setText(_('Calculated value is pending. Click Apply to save it.'))

    def _request_apply(self):
        if self._busy:
            return
        try:
            payload = self.values()
        except ValueError as error:
            self.show_error(str(error))
            return
        if any(values['backlash_mm'] != old.get('backlash_mm', 0.0)
               or values['backlash_compensation_enabled'] != old.get('backlash_compensation_enabled', False)
               for values, old in zip(payload['axes'], self._snapshot['axes'])):
            QtWidgets.QMessageBox.information(self, _('Backlash'), _('Backlash changed: set the machine home again.'))
        self.set_busy(True)
        self.status_label.setText(_('Applying settings…'))
        self.apply_requested.emit(payload)

    def _request_calibration(self):
        if self._busy:
            return
        if self.is_dirty():
            self.show_error(_('Apply pending changes before a test move.'))
            return
        try:
            distance = _positive(self.calibration_distance.value(), _('Requested travel'))
            feed = _positive(self.calibration_feed.value(), _('Test speed'))
            if distance > 10:
                raise ValueError(_('Test travel cannot exceed 10 mm.'))
        except ValueError as error:
            self.show_error(str(error))
            return
        if self.calibration_direction.currentIndex() == 1:
            distance = -distance
        self.set_busy(True)
        self.status_label.setText(_('Moving test distance…'))
        self.calibration_requested.emit(self.calibration_axis.currentIndex(), distance, feed,
                                        self._snapshot['revision'])

    def set_busy(self, busy):
        self._busy = bool(busy)
        if not busy:
            self.set_calibration_running(False)
        self.tabs.setEnabled(not busy)
        self.apply_button.setEnabled(not busy)
        self._update_home()
        self.cancel_button.setEnabled(not busy)
        if self.hardware_tabs is not None:
            self.hardware_tabs.set_busy(busy)
        self._changed()

    def set_calibration_running(self, running):
        self._calibration_running = bool(running) and self._busy
        self.calibration_stop_button.setEnabled(self._calibration_running)

    def _request_stop(self):
        if self._calibration_running:
            self.stop_requested.emit()

    def update_io(self, state):
        if self.hardware_tabs is not None:
            self.hardware_tabs.update_io(state)
        homed = state.get('homed_axes') if isinstance(state, dict) else None
        self._homed_axes = ({axis for axis in homed if axis in range(4)}
                            if isinstance(homed, (list, tuple, set)) else set())
        self._update_home()

    def show_error(self, message):
        self.set_busy(False)
        self.status_label.setText(str(message))

    def calibration_finished(self, result):
        self.set_busy(False)
        if isinstance(result, dict):
            text = result.get('error') or result.get('message') or _('Test move completed. Enter the measured travel.')
        else:
            text = str(result) if result else _('Test move completed. Enter the measured travel.')
        self.status_label.setText(str(text))

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
