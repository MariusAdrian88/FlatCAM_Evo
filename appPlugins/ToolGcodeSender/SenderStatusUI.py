# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/25/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import numpy as np
from PyQt6 import QtWidgets, QtGui, QtCore

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


class _ValueLabel(QtWidgets.QLabel):
    """A Work value that takes a click."""
    clicked = QtCore.pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class DroPanel(QtWidgets.QFrame):
    """
    One row per axis (X, Y, Z and A when it exists): a large Work value and a smaller Machine value, and a
    header line with the state, the WCS and the units. compact=True: Work values only, no header.
    """
    dro_clicked = QtCore.pyqtSignal(str)

    LETTERS = 'XYZA'

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.compact = compact
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(16)
        font.setBold(True)
        small = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.setLayoutDirection(QtCore.Qt.LayoutDirection.LeftToRight)
        self.letters = {}
        self.work = {}
        self.machine = {}
        self.header = QtWidgets.QLabel()
        self.header.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.header.setVisible(not compact)
        if compact:
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            layout = QtWidgets.QGridLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setVerticalSpacing(0)
            layout.addWidget(self.header, 0, 0, 1, 3)
            self.setStyleSheet('DroPanel {background: rgba(255,255,255,200);} QLabel {color: #202020;}')
        for row, letter in enumerate(self.LETTERS, start=1):
            self.letters[letter] = QtWidgets.QLabel(letter)
            self.work[letter] = _ValueLabel()
            self.work[letter].setFont(font)
            self.work[letter].clicked.connect(lambda axis=letter: self.dro_clicked.emit(axis))
            self.machine[letter] = QtWidgets.QLabel()
            self.machine[letter].setFont(small)
            for label in (self.letters[letter], self.machine[letter]):
                label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            if compact:
                layout.addWidget(self.letters[letter])
                layout.addWidget(self.work[letter])
            else:
                layout.addWidget(self.letters[letter], row, 0)
                layout.addWidget(self.work[letter], row, 1)
                layout.addWidget(self.machine[letter], row, 2)
            self.machine[letter].setVisible(not compact)
        self.set_axes('XYZ')
        self.clear()

    def set_axes(self, letters):
        for letter in self.LETTERS:
            shown = letter in letters
            self.letters[letter].setVisible(shown)
            self.work[letter].setVisible(shown)
            self.machine[letter].setVisible(shown and not self.compact)
        if not self.compact:
            self.adjustSize()

    def set_values(self, work, machine, units, wcs=None, state=None, stale=False, not_homed=False):
        count = len(work) if work is not None else len(machine) if machine is not None else 3
        self.set_axes(self.LETTERS[:max(3, min(count, 4))])
        fmt = '%.4f' if str(units).lower() in ('in', 'inch') else '%.3f'
        for index, letter in enumerate(self.LETTERS):
            for labels, values in ((self.work, work), (self.machine, machine)):
                text = '---'
                if values is not None and index < len(values) and np.isfinite(values[index]):
                    text = fmt % values[index]
                if labels[letter].text() != text:
                    labels[letter].setText(text)
        parts = (state, wcs, str(units).lower() if units else None, _("not homed") if not_homed else None)
        header = ' · '.join(str(part) for part in parts if part)
        if self.header.text() != header:
            self.header.setText(header)
        self.set_stale(stale)
        if not self.compact:
            self.adjustSize()

    def set_stale(self, stale):
        style = 'QLabel {color: %s;}' % GREY if stale else ''
        for labels in (self.work, self.machine):
            for label in labels.values():
                if label.styleSheet() != style:
                    label.setStyleSheet(style)

    def clear(self):
        self.set_values(None, None, None)


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

        self.dro = DroPanel(compact=True)
        row.addWidget(self.dro)
        self.units_label = QtWidgets.QLabel()
        row.addWidget(self.units_label)
        self.wcs_label = QtWidgets.QLabel()
        row.addWidget(self.wcs_label)
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
        self.dro.set_values(pos, None, units)
        self.units_label.setText(str(units).lower() if pos is not None and units else '')

    def set_wcs(self, wcs):
        self.wcs_label.setText(wcs or '')

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
        self.set_wcs(None)
        self.set_alarm(None)
        self.set_recovery(None)
