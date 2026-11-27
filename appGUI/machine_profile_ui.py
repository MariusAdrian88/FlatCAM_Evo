# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""The GUI side of the active machine profile: the fields that the profile sets are locked."""

import builtins
import gettext
import threading

from PyQt6 import QtCore, QtWidgets
from shapely import Point

from appCommon.machine_profile.check import bed_rectangle
from appCommon.machine_profile.output import correction_matrix
from appGUI.GUIElements import FCLabel
from appCommon.machine_profile.facts import active_profile_of, locked_keys, locked_values, mm_to_app_units, \
    permanent_areas
from appCommon.machine_profile.profile import RESERVED_NAME, list_profiles, resolve_profiles_folder

if '_' not in builtins.__dict__:
    _ = gettext.gettext

DOT_COLORS = {'none': 'grey', 'checked': '#2e9e44', 'warning': '#d9a400', 'rejected': '#d03030'}


def correction_on(app):
    """True when the active profile has an enabled calibration: the file outputs are corrected."""
    return correction_matrix(active_profile_of(app)) is not None


def profiles_folder(app):
    """The folder of the machine profiles: the configured one, else the default one in the data folder."""
    configured = app.options.get('global_machine_profiles_folder') or ''
    return resolve_profiles_folder(configured, '' if configured else app.data_path)


def set_status_dot(app, state, tip=''):
    """Color the dot of the status bar selector: state is 'none', 'checked', 'warning' or 'rejected'."""
    ui = getattr(app, 'ui', None)      # headless runs and stub apps have no GUI
    dot = getattr(ui, 'machine_profile_dot', None)
    if dot is None:
        return
    dot.setStyleSheet('QLabel { color: %s; }' % DOT_COLORS[state])
    dot.setToolTip(tip or _("Machine profile: no check yet"))


_LAST = {'result': None, 'dialog': None}      # the last check result and the open violations dialog
MARKER_RADIUS_MM = 1.0


def _app_scale(app):
    """Profile values are in mm: the factor to the units of the app."""
    return mm_to_app_units(1.0, app.app_units)


def clear_markers(app):
    app.tool_shapes.clear(update=True)


def show_markers(app, result):
    """Draw a marker at the end point of each violating record (design coordinates: D = W)."""
    clear_markers(app)
    scale = _app_scale(app)
    radius = MARKER_RADIUS_MM * scale
    for violation in result.violations:
        if violation.end_w is None or violation.end_w[0] is None or violation.end_w[1] is None:
            continue
        color = '#d03030af' if violation.severity == 'reject' else '#d9a400af'
        point = Point(violation.end_w[0] * scale, violation.end_w[1] * scale)
        app.tool_shapes.add(shape=point.buffer(radius), color=color,
                            face_color=color, layer=0, tolerance=radius / 10.0, visible=True)
    app.tool_shapes.redraw()


def show_violations(app, result=None):
    """Open the non-modal dialog with the violations and the assumptions of a check (default: the last one)."""
    result = result if result is not None else _LAST['result']
    if result is None:
        app.inform.emit('[WARNING_NOTCL] %s' % _("Machine profile: no check yet"))
        return
    if not isinstance(app.ui, QtWidgets.QWidget):
        return      # stub apps have no main window
    old = _LAST['dialog']
    if old is not None:
        old.close()
    dialog = QtWidgets.QDialog(app.ui)
    dialog.setWindowTitle(_("Machine profile: check result"))
    dialog.setModal(False)
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    layout = QtWidgets.QVBoxLayout(dialog)
    table = QtWidgets.QTableWidget(len(result.violations), 4)
    table.setHorizontalHeaderLabels([_("Line"), _("Kind"), _("Severity"), _("Message")])
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    for row, violation in enumerate(result.violations):
        for col, text in enumerate((str(violation.line), violation.kind.value, violation.severity,
                                    violation.message)):
            table.setItem(row, col, QtWidgets.QTableWidgetItem(text))
    table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(table)
    layout.addWidget(QtWidgets.QLabel(_("Assumptions")))
    assumptions = QtWidgets.QListWidget()
    assumptions.addItems(result.assumptions)
    layout.addWidget(assumptions)
    buttons = QtWidgets.QHBoxLayout()
    show_btn = QtWidgets.QPushButton(_("Show on canvas"))
    show_btn.clicked.connect(lambda: show_markers(app, result))
    close_btn = QtWidgets.QPushButton(_("Close"))
    close_btn.clicked.connect(dialog.close)
    buttons.addWidget(show_btn)
    buttons.addStretch()
    buttons.addWidget(close_btn)
    layout.addLayout(buttons)
    dialog.resize(700, 450)
    _LAST['dialog'] = dialog
    dialog.show()


