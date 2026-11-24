# ##########################################################
# FlatCAM Evo: E-Cut hardware configuration and live I/O   #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/20/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

"""Snapshot-only Qt pages; the sender worker owns all controller access."""
from copy import deepcopy
import gettext
import math

from PyQt6 import QtCore, QtWidgets

from appPlugins.Common.ecut.ecut_config import (INPUT_SIGNAL_NAMES, OUTPUT_SIGNAL_NAMES, OutputSignal,
                                                LABEL_MAX_LENGTH, NATIVE_INPUT_COUNT, NATIVE_OUTPUT_COUNT)

_ = gettext.gettext


def ecut_queue_text(state):
    """One status line from EcutSenderPort.get_queue_state(); only what the host knows is named."""
    if state['idle']:
        return _("Queue: idle")
    parts = []
    if state['lines_waiting']:
        parts.append(_("%d lines waiting") % state['lines_waiting'])
    if state['blocks_waiting']:
        parts.append(_("%d blocks waiting") % state['blocks_waiting'])
    if state['blocks_active']:
        parts.append(_("%d active") % state['blocks_active'])
    if state['records_unsent']:
        parts.append(_("%d records unsent") % state['records_unsent'])
    if state['program']:
        parts.append(_("Program running"))
    if state['board_queued']:
        parts.append(_("Board busy"))
    return _("Queue: %s") % (', '.join(parts) if parts else _("busy"))


