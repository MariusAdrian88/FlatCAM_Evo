"""Parameter dialogs of the Gerber Editor operations (menu actions). Each handler asks the numbers, keeps them in
`app.options['gerber_editor_<name>']` and calls the apply_* delegate of the editor."""

import builtins
import gettext

from PyQt6 import QtWidgets

import appTranslation as fcTranslate
from appGUI.GUIElements import FCDoubleSpinner, FCSpinner, FCLabel

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

PREFIX = 'gerber_editor_'


def stored(editor, name, fallback):
    """The value of the option `gerber_editor_<name>`: the saved one, else the default, else the fallback."""
    key = PREFIX + name
    app = editor.app
    for source in (app.options, app.defaults):
        try:
            value = source.get(key)
        except (AttributeError, TypeError):
            value = None
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            return value
    return fallback


def ask(editor, title, fields):
    """
    Show a modal dialog and return the dict {name: value}, None when it was cancelled.

    :param fields: list of (name, label, kind, options): kind 'float' (options: min, max, decimals, default),
                   'int' (min, max, default) or 'choice' (items: [(text, data)], default: data). The value of the
                   'float' and 'int' kinds is read from, and saved to, the option gerber_editor_<name>.
    """
    dialog = QtWidgets.QDialog(editor.app.ui)
    dialog.setWindowTitle(title)
    layout = QtWidgets.QGridLayout(dialog)
    widgets = {}
    for row, (name, label, kind, opts) in enumerate(fields):
        if kind == 'float':
            widget = FCDoubleSpinner()
            widget.set_precision(opts.get('decimals', editor.app.decimals))
            widget.set_range(opts.get('min', 0.0), opts.get('max', 1e6))
            widget.set_value(float(stored(editor, name, opts.get('default', 0.0))))
        elif kind == 'int':
            widget = FCSpinner()
            widget.set_range(opts.get('min', 1), opts.get('max', 1000000))
            widget.set_value(int(stored(editor, name, opts.get('default', 1))))
        else:
            widget = QtWidgets.QComboBox()
            for text, data in opts['items']:
                widget.addItem(str(text), data)
            index = widget.findData(opts.get('default'))
            widget.setCurrentIndex(max(index, 0))
        widgets[name] = (kind, widget)
        layout.addWidget(FCLabel(label), row, 0)
        layout.addWidget(widget, row, 1)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                         QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons, len(fields), 0, 1, 2)

    accepted = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    values = {}
    if accepted:
        for name, (kind, widget) in widgets.items():
            if kind == 'choice':
                values[name] = widget.currentData()
            else:
                values[name] = widget.get_value()
                editor.app.options[PREFIX + name] = values[name]
    dialog.deleteLater()
    if not accepted:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        return None
    return values


def on_step_repeat(editor):
    values = ask(editor, _("Step and Repeat"), [
        ('step_repeat_rows', _("Rows"), 'int', {'min': 1}),
        ('step_repeat_columns', _("Columns"), 'int', {'min': 1}),
        ('step_repeat_pitch_x', _("X pitch"), 'float', {'min': -1e6}),
        ('step_repeat_pitch_y', _("Y pitch"), 'float', {'min': -1e6}),
    ])
    if values:
        return editor.apply_step_repeat(values['step_repeat_rows'], values['step_repeat_columns'],
                                        values['step_repeat_pitch_x'], values['step_repeat_pitch_y'])


def on_transcode(editor):
    codes = [(str(code), code) for code, aperture in editor.storage_dict.items()
             if str(aperture.get('type')) != 'REG']
    if not codes:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % _("There are no apertures."))
        return 'fail'
    values = ask(editor, _("Transcode"), [
        ('transcode_code', _("Move the selection to the aperture"), 'choice',
         {'items': codes, 'default': editor.last_aperture_selected})])
    if values:
        return editor.apply_transcode(values['transcode_code'])


def on_snap_to_grid(editor):
    # the dialog opens with the grid of the editor toolbar; without a valid one, with the saved value or the default
    for name, entry in (('snap_grid_x', 'grid_gap_x_entry'), ('snap_grid_y', 'grid_gap_y_entry')):
        try:
            value = float(getattr(editor.app.ui, entry).get_value())
        except (AttributeError, TypeError, ValueError):
            continue
        if value > 0:
            editor.app.options[PREFIX + name] = value
    values = ask(editor, _("Snap to Grid"), [
        ('snap_grid_x', _("Grid X"), 'float', {'min': 1e-6}),
        ('snap_grid_y', _("Grid Y"), 'float', {'min': 1e-6}),
    ])
    if values:
        return editor.apply_snap_to_grid(values['snap_grid_x'], values['snap_grid_y'])


def on_renumber(editor):
    values = ask(editor, _("Renumber"), [
        ('renumber_start', _("First aperture code"), 'int', {'min': 10})])
    if values:
        return editor.apply_renumber(values['renumber_start'])


def on_pads_to_flashes(editor):
    values = ask(editor, _("Drawn Pads to Flashes"), [
        ('pads_tolerance', _("Tolerance"), 'float', {'min': 0.0, 'decimals': 4})])
    if values:
        return editor.apply_pads_to_flashes(values['pads_tolerance'])


def on_teardrops(editor):
    values = ask(editor, _("Teardrops"), [
        ('teardrop_length', _("Length (%)"), 'float', {'min': 1.0, 'max': 1000.0, 'decimals': 1}),
        ('teardrop_width', _("Width (%)"), 'float', {'min': 1.0, 'max': 100.0, 'decimals': 1})])
    if values:
        return editor.apply_teardrops(values['teardrop_length'], values['teardrop_width'])


def on_snowman(editor):
    values = ask(editor, _("Snowman Fillets"), [
        ('snowman_size', _("Size (%)"), 'float', {'min': 1.0, 'max': 1000.0, 'decimals': 1}),
        ('snowman_position', _("Position (%)"), 'float', {'min': 0.0, 'max': 100.0, 'decimals': 1})])
    if values:
        return editor.apply_snowman(values['snowman_size'], values['snowman_position'])
