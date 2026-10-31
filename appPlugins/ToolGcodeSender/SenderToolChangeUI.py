# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/31/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Panel of the calibrated manual tool changes (GRBL fixed or removable touchplate)."""

from PyQt6 import QtWidgets, QtCore

from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay
from appPlugins.ToolGcodeSender.ToolChange import (
    PLATE_NONE, PLATE_FIXED, PLATE_REMOVABLE, STATE_OFF, STATE_NO_REFERENCE, STATE_REFERENCE,
    STATE_MEASURED, STATE_NEEDS_REMOVAL, STATE_READY)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

_FIXED_INSTRUCTIONS = _(
    "Fixed touchplate:\n"
    "1. Install the reference tool and set the board Z datum as usual.\n"
    "2. Jog the spindle above the fixed plate.\n"
    "3. Press 'Calibrate fixed plate'.\n"
    "4. After a tool change, jog above the same plate and press 'Measure replacement'.\n"
    "5. Press 'Apply Z correction'.\n"
    "The machine must be homed and its position known before you start.")

_REMOVABLE_INSTRUCTIONS = _(
    "Removable touchplate:\n"
    "1. Place the plate on the PCB and jog the spindle above it.\n"
    "2. Press 'Measure with plate'.\n"
    "3. Press 'Apply Z zero'.\n"
    "4. Remove the plate and confirm.\n"
    "The machine must be homed and its position known before you start.")

_OFF_INSTRUCTIONS = _(
    "Calibrated tool-change measurement is disabled.\n"
    "Set 'Tool-change touchplate' to Fixed or Removable in Preferences to use it.")