class EcutHardwareTabs(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()
    output_requested = QtCore.pyqtSignal(int, bool)

    def __init__(self, hardware, parent=None):
        super().__init__(parent)
        self.hide()
        self.pages = []
        self.input_fields = {}
        self.output_fields = {}
        self.axis_fields = []
        self.spindle_fields = {}
        self.logical_leds = {}
        self.input_leds = []
        self.output_buttons = []
        self.input_label_fields = []
        self.output_label_fields = []
        self._hardware = {}
        self._edits = {}
        self._loading = True
        self._busy = False
        self._io = {}
        self._config_pages = []
        self._build_inputs(hardware)
        self._build_outputs(hardware)
        self._build_axes()
        self._build_spindle()
        self._build_live()
        self.load_settings(hardware)

    def _page(self, title, note, configurable=True):
        page = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(page)
        label = QtWidgets.QLabel(note)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.pages.append((title, page))
        if configurable:
            self._config_pages.append(page)
        return layout, label

    def _table(self, layout, headings):
        table = QtWidgets.QTableWidget(0, len(headings))
        table.setHorizontalHeaderLabels(headings)
        table.verticalHeader().hide()
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        return table

    def _check(self, path):
        widget = QtWidgets.QCheckBox()
        widget.toggled.connect(lambda value: self._edit(path, value))
        return widget

    def _spin(self, path, minimum, maximum, integer=True):
        widget = QtWidgets.QSpinBox() if integer else QtWidgets.QDoubleSpinBox()
        if not integer:
            widget.setDecimals(3)
        widget.setRange(minimum, maximum)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(lambda value: self._edit(path, value))
        return widget

    def _combo(self, path, options):
        widget = QtWidgets.QComboBox()
        for text, value in options:
            widget.addItem(text, value)
        widget.currentIndexChanged.connect(lambda index: self._edit(path, widget.itemData(index)))
        return widget

    def _pins(self, path, prefix, count):
        return self._combo(path, [(f'{prefix}{pin}', pin) for pin in range(count)])

    def _label_grid(self, layout, key, prefix, count, columns, title):
        """One name editor per pin; the names are saved in the PC profile."""
        group = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(group)
        fields = []
        for pin in range(count):
            field = QtWidgets.QLineEdit()
            field.setMaxLength(LABEL_MAX_LENGTH)
            field.setPlaceholderText(_('Name'))
            field.setToolTip(_('Free name of this pin (at most %d characters). It is only shown in Live I/O.')
                             % LABEL_MAX_LENGTH)
            field.textChanged.connect(lambda text, path=(key, pin): self._edit(path, self._clean_label(text)))
            row, column = divmod(pin, columns)
            grid.addWidget(QtWidgets.QLabel('%s%d' % (prefix, pin)), row, column * 2)
            grid.addWidget(field, row, column * 2 + 1)
            fields.append(field)
        layout.addWidget(group)
        return fields

    @staticmethod
    def _clean_label(text):
        return ''.join(char for char in text if char.isprintable()).strip()[:LABEL_MAX_LENGTH]

    def _label(self, key, pin):
        """Name of a pin: pending edit first, then the saved profile name."""
        if (key, pin) in self._edits:
            return self._edits[(key, pin)]
        labels = self._hardware.get(key)
        if isinstance(labels, list) and pin < len(labels) and isinstance(labels[pin], str):
            return labels[pin]
        return ''

    def _indicator(self):
        widget = QtWidgets.QCheckBox()
        widget.setEnabled(False)
        return widget

    def _build_inputs(self, hardware):
        layout, _label = self._page(_('Input signals'), _(
            'SH9001 inputs I0–I15: raw 1 means closed to Vo12V. Active Low treats raw 0 as active; '
            'use it for a normally closed switch that opens on a limit or broken wire. '
            'Home and limits may share an input. Logical states use applied settings. '
            'Homing and probing cycles remain unavailable.'))
        self.legacy_estop_note = QtWidgets.QLabel()
        self.legacy_estop_note.setWordWrap(True)
        layout.addWidget(self.legacy_estop_note)
        table = self._table(layout, [_('Signal'), _('Enabled'), _('Input'), _('Active level'),
                                     _('Debounce ms'), _('Port'), _('Active (applied)')])
        names = list(dict.fromkeys((*INPUT_SIGNAL_NAMES, *hardware.get('inputs', {}))))
        for row, name in enumerate(names):
            table.insertRow(row)
            path = ('inputs', name)
            fields = dict(enabled=self._check((*path, 'enabled')),
                          pin=self._pins((*path, 'pin'), 'I', 16),
                          polarity=self._combo((*path, 'polarity'), [(_('Active Low'), 0),
                                                                   (_('Active High'), 1)]),
                          debounce_ms=self._spin((*path, 'debounce_ms'), 0, 10000))
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            for column, key in enumerate(('enabled', 'pin', 'polarity', 'debounce_ms'), 1):
                table.setCellWidget(row, column, fields[key])
            fields['port'] = QtWidgets.QLabel()
            table.setCellWidget(row, 5, fields['port'])
            self.logical_leds[name] = self._indicator()
            table.setCellWidget(row, 6, self.logical_leds[name])
            self.input_fields[name] = fields
        self.input_label_fields = self._label_grid(layout, 'input_labels', 'I', NATIVE_INPUT_COUNT, 4,
                                                   _('Input names (I0-I15)'))

    def _build_outputs(self, hardware):
        layout, _label = self._page(_('Output signals'), _(
            'Map logical outputs to SH9001 O0–O7. Active High means raw 1 switches the sinking output on; '
            'Active Low reverses the logical signal. ENABLE1–ENABLE6 are output assignments only. '
            'STEP/DIR aliases belong to controller profile pins and cannot be toggled as bitmap outputs.'))
        table = self._table(layout, [_('Signal'), _('Enabled'), _('Output'), _('Active level')])
        names = list(dict.fromkeys((*OUTPUT_SIGNAL_NAMES, *(f'ENABLE{i}' for i in range(1, 7)),
                                   *hardware.get('outputs', {}))))
        for row, name in enumerate(names):
            table.insertRow(row)
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            if name.startswith(('STEP_', 'DIR_')):
                table.setCellWidget(row, 1, QtWidgets.QLabel(_('Controller profile — see Motor pins')))
                continue
            path = ('outputs', name)
            fields = dict(enabled=self._check((*path, 'enabled')),
                          pin=self._pins((*path, 'pin'), 'O', 8),
                          inverted=self._combo((*path, 'inverted'), [(_('Active High'), False),
                                                                    (_('Active Low'), True)]))
            for column, key in enumerate(('enabled', 'pin', 'inverted'), 1):
                table.setCellWidget(row, column, fields[key])
            self.output_fields[name] = fields
        self.output_label_fields = self._label_grid(layout, 'output_labels', 'O', NATIVE_OUTPUT_COUNT, 2,
                                                    _('Output names (O0-O7)'))

    def _build_axes(self):
        layout, self.motor_note = self._page(_('Motor pins'), _(
            'X/Y/Z/A software axis enable controls whether an axis participates in motion. '
            'It does not switch electrical driver power. Microstep/current DIP switches are set manually. '
            'These STEP/DIR fields are controller profile pins, separate from O0–O7. '
            'Physical pin remapping has not been independently verified.'))
        table = self._table(layout, [_('Axis'), _('Software enabled'), _('Direction'),
                                     _('Step profile pin'), _('Dir profile pin'), _('Step port'),
                                     _('Dir port'), _('Step Active Low'), _('Dir Active Low')])
        for row, name in enumerate('XYZA'):
            table.insertRow(row)
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            path = ('axes', row)
            fields = dict(enabled=self._check((*path, 'enabled')),
                          direction=self._combo((*path, 'direction'), [(_('Forward'), 1), (_('Reverse'), -1)]),
                          step_pin=self._spin((*path, 'step_pin'), 0, 255),
                          dir_pin=self._spin((*path, 'dir_pin'), 0, 255),
                          step_port=self._spin((*path, 'step_port'), 0, 15),
                          dir_port=self._spin((*path, 'dir_port'), 0, 63),
                          step_active_low=self._check((*path, 'step_active_low')),
                          dir_active_low=self._check((*path, 'dir_active_low')))
            for column, key in enumerate(fields, 1):
                table.setCellWidget(row, column, fields[key])
            self.axis_fields.append(fields)

    def _build_spindle(self):
        layout, _label = self._page(_('Spindle / VFD'), _(
            'PWM/DAC controls the spindle speed output; map SPINDLE_CW/CCW on Output signals for run control. '
            'The VFD run input and analog speed input require their corresponding wiring. '
            'The recovered raw PWM period is editable; frequency conversion and step/direction drive are unverified.'))
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.spindle_fields['control'] = self._combo(('spindle', 'control'),
                                                    [(_('None'), 0), (_('PWM / DAC'), 1)])
        form.addRow(_('Control'), self.spindle_fields['control'])
        options = [('pwm_period_counts', _('PWM raw period counts'), 1, 65535, True),
                   ('speed_rpm_min', _('Minimum RPM'), 0, 1e9, False),
                   ('speed_rpm_max', _('Maximum RPM'), 0, 1e9, False),
                   ('dwell_on_seconds', _('Run dwell seconds'), 0, 1e6, False),
                   ('dwell_off_seconds', _('Stop dwell seconds'), 0, 1e6, False),
                   ('pulses_per_revolution', _('Feedback pulses/revolution (0 = uncalibrated)'),
                    0, 2147483647, True)]
        for key, text, minimum, maximum, integer in options:
            self.spindle_fields[key] = self._spin(('spindle', key), minimum, maximum, integer)
            form.addRow(text, self.spindle_fields[key])
        self.spindle_frequency = QtWidgets.QLabel()
        form.addRow(_('Frequency profile (fixed)'), self.spindle_frequency)
        layout.addStretch()

    def _build_live(self):
        layout, _label = self._page(_('Live I/O'), _(
            'Readback only: raw input 1 = switch closed to Vo12V; physical output On = raw 1, sinking. '
            'Clicking an output requests a change. Its On/Off state changes only after controller readback. '
            'Assigned SPINDLE or ENABLE outputs can start equipment when switched manually.'), configurable=False)
        self.io_status = QtWidgets.QLabel()
        layout.addWidget(self.io_status)
        grid = QtWidgets.QGridLayout()
        layout.addLayout(grid)
        for pin in range(16):
            indicator = self._indicator()
            grid.addWidget(indicator, pin // 4, pin % 4)
            self.input_leds.append(indicator)
        output_grid = QtWidgets.QGridLayout()
        layout.addLayout(output_grid)
        for pin in range(8):
            button = QtWidgets.QPushButton()
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(lambda _checked=False, bit=pin: self._request_output(bit))
            output_grid.addWidget(button, pin // 2, pin % 2)
            self.output_buttons.append(button)
        layout.addStretch()

    def _pending(self):
        result = deepcopy(self._hardware)
        for key, count in (('input_labels', NATIVE_INPUT_COUNT), ('output_labels', NATIVE_OUTPUT_COUNT)):
            labels = [label if isinstance(label, str) else '' for label in (result.get(key) or [])][:count]
            result[key] = labels + [''] * (count - len(labels))
        for path, value in self._edits.items():
            if path[0] in ('input_labels', 'output_labels'):
                result[path[0]][path[1]] = value
                continue
            if path[0] == 'outputs':
                result.setdefault('outputs', {}).setdefault(path[1], OutputSignal().to_dict())
            target = result
            for key in path[:-1]:
                if isinstance(target, list):
                    while len(target) <= key:
                        target.append({})
                    target = target[key]
                else:
                    target = target.setdefault(key, [] if key == 'axes' else {})
            target[path[-1]] = value
        return result

    def _edit(self, path, value):
        if self._loading or value is None:
            return
        self._edits[path] = value
        self._render_io()
        self.changed.emit()

    @staticmethod
    def _set_field(widget, value):
        blocker = QtCore.QSignalBlocker(widget)
        if isinstance(widget, QtWidgets.QComboBox):
            for index in range(widget.count() - 1, -1, -1):
                if widget.itemData(index, QtCore.Qt.ItemDataRole.UserRole + 1):
                    widget.removeItem(index)
            index = widget.findData(value)
            if index < 0:
                widget.addItem(_('Unverified: %s') % value, value)
                index = widget.count() - 1
                widget.setItemData(index, True, QtCore.Qt.ItemDataRole.UserRole + 1)
                widget.model().item(index).setEnabled(False)
            widget.setCurrentIndex(index)
        elif isinstance(widget, QtWidgets.QCheckBox):
            widget.setChecked(bool(value))
        else:
            widget.setValue(value)
        del blocker

    def load_settings(self, hardware):
        self._loading = True
        self._hardware = deepcopy(hardware)
        self._edits.clear()
        input_defaults = dict(enabled=False, pin=0, polarity=0, debounce_ms=20, port=1)
        output_defaults = dict(enabled=False, pin=0, inverted=False)
        axis_defaults = dict(enabled=False, direction=1, step_pin=0, dir_pin=0,
                             step_port=1, dir_port=1, step_active_low=False, dir_active_low=False)
        for name, fields in self.input_fields.items():
            signal = {**input_defaults, **hardware.get('inputs', {}).get(name, {})}
            for key, widget in fields.items():
                if key == 'port':
                    widget.setText(str(signal[key]))
                else:
                    self._set_field(widget, signal[key])
        for name, fields in self.output_fields.items():
            signal = {**output_defaults, **hardware.get('outputs', {}).get(name, {})}
            for key, widget in fields.items():
                self._set_field(widget, signal[key])
        axes = hardware.get('axes', [])
        for index, fields in enumerate(self.axis_fields):
            axis = {**axis_defaults, **(axes[index] if index < len(axes) else {})}
            for key, widget in fields.items():
                self._set_field(widget, axis[key])
        spindle = {**dict(control=0, pwm_period_counts=1000, speed_rpm_min=0, speed_rpm_max=24000,
                          dwell_on_seconds=0, dwell_off_seconds=0, pulses_per_revolution=0),
                   **hardware.get('spindle', {})}
        for key, widget in self.spindle_fields.items():
            self._set_field(widget, spindle[key])
        for key, fields in (('input_labels', self.input_label_fields), ('output_labels', self.output_label_fields)):
            labels = hardware.get(key)
            for pin, field in enumerate(fields):
                text = labels[pin] if isinstance(labels, list) and pin < len(labels) and isinstance(
                    labels[pin], str) else ''
                blocker = QtCore.QSignalBlocker(field)
                field.setText(text)
                del blocker
        self.spindle_frequency.setText('%s Hz' % hardware.get('spindle', {}).get('pwm_frequency_hz', 1000))
        safety = hardware.get('safety', {})
        self.legacy_estop_note.setText(_(
            'Legacy E-stop input I%s is also enabled. Its pin and active level must agree with the ESTOP row '
            'before applying settings.') % safety.get('estop_input_pin', 0)
            if safety.get('estop_input_enabled') else '')
        self._loading = False
        self._render_io()

    def values(self):
        result = self._pending()
        for name, signal in result.get('inputs', {}).items():
            if signal.get('enabled') and signal.get('pin', 0) not in range(16):
                raise ValueError(_('%s uses an unverified expansion input; disable it or select I0–I15.') % name)
        for name, signal in result.get('outputs', {}).items():
            if (not name.startswith(('STEP_', 'DIR_')) and signal.get('enabled')
                    and signal.get('pin', 0) not in range(8)):
                raise ValueError(_('%s uses an unverified output; disable it or select O0–O7.') % name)
        spindle = result.get('spindle', {})
        if spindle.get('control', 0) not in (0, 1):
            raise ValueError(_('Unverified spindle mode; select None or PWM / DAC.'))
        if not 0 <= spindle.get('speed_rpm_min', 0) < spindle.get('speed_rpm_max', 24000):
            raise ValueError(_('Minimum spindle RPM must be below maximum RPM.'))
        return result

    def set_busy(self, busy):
        self._busy = bool(busy)
        for page in self._config_pages:
            page.setEnabled(not self._busy)
        self._render_io()

    def update_io(self, state):
        self._io = dict(state) if isinstance(state, dict) else {}
        self._render_io()

    def io_idle(self):
        """True when readback is current and neither this page nor the board is busy."""
        return self._fresh() and not self._busy and not self._io.get('busy', False)

    def _fresh(self):
        try:
            age = float(self._io.get('age_s', math.inf))
            inputs = self._io.get('inputs')
            outputs = self._io.get('outputs')
            return (bool(self._io.get('connected')) and math.isfinite(age) and 0 <= age <= 0.5
                    and type(inputs) is int and 0 <= inputs <= 65535
                    and type(outputs) is int and 0 <= outputs <= 255)
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _light(widget, active, fresh):
        blocker = QtCore.QSignalBlocker(widget)
        widget.setChecked(bool(active))
        widget.setProperty('io_state', ('active' if active else 'inactive') if fresh else 'unknown')
        color = ('#25a244' if active else '#671c23') if fresh else '#808080'
        widget.setStyleSheet('QCheckBox::indicator {width: 14px; height: 14px; '
                            'border: 1px solid #555; border-radius: 7px; background: %s;}' % color)
        del blocker

    def _render_io(self):
        fresh = self._fresh()
        config = self._hardware
        inputs = self._io.get('inputs', 0) if fresh else 0
        outputs = self._io.get('outputs', 0) if fresh else 0
        idle = fresh and not self._busy and not self._io.get('busy', False)
        self.io_status.setText(_('Connected — readback current') if fresh else _('Disconnected or stale — I/O unknown'))
        for pin, widget in enumerate(self.input_leds):
            active = bool(inputs & (1 << pin))
            name = self._label('input_labels', pin)
            widget.setText('I%d%s — %s' % (pin, ' ' + name if name else '',
                                          ('1 / ' + _('closed') if active else '0 / ' + _('open'))
                                          if fresh else '?'))
            self._light(widget, active, fresh)
        for name, widget in self.logical_leds.items():
            signal = config.get('inputs', {}).get(name, {})
            pin = signal.get('pin', 0)
            enabled = signal.get('enabled', False) and type(pin) is int and 0 <= pin < 16
            raw = bool(inputs & (1 << pin)) if enabled else False
            active = enabled and raw == bool(signal.get('polarity', 0))
            self._light(widget, active, fresh and enabled)
            widget.setText(_('Active') if active and fresh else (_('Inactive') if fresh and enabled else '—'))
        for pin, button in enumerate(self.output_buttons):
            roles = [name for name, signal in config.get('outputs', {}).items()
                     if signal.get('enabled') and signal.get('pin') == pin
                     and not name.startswith(('STEP_', 'DIR_'))]
            active = bool(outputs & (1 << pin))
            blocker = QtCore.QSignalBlocker(button)
            button.setChecked(active)
            name = self._label('output_labels', pin)
            state = (_('On') if active else _('Off')) if fresh else '?'
            button.setText('O%d%s — %s%s' % (pin, ' ' + name if name else '', state,
                                          ' · ' + ', '.join(roles) if roles else ''))
            button.setEnabled(idle)
            button.setToolTip(_('Physical sinking output. Labels use applied settings. Readback confirms changes.'))
            del blocker

    def _request_output(self, pin):
        active = bool(self._io.get('outputs', 0) & (1 << pin)) if self._fresh() else False
        self._render_io()
        if self._fresh() and not self._busy and not self._io.get('busy', False):
            self.output_requested.emit(pin, not active)
