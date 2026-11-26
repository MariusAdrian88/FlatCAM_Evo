# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/25/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtGui

from appGUI.GUIElements import FCButton

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

STATE_COLORS = {
    'Idle': '#2e8b57',
    'Run': '#1e6fd9', 'Jog': '#1e6fd9', 'Home': '#1e6fd9',
    'Hold': '#d98c1e', 'Door': '#d98c1e', 'Tool': '#d98c1e',
    'Alarm': '#c0392b',
}
GREY = '#808080'


class SenderStatusStrip(QtWidgets.QWidget):
    """Machine state badge, 3 position readouts and the alarm row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.state_label = QtWidgets.QLabel()
        row.addWidget(self.state_label)

        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(16)
        font.setBold(True)
        self.dro_x = QtWidgets.QLabel()
        self.dro_y = QtWidgets.QLabel()
        self.dro_z = QtWidgets.QLabel()
        for axis, dro in (('X', self.dro_x), ('Y', self.dro_y), ('Z', self.dro_z)):
            dro.setFont(font)
            letter = QtWidgets.QLabel(axis)
            row.addWidget(letter)
            row.addWidget(dro)
        self.units_label = QtWidgets.QLabel()
        row.addWidget(self.units_label)
        row.addStretch()
        layout.addLayout(row)

        self.alarm_frame = QtWidgets.QWidget()
        self.alarm_row = QtWidgets.QHBoxLayout(self.alarm_frame)
        self.alarm_row.setContentsMargins(0, 0, 0, 0)
        self.alarm_label = QtWidgets.QLabel()
        self.alarm_label.setWordWrap(True)
        self.alarm_label.setStyleSheet("QLabel {color: #c0392b; font-weight: bold;}")
        self.alarm_row.addWidget(self.alarm_label, 1)
        layout.addWidget(self.alarm_frame)
        self.alarm_frame.setVisible(False)

        # the banner of an aborted job: latched, it does not follow the state reports
        self.recovery_frame = QtWidgets.QWidget()
        recovery_row = QtWidgets.QHBoxLayout(self.recovery_frame)
        recovery_row.setContentsMargins(0, 0, 0, 0)
        self.recovery_label = QtWidgets.QLabel()
        self.recovery_label.setWordWrap(True)
        self.recovery_label.setStyleSheet("QLabel {color: #c0392b; font-weight: bold;}")
        recovery_row.addWidget(self.recovery_label, 1)
        self.recovery_button = FCButton(_("Reset (spindle off)"))
        recovery_row.addWidget(self.recovery_button)
        layout.addWidget(self.recovery_frame)
        self.recovery_frame.setVisible(False)
        self.clear()

    def set_state(self, state):
        if state is None:
            text, color = _("Not connected"), GREY
        else:
            text, color = state, STATE_COLORS.get(state.split(':')[0], GREY)
        self.state_label.setText(text)
        style = "QLabel {background-color: %s; color: white; font-weight: bold; padding: 2px 8px;}" % color
        if self.state_label.styleSheet() != style:
            self.state_label.setStyleSheet(style)

    def set_position(self, pos, units):
        if pos is None:
            for dro in (self.dro_x, self.dro_y, self.dro_z):
                dro.setText('---')
            self.units_label.setText('')
            return
        fmt = '%.4f' if str(units).lower() in ('in', 'inch') else '%.3f'
        for dro, value in zip((self.dro_x, self.dro_y, self.dro_z), pos):
            dro.setText(fmt % value)
        self.units_label.setText(str(units).lower() if units else '')

    def set_alarm(self, text):
        self.alarm_label.setText(text or '')
        self.alarm_frame.setVisible(bool(text))

    def set_recovery(self, text, button=True):
        """Show the latched recovery banner; None hides it. button=False: text only."""
        self.recovery_label.setText(text or '')
        self.recovery_button.setVisible(button)
        self.recovery_frame.setVisible(bool(text))

    def clear(self):
        self.set_state(None)
        self.set_position(None, None)
        self.set_alarm(None)
        self.set_recovery(None)
