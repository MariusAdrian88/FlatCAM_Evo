# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

ON_COLOR = '#27ae60'
OFF_COLOR = '#c0392b'
UNKNOWN_COLOR = '#808080'
SIZE = 24
SPACING = 4


class IOIndicator(QtWidgets.QToolButton):
    """One IO signal: a small rounded square. Green = On, red = Off, gray = unknown (the reason is in the tooltip).
    An input does not react to clicks."""

    def __init__(self, name, label, tooltip, interactive=False, parent=None):
        super().__init__(parent)
        self.name = name
        self.base_tooltip = tooltip
        self.interactive = interactive
        self.state = None
        self.reason = ''
        self.source = ''
        self.pending = False
        self.note = ''
        self.setText(label)
        self.setFixedSize(SIZE, SIZE)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._refresh()

    def mousePressEvent(self, event):
        if self.interactive:
            super().mousePressEvent(event)
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if self.interactive:
            super().mouseReleaseEvent(event)
        else:
            event.ignore()

    def set_state(self, state, reason='', source=''):
        self.state = state
        self.reason = reason
        self.source = source
        self._refresh()

    def set_pending(self, pending):
        self.pending = bool(pending)
        self._refresh()

    def set_note(self, note):
        """An extra tooltip line (why the output is disabled)."""
        self.note = note
        self._refresh()

    def _refresh(self):
        color = ON_COLOR if self.state is True else OFF_COLOR if self.state is False else UNKNOWN_COLOR
        border = 'dashed' if self.pending else 'solid'
        self.setStyleSheet(
            'QToolButton {background-color: %s; color: white; border: 1px %s #202020; border-radius: 4px; '
            'font-size: 8pt; padding: 0px;}'
            'QToolButton:disabled {color: #dddddd;}' % (color, border))
        if self.state is True:
            status = _('On') + (' (%s)' % self.source if self.source else '')
        elif self.state is False:
            status = _('Off') + (' (%s)' % self.source if self.source else '')
        else:
            status = _('unknown') + (': %s' % self.reason if self.reason else '')
        lines = [self.base_tooltip, status]
        if self.pending:
            lines.append(_('Waiting for confirmation'))
        if self.note:
            lines.append(self.note)
        self.setToolTip('\n'.join(lines))


class _SignalGrid(QtWidgets.QGroupBox):
    """A group box with indicators in a grid; the column count follows the width."""

    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.indicators = []
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setSpacing(SPACING)
        self.grid.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        self._columns = 0

    def set_indicators(self, indicators):
        for indicator in self.indicators:
            self.grid.removeWidget(indicator)
            indicator.hide()
            indicator.setParent(None)
            indicator.deleteLater()
        self.indicators = list(indicators)
        for indicator in self.indicators:
            indicator.setParent(self)
        self._columns = 0
        self.reflow()
        self.setVisible(bool(self.indicators))

    def columns_for(self, width):
        margins = self.contentsMargins()
        avail = width - margins.left() - margins.right() - 2 * self.grid.contentsMargins().left()
        return max(1, (avail + SPACING) // (SIZE + SPACING))

    def reflow(self):
        columns = self.columns_for(self.width())
        if columns == self._columns:
            return
        self._columns = columns
        for indicator in self.indicators:
            self.grid.removeWidget(indicator)
        for i, indicator in enumerate(self.indicators):
            self.grid.addWidget(indicator, i // columns, i % columns)
            indicator.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()


class IOPage(QtWidgets.QWidget):
    """The IO tab: the inputs (read-only) and the outputs (a click requests the opposite state)."""

    output_requested = QtCore.pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._outputs_enabled = False
        self._outputs_reason = ''
        self._unsupported = set()
        self._blocked = {}
        layout =QtWidgets.QVBoxLayout(self)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.inputs_box = _SignalGrid(_('Inputs'))
        self.outputs_box = _SignalGrid(_('Outputs'))
        layout.addWidget(self.inputs_box)
        layout.addWidget(self.outputs_box)
        layout.addStretch(1)
        self.inputs = {}
        self.outputs = {}
        self.inputs_box.hide()
        self.outputs_box.hide()

    def set_signals(self, inputs, outputs):
        """inputs, outputs: lists of (name, label, tooltip). Every state goes back to unknown."""
        self._unsupported = set()
        self._blocked = {}
        self.inputs ={name: IOIndicator(name, label, tip) for name, label, tip in inputs}
        self.outputs = {name: IOIndicator(name, label, tip, interactive=True) for name, label, tip in outputs}
        for name, indicator in self.outputs.items():
            indicator.clicked.connect(lambda _checked=False, n=name: self._output_clicked(n))
        self.inputs_box.set_indicators(self.inputs.values())
        self.outputs_box.set_indicators(self.outputs.values())
        self._apply_enabled()

    def update(self, states):
        """states: name -> (True | False | None, reason, source)."""
        for name, value in states.items():
            indicator = self.inputs.get(name) or self.outputs.get(name)
            if indicator is not None:
                state, reason, source = value
                indicator.set_state(state, reason, source)

    def set_outputs_enabled(self, enabled, reason=''):
        self._outputs_enabled = bool(enabled)
        self._outputs_reason = reason
        self._apply_enabled()

    def set_output_supported(self, name, supported):
        if supported:
            self._unsupported.discard(name)
        else:
            self._unsupported.add(name)
        self._apply_enabled()

    def set_output_blocked(self, name, reason=''):
        """Block one output with a reason (empty reason: unblock). It combines with the global enable."""
        if reason:
            self._blocked[name] = reason
        else:
            self._blocked.pop(name, None)
        self._apply_enabled()

    def set_pending(self, name, pending):
        indicator = self.outputs.get(name) or self.inputs.get(name)
        if indicator is not None:
            indicator.set_pending(pending)

    def set_message(self, text):
        self.message.setText(text or '')
        self.message.setVisible(bool(text))

    def _apply_enabled(self):
        for name, indicator in self.outputs.items():
            unsupported = name in self._unsupported
            blocked = self._blocked.get(name, '')
            indicator.setEnabled(self._outputs_enabled and not unsupported and not blocked)
            if unsupported:
                indicator.set_note(_('Not supported by the controller'))
            elif blocked:
                indicator.set_note(blocked)
            elif not self._outputs_enabled:
                indicator.set_note(self._outputs_reason)
            else:
                indicator.set_note('')

    def _output_clicked(self, name):
        indicator = self.outputs.get(name)
        if indicator is not None:
            self.output_requested.emit(name, indicator.state is not True)