def update_bed_outline(app):
    """Draw the bed of the active profile (minus the work offset) on the canvas, or remove it with no profile."""
    canvas = getattr(app, 'plotcanvas', None)       # headless runs and stub apps have no canvas
    if canvas is None:
        return
    profile = active_profile_of(app)
    if profile is None:
        canvas.delete_machine_bed()
        return
    x0, x1, y0, y1 = bed_rectangle(profile)
    scale = _app_scale(app)
    ox, oy = profile.work_offset[0], profile.work_offset[1]
    canvas.draw_machine_bed(((x0 - ox) * scale, (x1 - ox) * scale, (y0 - oy) * scale, (y1 - oy) * scale))


EXCLUSION_COLORS = {'forbid': '#d03030', 'over': '#ff8c00', 'around': '#e6d000'}
_EXCL = {'canvas': None, 'shapes': None}


def update_exclusion_drawing(app):
    """Draw the areas of the active profile (design frame = M - work offset, app units); no profile: clear them."""
    canvas = getattr(app, 'plotcanvas', None)       # headless runs and stub apps have no canvas
    if canvas is None:
        return
    if _EXCL['canvas'] is not canvas or _EXCL['shapes'] is None:
        _EXCL['canvas'] = canvas
        _EXCL['shapes'] = canvas.new_shape_collection(layers=1)
    shapes = _EXCL['shapes']
    shapes.clear(update=True)
    for area in permanent_areas(active_profile_of(app), app.app_units):
        color = EXCLUSION_COLORS[area['strategy']]
        shapes.add(area['shape'], color=color, face_color=color + '4d', update=False, layer=0, tolerance=None)
    shapes.redraw()


class ExclusionDrawer:
    """Click the points of a polygon on the canvas; right click or Escape ends it (fewer than 3 points cancels)."""

    def __init__(self, app, on_done):
        self.app = app
        self.on_done = on_done      # called with the list of design points (app units)
        self.points = []
        self.dragging = False

    def start(self):
        canvas = self.app.plotcanvas
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the points of the area, right click to finish."))
        self.app.call_source = 'geometry'
        canvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        canvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        canvas.graph_event_connect('mouse_release', self.on_mouse_release)
        canvas.graph_event_connect('mouse_move', self.on_mouse_move)
        canvas.graph_event_connect('key_press', self.on_key_press)

    def _position(self, event):
        pos = self.app.plotcanvas.translate_coords(event.pos)
        if self.app.grid_status():
            return self.app.geo_editor.snap(pos[0], pos[1])
        return (pos[0], pos[1])

    def on_mouse_move(self, event):
        from appTool import AppTool     # here: appTool imports the plugin modules that import this one
        self.dragging = bool(event.is_dragging)
        AppTool.draw_moving_selection_shape_poly(self, points=self.points, data=self._position(event),
                                                 color='#ff8c00', face_color='#ff8c00bf')

    def on_mouse_release(self, event):
        if event.button == 1:
            self.points.append(self._position(event))
        elif event.button == 2 and not self.dragging:
            self.finish()

    def on_key_press(self, event):
        from appPlugins.Common.clearing.selection import ESCAPE_KEYS, key_of
        if key_of(event) in ESCAPE_KEYS:
            self.finish()

    def finish(self):
        from appTool import AppTool
        canvas = self.app.plotcanvas
        canvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
        canvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
        canvas.graph_event_disconnect('key_press', self.on_key_press)
        canvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        canvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
        canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        AppTool.delete_moving_selection_shape(self)
        self.app.call_source = 'app'
        points, self.points = self.points, []
        if len(points) < 3:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Fewer than 3 points."))
            return
        self.on_done(points)


