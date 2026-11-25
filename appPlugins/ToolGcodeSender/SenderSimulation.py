"""Virtual GRBL transport and the sender's simulation controls."""

import builtins
import math
import re
import threading
import time
from collections import deque
import gettext

from PyQt6 import QtCore, QtWidgets

from appGUI.GUIElements import FCButton, FCLabel
from appPlugins.Common.gcode_segment import _words

import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
_ = builtins.__dict__.get('_', gettext.gettext)


def speed_multiplier(value):
    if value <= 25:
        return 0.5 + value / 50.0
    if value <= 34:
        return value - 24
    return 10.0 + (value - 34) * 90.0 / 66.0


class VirtualGrbl:
    """Execute queued blocks in virtual time; never owns a physical serial port."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.commands = deque()
        self.replies = deque()
        self.probe_heights = deque()
        self.probing = False
        self.position = (0.0, 0.0, 0.0)
        self.scale = 1.0
        self.absolute = True
        self.motion = 0
        self.feed = 100.0
        # ponytail: fixed rapid rate, add machine acceleration modelling if timing needs it.
        self.rapid = 3000.0
        self.speed = 1.0
        self.paused = False
        self.active = None
        self.last_time = clock()

    def set_speed(self, value):
        with self.lock:
            self.advance()
            self.speed = speed_multiplier(value)

    def set_paused(self, paused):
        with self.lock:
            self.advance()
            self.paused = paused

    def stop(self):
        self.cancel.set()

    def write(self, data):
        with self.lock:
            if self.cancel.is_set():
                return 'cancelled'
            self.advance()
            if data == b'?':
                state = 'Hold:0' if self.paused else ('Run' if self.active or self.commands else 'Idle')
                self.replies.append(('<%s|MPos:%.6f,%.6f,%.6f|WCO:0,0,0>\n' %
                                     (state, *self.position)).encode())
            else:
                self.commands.extend(data.decode('utf-8').splitlines())
            return 'ok'

    def _begin(self, line):
        masked = re.sub(r'\([^)]*\)', '', line).split(';', 1)[0].strip().upper()
        words = _words(masked)
        values = dict(words)
        codes = [value for letter, value in words if letter == 'G']
        supported = (0, 1, 2, 3, 4, 17, 20, 21, 40, 49, 54, 80, 90, 91, 91.1, 94)
        if self.probe_heights:
            supported += (38.2,)
        if (any(code not in supported for code in codes) or
                any(not math.isfinite(value) for _, value in words) or masked.startswith('$')):
            raise ValueError('Unsupported virtual controller command')
        if 20 in codes:
            self.scale = 25.4
        if 21 in codes:
            self.scale = 1.0
        if 90 in codes:
            self.absolute = True
        if 91 in codes:
            self.absolute = False
        for code in codes:
            if code in (0, 1, 2, 3):
                self.motion = int(code)
        if 'F' in values:
            self.feed = values['F'] * self.scale
        start = self.position
        end = tuple((values[axis] * self.scale + (0 if self.absolute else start[i]))
                    if axis in values else start[i] for i, axis in enumerate('XYZ'))
        self.probing = 38.2 in codes
        if self.probing:
            height = self.probe_heights.popleft()
            if (not math.isfinite(height) or not end[2] <= height <= start[2] or
                    end[:2] != start[:2]):
                raise ValueError('Invalid virtual probe contact')
            end = (start[0], start[1], height)
            self.motion = 1
        seconds = 0.0
        arc = None
        if 4 in codes:
            seconds = max(0.0, values.get('P', 0.0))
            end = start
        elif any(axis in values for axis in 'XYZ') or (self.motion in (2, 3) and
                                                       any(axis in values for axis in 'IJR')):
            distance = math.dist(start, end)
            if self.motion in (2, 3):
                clockwise = self.motion == 2
                if 'R' in values:
                    radius = abs(values['R'] * self.scale)
                    dx, dy = end[0] - start[0], end[1] - start[1]
                    chord = math.hypot(dx, dy)
                    if not chord or radius < chord / 2:
                        raise ValueError('Invalid arc')
                    height = math.sqrt(max(0.0, radius * radius - chord * chord / 4))
                    sign = (-1 if clockwise else 1) * (-1 if values['R'] < 0 else 1)
                    center = ((start[0] + end[0]) / 2 - sign * dy * height / chord,
                              (start[1] + end[1]) / 2 + sign * dx * height / chord)
                else:
                    center = (start[0] + values.get('I', 0) * self.scale,
                              start[1] + values.get('J', 0) * self.scale)
                    radius = math.hypot(start[0] - center[0], start[1] - center[1])
                if not radius or not math.isclose(math.hypot(end[0] - center[0], end[1] - center[1]),
                                                 radius, abs_tol=0.01):
                    raise ValueError('Invalid arc')
                angle = math.atan2(start[1] - center[1], start[0] - center[0])
                finish = math.atan2(end[1] - center[1], end[0] - center[0])
                sweep = (angle - finish if clockwise else finish - angle) % math.tau or math.tau
                sweep *= -1 if clockwise else 1
                arc = (center, radius, angle, sweep)
                distance = math.hypot(radius * sweep, end[2] - start[2])
            rate = self.rapid if self.motion == 0 else self.feed
            if rate <= 0:
                raise ValueError('Invalid feed')
            seconds = distance * 60.0 / rate
        self.active = [start, end, seconds, 0.0, arc]

    def advance(self):
        with self.lock:
            now = self.clock()
            remaining = max(0.0, now - self.last_time) * self.speed
            self.last_time = now
            if self.paused or self.cancel.is_set():
                return
            while self.active is not None or self.commands:
                if self.active is None:
                    try:
                        self._begin(self.commands.popleft())
                    except (ValueError, OverflowError):
                        self.replies.append(b'error:20\n')
                        continue
                start, end, duration, elapsed, arc = self.active
                step = min(remaining, max(0.0, duration - elapsed))
                elapsed += step
                remaining -= step
                fraction = min(1.0, elapsed / duration) if duration else 1.0
                self.position = tuple(a + (b - a) * fraction for a, b in zip(start, end))
                if arc is not None and fraction < 1:
                    center, radius, angle, sweep = arc
                    angle += sweep * fraction
                    self.position = (center[0] + radius * math.cos(angle),
                                     center[1] + radius * math.sin(angle), self.position[2])
                self.active[3] = elapsed
                if fraction < 1:
                    break
                self.active = None
                if self.probing:
                    self.replies.append(('[PRB:%.6f,%.6f,%.6f:1]\n' % self.position).encode())
                self.replies.append(b'ok\n')

    def readline(self):
        with self.lock:
            if self.cancel.is_set():
                return b''
            self.advance()
            if self.replies:
                return self.replies.popleft()
        self.cancel.wait(0.005)
        with self.lock:
            if self.cancel.is_set():
                return b''
            self.advance()
            return self.replies.popleft() if self.replies else b''


class SimulationDialog(QtWidgets.QDialog):
    def __init__(self, tool):
        super().__init__(tool)
        self.setWindowTitle(_("G-code simulation"))
        self.setModal(False)
        self.controller = None
        self.running = False
        self.closing = False
        layout = QtWidgets.QVBoxLayout(self)
        self.speed_label = FCLabel(_("Speed") + ': 1.00×')
        layout.addWidget(self.speed_label)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(25)
        self.slider.setToolTip(_("Simulation speed from 0.5× to 100× of real time. "
                                 "It can be changed while the simulation runs."))
        speed_layout = QtWidgets.QHBoxLayout()
        speed_layout.addWidget(self.slider, 1)
        self.reset_speed_button = QtWidgets.QToolButton()
        self.reset_speed_button.setText(_("Reset"))
        self.reset_speed_button.setToolTip(_("Reset simulation speed to 1×."))
        self.reset_speed_button.setAutoRaise(True)
        self.reset_speed_button.clicked.connect(lambda: self.slider.setValue(25))
        speed_layout.addWidget(self.reset_speed_button)
        layout.addLayout(speed_layout)
        buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(buttons)
        self.start_button = FCButton(_("Start"))
        self.stop_button = FCButton(_("Stop"))
        self.pause_button = FCButton(_("Pause"))
        self.close_button = FCButton(_("Close"))
        for button in (self.start_button, self.stop_button, self.pause_button, self.close_button):
            buttons.addWidget(button)
        self.start_button.setToolTip(_("Runs the loaded G-code on a virtual controller. "
                                       "No machine moves and nothing is sent to a serial port."))
        self.stop_button.setToolTip(_("Stops the running simulation."))
        self.pause_button.setToolTip(_("Pauses the simulation; click again to resume."))
        self.close_button.setToolTip(_("Stops the simulation and closes this window."))
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.slider.valueChanged.connect(self.change_speed)
        self.start_button.clicked.connect(tool._start_simulation)
        self.stop_button.clicked.connect(self.stop)
        self.pause_button.clicked.connect(self.pause_resume)
        self.close_button.clicked.connect(self.close)

    def change_speed(self, value):
        self.speed_label.setText(_("Speed") + ': %.2f×' % speed_multiplier(value))
        if self.controller is not None:
            self.controller.set_speed(value)

    def stop(self):
        if self.controller is not None:
            self.controller.stop()
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)

    def pause_resume(self):
        if self.controller is not None:
            self.controller.set_paused(not self.controller.paused)
            self.pause_button.setText(_("Resume") if self.controller.paused else _("Pause"))

    def closeEvent(self, event):
        self.closing = True
        self.stop()
        self.parent()._close_simulation()
        super().closeEvent(event)

    def reject(self):
        self.close()
