# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtCore

from appGUI import shortcuts

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

CONTEXT = 'gcode_sender'


class KeyHold:
    """Stands in for a pressed jog button of a key jog: down until the key is released."""

    def __init__(self, window):
        self._window = window
        self._down = True

    def isDown(self):
        return self._down

    def window(self):
        return self._window

    def release(self):
        self._down = False


def _jog(direction):
    return lambda tool, pressed: tool.key_jog(direction, pressed)


def _pressed(method):
    """An action that runs on the press of its key only."""
    def run(tool, pressed):
        if pressed:
            method(tool)
    return run


def _hold_resume(tool):
    if tool.action_allowed('hold') is None:
        tool._on_hold_button()
    elif tool.action_allowed('resume') is None:
        tool._on_resume_button()


def _console_focus(tool):
    tool.ui.grbl_command_entry.setFocus()


def actions():
    """The keys of the G-code Sender (context 'gcode_sender'); run(tool, pressed) is called by dispatch()."""
    def sc(aid, name, default, run):
        return shortcuts.Shortcut('sender.%s' % aid, '%s: %s' % (_("Sender"), name), CONTEXT, default, run)

    return [
        sc('jog_x_plus', _("Jog X+"), 'Right', _jog('xplus')),
        sc('jog_x_minus', _("Jog X-"), 'Left', _jog('xminus')),
        sc('jog_y_plus', _("Jog Y+"), 'Up', _jog('yplus')),
        sc('jog_y_minus', _("Jog Y-"), 'Down', _jog('yminus')),
        sc('jog_z_plus', _("Jog Z+"), 'PgUp', _jog('zplus')),
        sc('jog_z_minus', _("Jog Z-"), 'PgDown', _jog('zminus')),
        sc('jog_a_plus', _("Jog A+"), '.', _jog('aplus')),
        sc('jog_a_minus', _("Jog A-"), ',', _jog('aminus')),
        sc('hold_resume', _("Hold / Resume"), 'Space', _pressed(_hold_resume)),
        sc('stop', _("Stop"), 'Esc', _pressed(lambda tool: tool.on_stream_stop())),
        sc('jog_block', _("Show or hide the jog block"), 'Tab', _pressed(lambda tool: tool.toggle_jog_page())),
        sc('start', _("Start"), '', _pressed(lambda tool: tool.on_start())),
        sc('next_block', _("Next block"), '', _pressed(lambda tool: tool.on_next_block())),
        sc('home', _("Home"), '', _pressed(lambda tool: tool.on_grbl_homing())),
        sc('zero_x', _("Zero X"), '', _pressed(lambda tool: tool.on_grbl_zero('x'))),
        sc('zero_y', _("Zero Y"), '', _pressed(lambda tool: tool.on_grbl_zero('y'))),
        sc('zero_z', _("Zero Z"), '', _pressed(lambda tool: tool.on_grbl_zero('z'))),
        sc('zero_all', _("Zero all axes"), '', _pressed(lambda tool: tool.on_grbl_zero('all'))),
        sc('console_focus', _("Focus the command line"), '', _pressed(_console_focus)),
        sc('step_next', _("Next jog step"), ']', _pressed(lambda tool: tool.next_jog_preset())),
        sc('step_prev', _("Previous jog step"), '[', _pressed(lambda tool: tool.previous_jog_preset())),
    ] + [sc('preset_%d' % number, _("Jog step %d") % number, str(number),
            _pressed(lambda tool, index=number - 1: tool.set_jog_preset(index))) for number in range(1, 5)]


def dispatch(tool, event):
    """
    Run the Sender action of a key event; True when the event is consumed. A release runs the action of its press
    (also when the modifiers changed), so that a held jog always ends.
    """
    kind = event.type()
    if kind not in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease) or event.isAutoRepeat():
        return False
    down = tool.__dict__.setdefault('_sender_keys_down', {})
    key = event.key()
    if kind == QtCore.QEvent.Type.KeyRelease:
        action = down.pop(key, None)
        if action is None:
            return False
        action.run(tool, False)
        return True
    combination = shortcuts.combination_text(key, event.modifiers(), True)
    if combination is None:
        return False
    found = shortcuts.registry().match(CONTEXT, combination, shortcuts.SOURCE_ANY)
    if not found:
        return False
    down[key] = found[0]
    found[0].run(tool, True)
    return True


def release_all(tool):
    """The window lost the focus: every held key ends (no key release will come)."""
    down = tool.__dict__.get('_sender_keys_down', {})
    for key in list(down):
        down.pop(key).run(tool, False)
