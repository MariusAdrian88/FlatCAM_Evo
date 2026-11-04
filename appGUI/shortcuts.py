# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# License:  MIT Licence                                    #
# ##########################################################
"""
The registry of the keyboard shortcuts: one table of actions (appGUI/shortcut_actions.py) with, for each action, an id,
the name that the user sees, the context where it works, the factory key sequence and the function that it runs.

MainGUI.keyPressEvent asks dispatch() for the action of a key instead of comparing keys, the F3 list and the key hints
of the menus are made from the table, and the Keyboard Shortcuts page of the Preferences changes the keys. The keys
that the user changed are kept in the option 'global_shortcuts' (action id -> key sequence, only the changed ones).

A key comes from 2 places: the plot area (a vispy key) and the Project tab (an int, a Qt key code). Some actions take
the key only from one of them (SOURCE_CANVAS, SOURCE_PROJECT): Del on the plot area deletes the object, Del in the
Project tab deletes it from the list, F2 renames only in the Project tab.

The key sequences are kept in Qt's portable text ('Ctrl+Shift+S', 'Esc', 'Del'); on macOS Qt shows Ctrl as Cmd.
"""

import json

from PyQt6 import QtCore, QtGui

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

Qt = QtCore.Qt

# where an action takes its key from
SOURCE_ANY = 'any'
SOURCE_CANVAS = 'canvas'        # the plot area (a vispy key)
SOURCE_PROJECT = 'project'      # the Project tab (a Qt key code)

# when an action runs in a context that passes keys to the active tool of an editor
PHASE_MAIN = 'main'     # first, always
PHASE_TOOL = 'tool'     # when no tool of the editor is active (else the key goes to the active tool)
PHASE_LATE = 'late'     # last, also after the active tool got the key

class Stop:
    """
    What an action returns when the key is used up: the next phases of the context do not get it, and keyPressEvent
    returns the value. Any other return value of an action is ignored (the key goes on).
    """

    def __init__(self, value=None):
        self.value = value


STOP = Stop()

# the key sequences of the editor and of the files: Qt's portable text
PORTABLE = QtGui.QKeySequence.SequenceFormat.PortableText
NATIVE = QtGui.QKeySequence.SequenceFormat.NativeText

_MODIFIER_MASK = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier |
                  Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
_MODIFIER_KEYS = (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr)

# the vispy names of the keys that are not a character (vispy.util.keys)
_VISPY_NAMES = {
    'Escape': Qt.Key.Key_Escape, 'Delete': Qt.Key.Key_Delete, 'Space': Qt.Key.Key_Space,
    'Enter': Qt.Key.Key_Return, 'Backspace': Qt.Key.Key_Backspace, 'Tab': Qt.Key.Key_Tab,
    'Up': Qt.Key.Key_Up, 'Down': Qt.Key.Key_Down, 'Left': Qt.Key.Key_Left, 'Right': Qt.Key.Key_Right,
    'PageUp': Qt.Key.Key_PageUp, 'PageDown': Qt.Key.Key_PageDown, 'Insert': Qt.Key.Key_Insert,
    'Home': Qt.Key.Key_Home, 'End': Qt.Key.Key_End,
}
_VISPY_NAMES.update({'F%d' % i: getattr(Qt.Key, 'Key_F%d' % i) for i in range(1, 13)})


class Context:
    """
    A context: the state of the application in which a set of keys works (MainGUI.keyPressEvent picks it by
    app.call_source). A context that can pass keys to the active tool of an editor has tool_active() and pass_to_tool().
    """

    def __init__(self, cid, name, keypad_plain=False, tool_editor=None, select_button=None, passthrough=(),
                 editable=True, text=False):
        self.id = cid
        self.name = name
        self.keypad_plain = keypad_plain        # a key of the number pad is the same key without a modifier
        self.tool_editor = tool_editor          # the editor attribute of the app whose active tool gets keys
        self.select_button = select_button      # the MainGUI button that is checked when no tool is active
        self.passthrough = passthrough          # the modifiers ('' or 'Ctrl') with which the active tool gets a key
        self.editable = editable                # False: the keys are listed only (handled by code of their own)
        self.text = text                        # a text field: a key of 'global' does not reach it

    def tool_active(self, ui):
        editor = getattr(ui.app, self.tool_editor)
        return editor.active_tool is not None and getattr(ui, self.select_button).isChecked() is False

    def pass_to_tool(self, ui, key):
        response = getattr(ui.app, self.tool_editor).active_tool.on_key(key=key)
        if response is not None:
            ui.app.inform.emit(response)


class Shortcut:
    """1 action of the registry."""

    def __init__(self, aid, name, context, default, run=None, source=SOURCE_ANY, phase=PHASE_MAIN, f3=True,
                 f3_name=None):
        self.id = aid
        self.name = name
        self.context = context
        self.default = normalize(default)
        self.key = self.default
        self.run = run              # run(ui, key) -> STOP or Stop(value) when the key is used up
        self.source = source
        self.phase = phase
        self.f3 = f3                # listed in the F3 list
        self.f3_name = f3_name      # the text of the F3 list when it differs from the name

    @property
    def editable(self):
        return self.run is not None

    def __repr__(self):
        return '<Shortcut %s %r>' % (self.id, self.key)