def update_sender_label(app):
    """Show the label of the G-code Sender only while a profile is active (nothing when the Sender is not built)."""
    sender = app.__dict__.get('grbl_sender_tool')
    ui = sender.__dict__.get('ui') if sender is not None else None
    label = ui.__dict__.get('machine_profile_label') if ui is not None else None
    if label is not None:
        profile = active_profile_of(app)
        label.setVisible(profile is not None)
        if profile is not None:
            # the stream, the restart and Save levelled G-code are bound to the profile: checked, and corrected
            # when the calibration is on
            label.setText(_("Machine profile: %s (checked / corrected)") % profile.name if correction_on(app)
                          else _("Machine profile: %s (checked)") % profile.name)


def machine_status_text(plugin):
    """The status line under the generate button of a CNC plugin."""
    profile = active_profile_of(plugin.app)
    if profile is None:
        return _("Machine profile: None")
    text = _("Machine profile: %s") % profile.name
    return text + _(" (fields locked)") if plugin.locked_form_keys else text


def update_status_line(plugin):
    """Create (once) and refresh the label under the generate button of Milling, Drilling or Solder Paste."""
    ui = plugin.ui
    button = ui.solder_gcode_btn if plugin is plugin.app.paste_tool else ui.generate_cnc_button
    label = ui.__dict__.get('machine_profile_label')
    if label is None:
        label = ui.machine_profile_label = FCLabel('')
        parent_layout = _layout_of(button.parentWidget().layout(), button)
        if parent_layout is None:
            return
        parent_layout.insertWidget(parent_layout.indexOf(button) + 1, label)
    label.set_value(machine_status_text(plugin))


def _layout_of(layout, widget):
    """The (nested) layout that holds the widget, or None."""
    if layout is None:
        return None
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        found = _layout_of(child, widget)
        if found is not None:
            return found
    return None


def report_gate(app, gate):
    """Show the result of a file check (a FileGate) in the dot: green checked, yellow warnings, red rejected."""
    _LAST['result'] = gate.result
    if getattr(getattr(app, 'ui', None), 'machine_profile_dot', None) is None:
        return      # stub apps have no status bar
    if app.headless is True:
        return      # a headless run has no dot and no dialog: the refusal text on stderr is the feedback
    qt_app = QtCore.QCoreApplication.instance()
    if qt_app is not None and QtCore.QThread.currentThread() is not qt_app.thread():
        # a Tcl command or a job runs on a worker thread: the dot and the dialog belong to the GUI thread
        _gate_bridge().reported.emit(app, gate)
        return
    _show_gate(app, gate)


class _GateBridge(QtCore.QObject):
    """Carries a file check result from a worker thread to the GUI thread (this object lives in the GUI thread)."""
    reported = QtCore.pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.reported.connect(self.on_reported)

    @QtCore.pyqtSlot(object, object)
    def on_reported(self, app, gate):
        _show_gate(app, gate)


def _make_gate_bridge():
    """A bridge that lives in the thread of the application (the GUI thread), whichever thread made it."""
    bridge = _GateBridge()
    qt_app = QtCore.QCoreApplication.instance()
    if qt_app is not None and bridge.thread() is not qt_app.thread():
        bridge.moveToThread(qt_app.thread())    # only possible from the thread that made it: here
    return bridge


_GATE_BRIDGE = None
_GATE_BRIDGE_LOCK = threading.Lock()