class SenderToolChangeUI(QtWidgets.QWidget):
    calibrate_sig = QtCore.pyqtSignal()
    measure_sig = QtCore.pyqtSignal()
    apply_sig = QtCore.pyqtSignal()
    acknowledge_sig = QtCore.pyqtSignal()
    clear_sig = QtCore.pyqtSignal()
    stop_sig = QtCore.pyqtSignal()

    def __init__(self, app, parent=None):
        super(SenderToolChangeUI, self).__init__(parent=parent)
        self.app = app
        self.decimals = app.decimals
        self.mode = PLATE_NONE
        self.state = STATE_OFF
        self.connected = False
        self.busy = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        self.instructions_label = FCLabel(_OFF_INSTRUCTIONS)
        self.instructions_label.setWordWrap(True)
        layout.addWidget(self.instructions_label)

        self.reference_label = FCLabel('')
        self.reference_label.setWordWrap(True)
        layout.addWidget(self.reference_label)

        self.status_label = FCLabel('')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        frame = FCFrame()
        layout.addWidget(frame)
        grid = GLay(v_spacing=5, h_spacing=3)
        frame.setLayout(grid)

        self.calibrate_button = FCButton(_("Calibrate fixed plate"))
        self.calibrate_button.setToolTip(
            _("Probe the fixed plate with the reference tool and record its position."))
        self.calibrate_button.clicked.connect(self.calibrate_sig)

        self.measure_button = FCButton(_("Measure replacement"))
        self.measure_button.setToolTip(
            _("Probe the plate with the installed tool. The spindle must be above the plate."))
        self.measure_button.clicked.connect(self.measure_sig)

        self.apply_button = FCButton(_("Apply Z correction"))
        self.apply_button.setToolTip(
            _("Set the active work offset Z from the verified measurement. X and Y are kept."))
        self.apply_button.clicked.connect(self.apply_sig)

        self.acknowledge_button = FCButton(_("Plate removed"))
        self.acknowledge_button.setToolTip(
            _("Confirm that the removable plate is off the PCB before cutting."))
        self.acknowledge_button.clicked.connect(self.acknowledge_sig)

        self.clear_button = FCButton(_("Forget reference"))
        self.clear_button.setToolTip(_("Discard the fixed plate reference of this session."))
        self.clear_button.clicked.connect(self.clear_sig)

        self.stop_button = FCButton(_("Stop"))
        self.stop_button.setStyleSheet("QPushButton {background-color: red; color: white;}")
        self.stop_button.setToolTip(
            _("Stop the running GRBL operation now. Press Reset, then Home, before more motion."))
        self.stop_button.clicked.connect(self.stop_sig)

        grid.addWidget(self.calibrate_button, 0, 0, 1, 2)
        grid.addWidget(self.measure_button, 1, 0, 1, 2)
        grid.addWidget(self.apply_button, 2, 0, 1, 2)
        grid.addWidget(self.acknowledge_button, 3, 0, 1, 2)
        hl = QtWidgets.QHBoxLayout()
        hl.addWidget(self.clear_button)
        hl.addWidget(self.stop_button)
        grid.addLayout(hl, 4, 0, 1, 2)

        layout.addStretch(1)
        self.set_mode(PLATE_NONE)
        self._update_buttons()

    def set_connected(self, connected):
        self.connected = bool(connected)
        self._update_buttons()

    def set_busy(self, busy):
        self.busy = bool(busy)
        self._update_buttons()

    def set_mode(self, mode, settings_ok=True):
        self.mode = mode
        self.settings_ok = bool(settings_ok) and mode != PLATE_NONE
        if mode == PLATE_FIXED:
            self.instructions_label.setText(_FIXED_INSTRUCTIONS)
            self.measure_button.setText(_("Measure replacement"))
            self.apply_button.setText(_("Apply Z correction"))
        elif mode == PLATE_REMOVABLE:
            self.instructions_label.setText(_REMOVABLE_INSTRUCTIONS)
            self.measure_button.setText(_("Measure with plate"))
            self.apply_button.setText(_("Apply Z zero"))
        else:
            self.instructions_label.setText(_OFF_INSTRUCTIONS)
        self._update_buttons()

    def set_state(self, state, text=''):
        self.state = state
        if state == STATE_MEASURED:
            self.reference_label.setText(_("A measurement is ready. Apply it to change the work offset Z."))
        elif state == STATE_NEEDS_REMOVAL:
            self.reference_label.setText(
                _("The board Z zero was set. Remove the plate, then confirm that it is off the PCB."))
        elif state == STATE_READY:
            self.reference_label.setText(_("The board Z zero is set and the plate is removed."))
        elif state == STATE_REFERENCE:
            self.reference_label.setText(
                _("The fixed plate reference is valid for this session. Measure the installed tool."))
        elif state == STATE_NO_REFERENCE:
            if self.mode == PLATE_REMOVABLE:
                self.reference_label.setText(_("Place the plate on the PCB and measure the installed tool."))
            else:
                self.reference_label.setText(_("Calibrate the fixed plate with the reference tool first."))
        else:
            self.reference_label.setText('')
        self._update_buttons()
        self.set_message(text)

    def set_message(self, text='', error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet('color: red;' if error else 'color: green;')

    def _update_buttons(self):
        enabled = self.connected and self.settings_ok and not self.busy
        fixed = self.mode == PLATE_FIXED
        removable = self.mode == PLATE_REMOVABLE
        self.calibrate_button.setVisible(fixed)
        self.clear_button.setVisible(fixed)
        self.acknowledge_button.setVisible(removable)
        self.measure_button.setVisible(fixed or removable)
        self.apply_button.setVisible(fixed or removable)
        self.calibrate_button.setEnabled(enabled)
        self.measure_button.setEnabled(
            enabled and (self.state in (STATE_REFERENCE, STATE_MEASURED) if fixed else True))
        self.apply_button.setEnabled(enabled and self.state == STATE_MEASURED)
        self.acknowledge_button.setEnabled(self.connected and not self.busy and
                                           self.state == STATE_NEEDS_REMOVAL)
        self.clear_button.setEnabled(enabled)
        self.stop_button.setEnabled(self.connected)