def normalize(sequence):
    """The portable text of a key sequence ('ctrl+s' -> 'Ctrl+S'); '' for no key or a text that is not a key."""
    if not sequence:
        return ''
    seq = QtGui.QKeySequence.fromString(str(sequence), PORTABLE)
    if seq.count() != 1:
        return ''
    combination = seq[0]
    if combination.key() in (Qt.Key.Key_unknown,) + _MODIFIER_KEYS:
        return ''
    if combination.key() == Qt.Key.Key_Enter:       # Enter of the number pad is Return
        combination = QtCore.QKeyCombination(combination.keyboardModifiers(), Qt.Key.Key_Return)
    return QtGui.QKeySequence(combination).toString(PORTABLE)


def native_text(sequence):
    """The key sequence as the user sees it (Cmd on macOS); Return is named Enter, as the keys show it."""
    if not sequence:
        return ''
    text = QtGui.QKeySequence.fromString(sequence, PORTABLE).toString(NATIVE)
    return text.replace('Return', 'Enter')


def qt_key_of(key):
    """
    The Qt key code of a key: an int (the Project tab) or a vispy key (the plot area) or the name of a vispy key.
    None if it has none.
    """
    if key is None:
        return None
    if isinstance(key, int):
        code = int(key)
    else:
        name = key if isinstance(key, str) else getattr(key, 'name', None)     # a vispy key or its name
        if name in _VISPY_NAMES:
            code = int(_VISPY_NAMES[name])
        elif isinstance(name, str) and len(name) == 1:
            code = ord(name.upper())
        else:
            return None
    if code == int(Qt.Key.Key_Enter):
        code = int(Qt.Key.Key_Return)
    return code


def combination_text(code, modifiers, keypad_plain=False):
    """
    The portable text of a key code with the modifiers, or None when no action can have it: a modifier key alone, a
    key of the number pad (unless keypad_plain and no other modifier), an unknown key.
    """
    if code is None:
        return None
    mods = Qt.KeyboardModifier(modifiers) if not isinstance(modifiers, Qt.KeyboardModifier) else modifiers
    if mods & Qt.KeyboardModifier.KeypadModifier:
        if keypad_plain and mods == Qt.KeyboardModifier.KeypadModifier:
            mods = Qt.KeyboardModifier.NoModifier
        else:
            return None
    if mods & ~_MODIFIER_MASK:
        return None
    try:
        qt_key = Qt.Key(code)
    except ValueError:
        return None
    if qt_key in (Qt.Key.Key_unknown,) + _MODIFIER_KEYS:
        return None
    return QtGui.QKeySequence(QtCore.QKeyCombination(mods, qt_key)).toString(PORTABLE)


def event_text(event):
    """The portable text of a QKeyEvent (the key field of the Keyboard Shortcuts page); '' for a modifier alone."""
    text = combination_text(event.key(), event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier)
    return normalize(text) if text else ''


def modifiers_of(sequence):
    """'Ctrl+Shift+S' -> 'Ctrl+Shift'; 'S' -> ''."""
    if not sequence:
        return ''
    combination = QtGui.QKeySequence.fromString(sequence, PORTABLE)[0]
    mods = combination.keyboardModifiers()
    if mods == Qt.KeyboardModifier.NoModifier:
        return ''
    return QtGui.QKeySequence(QtCore.QKeyCombination(mods, Qt.Key.Key_A)).toString(PORTABLE)[:-2]


def key_of(sequence):
    """'Ctrl+Shift+S' -> 'S'."""
    if not sequence:
        return ''
    combination = QtGui.QKeySequence.fromString(sequence, PORTABLE)[0]
    return QtGui.QKeySequence(QtCore.QKeyCombination(combination.key())).toString(PORTABLE)


def join(modifiers, key):
    """'Ctrl+Shift' and 'S' -> 'Ctrl+Shift+S'."""
    if not key:
        return ''
    return normalize('%s+%s' % (modifiers, key) if modifiers else key)


def sources_overlap(a, b):
    return a == SOURCE_ANY or b == SOURCE_ANY or a == b