def _gate_bridge():
    """The bridge, made at the first use (a worker thread can be the first user)."""
    global _GATE_BRIDGE
    with _GATE_BRIDGE_LOCK:
        if _GATE_BRIDGE is None:
            _GATE_BRIDGE = _make_gate_bridge()
        return _GATE_BRIDGE


def _show_gate(app, gate):
    """The GUI part of report_gate(): only on the GUI thread."""
    name = app.options.get('global_machine_profile')
    if not name or name == RESERVED_NAME:
        set_status_dot(app, 'none')
    elif not gate.ok:
        set_status_dot(app, 'rejected', gate.error)
        if gate.result is not None:
            show_violations(app, gate.result)
    elif gate.note:
        set_status_dot(app, 'warning', gate.note)
    else:
        set_status_dot(app, 'checked', _("Machine profile: the last file is checked"))


def refresh_profile_selector(app):
    """Fill the status bar combo with None and the profile names; select the active one."""
    ui = getattr(app, 'ui', None)
    combo = getattr(ui, 'machine_profile_combo', None)
    if combo is None:
        return
    names = [profile.name for profile in list_profiles(profiles_folder(app))]
    active = app.options.get('global_machine_profile') or RESERVED_NAME
    combo.blockSignals(True)
    combo.clear()
    combo.addItems([RESERVED_NAME] + names)
    index = combo.findText(active)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def on_selector_changed(app, name):
    """The user picked a profile in the status bar: make it the active one."""
    app.defaults['global_machine_profile'] = name
    app.options['global_machine_profile'] = name
    apply_machine_profile_locks(app)
    set_status_dot(app, 'none')


def setup_profile_selector(app):
    """Connect the status bar combo and fill it (called once the GUI is built)."""
    ui = getattr(app, 'ui', None)
    combo = getattr(ui, 'machine_profile_combo', None)
    if combo is None:
        return
    refresh_profile_selector(app)
    combo.currentTextChanged.connect(lambda name: on_selector_changed(app, name))
    ui.machine_profile_dot.mousePressEvent = lambda event: show_violations(app)
    set_status_dot(app, 'none')


def show_locked_fields(plugin, previous=()):
    """
    Disable the widgets of a plugin (Milling, Drilling, Solder Paste) for its locked keys (`plugin.locked_form_keys`,
    {key: note}). The widget shows the profile value and has the note as tooltip. A key of `previous` that is not
    locked any more is enabled again. A plugin calls this after each reload of its form from a storage, so the
    stored value never shows in a locked widget. The plugin lists its field maps in `locked_field_maps()`.
    """
    locked, values = plugin.locked_form_keys, plugin.locked_form_values
    for fields in plugin.locked_field_maps():
        for key, widget in fields.items():
            if key in locked:
                widget.setEnabled(False)
                widget.setToolTip(locked[key])
                if key in values:
                    widget.set_value(values[key])
            elif key in previous:
                widget.setEnabled(True)


def lock_plugin_fields(plugin):
    """Take the locked keys of the Preferences manager into the plugin and show them in its form."""
    manager = plugin.app.preferencesUiManager
    previous = plugin.locked_form_keys
    plugin.locked_form_keys = dict(manager.locked)
    plugin.locked_form_values = dict(manager.locked_values)
    show_locked_fields(plugin, previous)


def apply_machine_profile_locks(app):
    """Lock the Preferences fields and the plugin fields that the active profile overrides. No profile: unlock all."""
    profile = active_profile_of(app)
    app.preferencesUiManager.set_locked(locked_keys(profile), locked_values(profile))
    for plugin in (app.milling_tool, app.drilling_tool, app.paste_tool):
        if plugin is not None:
            lock_plugin_fields(plugin)
            try:
                update_status_line(plugin)
            except AttributeError:
                pass        # a plugin whose form is not built (headless runs, stub plugins)
    update_bed_outline(app)
    update_exclusion_drawing(app)
    update_sender_label(app)