class ShortcutRegistry:
    """The actions and their keys. One registry for the application: registry()."""

    def __init__(self, contexts, actions):
        self.contexts = {context.id: context for context in contexts}
        self.actions = {}
        for action in actions:
            if action.id in self.actions:
                raise ValueError('duplicate shortcut id %s' % action.id)
            if action.context not in self.contexts:
                raise ValueError('shortcut %s: unknown context %s' % (action.id, action.context))
            self.actions[action.id] = action
        self._index = {}
        self._reindex()

    def _reindex(self):
        self._index = {}
        for action in self.actions.values():
            if action.key:
                self._index.setdefault((action.context, action.key), []).append(action)

    # ------------------------------------------------------------------------------------------------------------
    # the keys
    # ------------------------------------------------------------------------------------------------------------
    def get(self, aid):
        return self.actions.get(aid)

    def key(self, aid):
        action = self.actions.get(aid)
        return action.key if action is not None else ''

    def in_context(self, context):
        return [action for action in self.actions.values() if action.context == context]

    def set_key(self, aid, sequence):
        self.actions[aid].key = normalize(sequence)
        self._reindex()

    def reset(self, aid):
        self.set_key(aid, self.actions[aid].default)

    def reset_all(self):
        for action in self.actions.values():
            action.key = action.default
        self._reindex()

    def overrides(self):
        """{action id: key sequence} of the actions whose key differs from the factory key."""
        return {a.id: a.key for a in self.actions.values() if a.editable and a.key != a.default}

    def apply_overrides(self, overrides):
        """Factory keys, then the keys of the dict; an id that no longer exists (or is read-only) is ignored."""
        for action in self.actions.values():
            action.key = action.default
        if isinstance(overrides, dict):
            for aid, sequence in overrides.items():
                action = self.actions.get(aid)
                if action is not None and action.editable and isinstance(sequence, str):
                    action.key = normalize(sequence)
        self._reindex()

    def conflicts(self, aid, sequence, keys=None):
        """
        The actions that already use the key sequence where the action would use it: the same context (and the same
        source of the key), and the keys of the 'global' context, which come first in every context but a text field.
        keys: {action id: key sequence} to check instead of the keys of the registry (the edits of the page).
        """
        sequence = normalize(sequence)
        action = self.actions[aid]
        if not sequence:
            return []
        own = self.contexts[action.context]
        found = []
        for other in self.actions.values():
            if other is action:
                continue
            other_key = keys.get(other.id, other.key) if keys is not None else other.key
            if other_key != sequence:
                continue
            if other.context == action.context:
                if sources_overlap(other.source, action.source):
                    found.append(other)
            elif 'global' in (other.context, action.context):
                other_context = self.contexts[other.context]
                if not (own.text or other_context.text):
                    found.append(other)
        return found

    # ------------------------------------------------------------------------------------------------------------
    # the keys at work
    # ------------------------------------------------------------------------------------------------------------
    def match(self, context, combination, source, phase=PHASE_MAIN):
        return [a for a in self._index.get((context, combination), ())
                if a.run is not None and a.phase == phase and sources_overlap(a.source, source)]

    def dispatch(self, ui, context_id, key, modifiers):
        """
        Run the action of a key in a context (MainGUI.keyPressEvent). Returns what keyPressEvent returns: None, or the
        value of the Stop that an action returned ('fail', the answer of a tool).
        """
        context = self.contexts.get(context_id)
        if context is None or not context.editable:
            return None
        source = SOURCE_PROJECT if isinstance(key, int) else SOURCE_CANVAS
        combination = combination_text(qt_key_of(key), modifiers, context.keypad_plain)
        if combination is None:
            return None

        def run(phase):
            for action in self.match(context.id, combination, source, phase):
                result = action.run(ui, key)
                if isinstance(result, Stop):
                    return result
            return None

        result = run(PHASE_MAIN)
        if result is None and context.tool_editor is not None:
            if context.tool_active(ui):
                if modifiers_of(combination) in context.passthrough:
                    context.pass_to_tool(ui, key)
            else:
                result = run(PHASE_TOOL)
            if result is None:
                result = run(PHASE_LATE)
        return result.value if result is not None else None

    def global_action(self, code, modifiers):
        """The action of the 'global' context for a key of a QKeyEvent (MainGUI.eventFilter), or None."""
        combination = combination_text(code, modifiers)
        if combination is None:
            return None
        actions = self.match('global', combination, SOURCE_ANY)
        return actions[0] if actions else None

    # ------------------------------------------------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def to_json(overrides):
        return json.dumps({'flatcam_shortcuts': 1, 'shortcuts': overrides}, indent=2, sort_keys=True)

    def from_json(self, text):
        """The {action id: key sequence} of a file of export_shortcuts(); the unknown ids are left out."""
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get('shortcuts'), dict):
            data = data['shortcuts']
        if not isinstance(data, dict):
            raise ValueError('not a file of keyboard shortcuts')
        return {aid: normalize(seq) for aid, seq in data.items()
                if aid in self.actions and self.actions[aid].editable and isinstance(seq, str)}


_REGISTRY = None


def registry():
    """The registry of the application, built on the first use (when the language of the texts is set)."""
    global _REGISTRY
    if _REGISTRY is None:
        from appGUI import shortcut_actions
        _REGISTRY = ShortcutRegistry(shortcut_actions.contexts(), shortcut_actions.actions())
    return _REGISTRY


def dispatch(ui, context, key, modifiers):
    return registry().dispatch(ui, context, key, modifiers)


def apply_options(app):
    """Apply the shortcuts of the preferences (start-up, Save and Apply of the Preferences)."""
    registry().apply_overrides(app.defaults.get('global_shortcuts', {}))
