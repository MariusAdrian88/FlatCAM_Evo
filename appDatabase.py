
from PyQt6 import QtGui, QtCore, QtWidgets
from appGUI.GUIElements import FCEntry, FCButton, FCDoubleSpinner, FCComboBox, FCCheckBox, FCSpinner, \
    FCTree, RadioSet, FCFileSaveDialog, FCLabel, FCComboBox2, FCMessageBox, GLay
from camlib import to_dict
from appCommon.SafeEval import safe_eval
from appCommon.ToolDatabase import read_database, read_library, write_database, matching_tools, OPERATION_PREFIXES, \
    UnitsMismatchError
from appGUI.ToolLibrary import ToolLibraryModel, ToolLibraryFilter, operation_names
from defaults import AppDefaults

import sys
import json

from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path

import gettext
import appTranslation as fcTranslate
import builtins
import logging

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def _database_enum(value, labels, field):
    if type(value) is int and 0 <= value < len(labels):
        return value
    if isinstance(value, str):
        for index, aliases in enumerate(labels):
            if value in aliases or value == str(index):
                return index
    raise ValueError('%s: unsupported value %r' % (field, value))


def _finite_database_number(value, field):
    if isinstance(value, bool):
        raise ValueError('%s: boolean is not a number' % field)
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError('%s: expected a number' % field) from error
    if not math.isfinite(number):
        raise ValueError('%s: expected a finite number' % field)
    return number


def _database_option_items(defaults):
    try:
        return defaults.items()
    except AttributeError:
        return defaults.options.items()


def _database_option(app, key):
    defaults = getattr(app, 'defaults', {})
    factory_defaults = getattr(defaults, 'factory_defaults', {})
    missing = object()
    option = app.options.get(key, missing)
    if option is not missing:
        return option
    try:
        fallback = defaults.get(key, missing)
    except AttributeError:
        fallback = missing
    if fallback is missing:
        fallback = deepcopy(factory_defaults.get(key))
    return fallback


def _evaluated_preference(field):
    """True for a key whose factory value is None or a text of numbers ('0.1', '0.0, 0.0'). The Preferences form
    evaluates such a field on Apply and stores a number, a tuple (a list after a restart) or None instead."""
    if field not in AppDefaults.factory_defaults:
        return False
    factory = AppDefaults.factory_defaults[field]
    if factory is None:
        return True
    if not isinstance(factory, str):
        return False
    try:
        return all(math.isfinite(float(part)) for part in factory.split(','))
    except ValueError:
        return False


def _evaluated_preference_value(value):
    """True for None, a text, a finite number or a list of finite numbers: the forms of an evaluated preference."""
    if value is None or isinstance(value, str):
        return True
    items = value if isinstance(value, (list, tuple)) else [value]
    return all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in items)


def _validate_database_option(value, default, field):
    # A record keeps the form of the moment it was saved, and the Preferences change the form of these keys on Apply.
    if _evaluated_preference(field) and _evaluated_preference_value(value):
        return value

    if value is None:
        if default is None:
            return value
        raise ValueError('%s: None is not allowed' % field)

    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError('%s: expected a boolean' % field)
    elif isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('%s: expected a number' % field)
        _finite_database_number(value, field)
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError('%s: expected a string' % field)
    elif isinstance(default, list):
        if not isinstance(value, list):
            raise ValueError('%s: expected a list' % field)
    elif default is None:
        if isinstance(value, bool):
            raise ValueError('%s: expected a string, number, or None' % field)
        if isinstance(value, (int, float)):
            _finite_database_number(value, field)
        elif not isinstance(value, str):
            raise ValueError('%s: expected a string, number, or None' % field)
    elif not isinstance(value, type(default)):
        raise ValueError('%s: unexpected value type' % field)
    return value


def normalize_tools_database(records, defaults, skipped=None):
    """Raise on the first bad record, or, with a ``skipped`` dictionary, leave it out and store the reason."""
    if not isinstance(records, dict):
        raise ValueError('Tools DB root: expected a dictionary')

    default_values = dict(_database_option_items(defaults))
    normalized = deepcopy(records)
    target_labels = [('General', _('General')), ('Milling', _('Milling')), ('Drilling', _('Drilling')),
                     ('Isolation', _('Isolation')), ('Paint', _('Paint')), ('NCC', _('NCC')), ('Cutout', _('Cutout'))]
    shape_labels = [(label,) for label in ('C1', 'C2', 'C3', 'C4', 'B', 'V', 'L')]
    job_labels = [('Roughing', _('Roughing')), ('Finishing', _('Finishing')),
                  ('Isolation', _('Isolation')), ('Polishing', _('Polishing'))]
    offset_labels = [('Path', _('Path')), ('In', _('In')), ('Out', _('Out')), ('Custom', _('Custom'))]
    gap_labels = [('Bridge', _('Bridge')), ('Thin', _('Thin')), ('M-Bites', _('M-Bites'))]
    order_labels = [('no',), ('fwd',), ('rev',)]

    for tool_id, tool in list(normalized.items()):
        try:
            if (type(tool_id) is not str or not tool_id.isdecimal() or
                    str(int(tool_id)) != tool_id or not 0 < int(tool_id) <= 9223372036854775807):
                raise ValueError('record ID %r: expected a positive canonical decimal string' % tool_id)
            if not isinstance(tool, dict):
                raise ValueError('expected a record dictionary')
            if not isinstance(tool.get('name'), str):
                raise ValueError('name: expected a string')
            if not tool['name'].strip():
                raise ValueError('name: enter a tool name')
            tool['tooldia'] = _finite_database_number(tool.get('tooldia'), 'tooldia')
            if tool['tooldia'] <= 0:
                raise ValueError('tooldia: diameter must be greater than zero')
            if not isinstance(tool.get('data'), dict):
                raise ValueError('data: expected a dictionary')

            data = tool['data']
            # Old databases must not enable a new cutting operation via current preferences.
            data.setdefault('tools_ncc_final_cleanup', False)
            data.setdefault('tools_paint_final_cleanup', False)
            data['tool_target'] = _database_enum(data.get('tool_target'), target_labels, 'data.tool_target')

            if 'job' in data:
                data['tools_mill_job_type'] = _database_enum(data['job'], job_labels, 'data.job')
                del data['job']
                log.warning('Tools DB record %s: migrated data.job to data.tools_mill_job_type', tool_id)
            if 'tools_mill_offset_value' in tool:
                data['tools_mill_offset_value'] = _finite_database_number(
                    tool['tools_mill_offset_value'], 'tools_mill_offset_value')
                del tool['tools_mill_offset_value']
                log.warning('Tools DB record %s: migrated record tools_mill_offset_value to data', tool_id)

            for option, default in default_values.items():
                if option.startswith(OPERATION_PREFIXES[data['tool_target']]):
                    data.setdefault(option, deepcopy(default))

            for option in ('tools_mill_tool_shape', 'tools_mill_job_type', 'tools_mill_offset_type',
                           'tools_mill_offset_value', 'tools_cutout_gap_type'):
                if option not in data:
                    if option not in default_values:
                        raise ValueError('%s: missing option' % option)
                    data[option] = deepcopy(default_values[option])

            data['tools_mill_tool_shape'] = _database_enum(
                data['tools_mill_tool_shape'], shape_labels, 'tools_mill_tool_shape')
            data['tools_mill_job_type'] = _database_enum(
                data['tools_mill_job_type'], job_labels, 'tools_mill_job_type')
            data['tools_mill_offset_type'] = _database_enum(
                data['tools_mill_offset_type'], offset_labels, 'tools_mill_offset_type')
            data['tools_mill_offset_value'] = _finite_database_number(
                data['tools_mill_offset_value'], 'tools_mill_offset_value')

            gap_value = data['tools_cutout_gap_type']
            if gap_value in ('b', 'bt', 'mb'):
                gap_value = {'b': 0, 'bt': 1, 'mb': 2}[gap_value]
            data['tools_cutout_gap_type'] = _database_enum(gap_value, gap_labels, 'tools_cutout_gap_type')
            if 'tools_drill_tool_order' in data:
                # the factory value is 'no', the Preferences store the index of the Order combo
                _database_enum(data['tools_drill_tool_order'], order_labels, 'tools_drill_tool_order')

            for bound in ('tol_min', 'tol_max'):
                value = data.get(bound, 0.0)
                data[bound] = _finite_database_number(value, bound)
            if data['tol_min'] < 0 or data['tol_min'] > data['tol_max']:
                raise ValueError('tol_min/tol_max: expected 0 <= min <= max')

            enum_options = {
                'tools_mill_tool_shape', 'tools_mill_job_type',
                'tools_mill_offset_type', 'tools_cutout_gap_type', 'tools_drill_tool_order'
            }
            for option, value in data.items():
                if option in enum_options or option in ('tool_target', 'tol_min', 'tol_max'):
                    continue
                if option in default_values:
                    _validate_database_option(value, default_values[option], option)
        except (TypeError, ValueError) as error:
            if skipped is None:
                raise ValueError('record %s: %s' % (tool_id, error)) from error
            skipped[str(tool_id)] = str(error)
            del normalized[tool_id]

    return normalized


def tools_database_error_message(error):
    if isinstance(error, UnitsMismatchError):
        return _("The Tools DB uses %s and the application uses %s. The Tools DB was not used.") % (
            error.stored, error.current)
    return _("Could not load Tools DB file.")


def initialize_tools_database(filename, legacy_filenames, defaults):
    """Migrate the first legacy library that holds tools; an empty file is a failed start, not a library."""
    if Path(filename).exists() and Path(filename).stat().st_size:
        return
    if isinstance(legacy_filenames, (str, Path)):
        legacy_filenames = [legacy_filenames]
    records = {}
    for legacy_filename in legacy_filenames:
        if not Path(legacy_filename).is_file():
            continue
        try:
            records = load_tools_database(legacy_filename, defaults)
        except (OSError, TypeError, ValueError) as error:
            log.error('Tools DB migration skipped %s: %s', legacy_filename, error)
            continue
        if records:
            log.info('Tools DB migrated from %s', legacy_filename)
            break
    units = dict(_database_option_items(defaults)).get('units', 'MM') if records else None
    write_tools_database(filename, records, units=units, expected_revision=0)


def load_tools_database(filename, defaults, *, target=None, diameter=None, units=None, skipped=None):
    """Bad records are left out and logged; ``skipped`` collects their reasons."""
    if Path(filename).suffix.lower() == '.sqlite3':
        records, metadata = read_database(filename, target=target, diameter=diameter, units=units)
    else:
        with open(filename, encoding='utf-8-sig') as stream:
            records = json.load(stream)
        if isinstance(records, dict) and records.get('format') == 'flatcam-tools':
            if records.get('version') != 1:
                raise ValueError('Unsupported tools JSON version')
            stored_units = records.get('units')
            if units and stored_units and units.upper() != stored_units:
                raise UnitsMismatchError(stored_units, units.upper())
            records = records['tools']
    skipped = {} if skipped is None else skipped
    normalized = normalize_tools_database(records, defaults, skipped=skipped)
    for tool_id, reason in skipped.items():
        log.warning('Tools DB %s: skipped record %s: %s', filename, tool_id, reason)
    return matching_tools(normalized, target=target, diameter=diameter)


def write_tools_database(filename, records, *, units=None, expected_revision=None, retained_ids=None):
    if Path(filename).suffix.lower() == '.sqlite3':
        return write_database(filename, records, units=units, expected_revision=expected_revision,
                              retained_ids=retained_ids)
    if Path(filename).suffix.lower() == '.json' and units:
        records = {'format': 'flatcam-tools', 'version': 1, 'units': units.upper(), 'tools': records}
    payload = json.dumps(records, default=to_dict, indent=2, ensure_ascii=False, allow_nan=False).encode('utf-8')
    output = QtCore.QSaveFile(str(filename))
    output.setDirectWriteFallback(False)
    if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    try:
        if output.write(payload) != len(payload):
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
    except Exception:
        output.cancelWriting()
        raise


class ToolsDB2UI:
    
    def __init__(self, app, grid_layout):
        self.app = app
        self.decimals = self.app.decimals

        self.offset_item_options = [_("Path"), _("In"), _("Out"), _("Custom")]
        self.job_item_options = [_('Roughing'), _('Finishing'), _('Isolation'), _('Polishing')]
        self.tool_job_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]

        self.g_lay = grid_layout
        self.g_lay.setRowStretch(0, 1)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.g_lay.addWidget(self.splitter, 0, 0, 1, 2)
        library_widget = QtWidgets.QWidget()
        tree_layout = QtWidgets.QVBoxLayout(library_widget)
        tree_layout.setContentsMargins(0, 0, 6, 0)
        self.splitter.addWidget(library_widget)

        self.context_label = FCLabel(_("Tool library"), bold=True)
        tree_layout.addWidget(self.context_label)
        self.search_entry = QtWidgets.QLineEdit()
        self.search_entry.setPlaceholderText(_("Search name, diameter or shape…"))
        self.search_entry.setClearButtonEnabled(True)
        self.search_entry.setAccessibleName(_("Search tools"))
        tree_layout.addWidget(self.search_entry)
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItem(_("All operations"), None)
        for target, name in enumerate(operation_names()):
            self.filter_combo.addItem(name, target)
        self.filter_combo.setAccessibleName(_("Filter by operation"))
        tree_layout.addWidget(self.filter_combo)

        self.tree_widget = QtWidgets.QTreeView()
        self.tree_widget.setIndentation(0)
        self.tree_widget.setRootIsDecorated(False)
        self.tree_widget.setUniformRowHeights(True)
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree_widget.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree_widget.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

        # set alternating colors
        # self.tree_widget.setAlternatingRowColors(True)
        # p = QtGui.QPalette()
        # p.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(226, 237, 253) )
        # self.tree_widget.setPalette(p)

        self.tree_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                       QtWidgets.QSizePolicy.Policy.MinimumExpanding)
        tree_layout.addWidget(self.tree_widget)

        param_hlay = QtWidgets.QVBoxLayout()
        param_area = QtWidgets.QScrollArea()
        self.param_area = param_area
        param_widget = QtWidgets.QWidget()
        param_widget.setLayout(param_hlay)

        param_area.setWidget(param_widget)
        param_area.setWidgetResizable(True)

        param_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.splitter.addWidget(param_area)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([400, 600])
        self.splitter.setChildrenCollapsible(False)

        # ###########################################################################
        # ############## The UI form ################################################
        # ###########################################################################

        # Tool description box
        self.tool_description_box = QtWidgets.QGroupBox()
        self.tool_description_box.setStyleSheet("""
        QGroupBox
        {
            font-size: 16px;
            font-weight: bold;
        }
        """)
        self.description_vlay = QtWidgets.QVBoxLayout()
        self.tool_description_box.setTitle(_("Tool geometry"))
        self.tool_description_box.setMinimumWidth(250)
        self.tool_description_box.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                                QtWidgets.QSizePolicy.Policy.Minimum)

        # Milling box
        self.milling_box = QtWidgets.QGroupBox()
        self.milling_box.setStyleSheet("""
        QGroupBox
        {
            font-size: 16px;
            font-weight: bold;
        }
        """)
        self.milling_vlay = QtWidgets.QVBoxLayout()
        self.milling_box.setTitle(_("Cutting parameters"))
        self.milling_box.setMinimumWidth(250)

        # NCC TOOL BOX
        self.ncc_box = QtWidgets.QGroupBox()
        self.ncc_box.setStyleSheet("""
                        QGroupBox
                        {
                            font-size: 16px;
                            font-weight: bold;
                        }
                        """)
        self.ncc_vlay = QtWidgets.QVBoxLayout()
        self.ncc_box.setTitle(_("NCC Parameters"))
        self.ncc_box.setMinimumWidth(250)

        # PAINT TOOL BOX
        self.paint_box = QtWidgets.QGroupBox()
        self.paint_box.setStyleSheet("""
                        QGroupBox
                        {
                            font-size: 16px;
                            font-weight: bold;
                        }
                        """)
        self.paint_vlay = QtWidgets.QVBoxLayout()
        self.paint_box.setTitle(_("Paint Parameters"))
        self.paint_box.setMinimumWidth(250)

        # ISOLATION TOOL BOX
        self.iso_box = QtWidgets.QGroupBox()
        self.iso_box.setStyleSheet("""
                     QGroupBox
                     {
                         font-size: 16px;
                         font-weight: bold;
                     }
                     """)
        self.iso_vlay = QtWidgets.QVBoxLayout()
        self.iso_box.setTitle(_("Isolation Parameters"))
        self.iso_box.setMinimumWidth(250)

        # DRILLING TOOL BOX
        self.drill_box = QtWidgets.QGroupBox()
        self.drill_box.setStyleSheet("""
                     QGroupBox
                     {
                         font-size: 16px;
                         font-weight: bold;
                     }
                     """)
        self.drill_vlay = QtWidgets.QVBoxLayout()
        self.drill_box.setTitle(_("Drilling Parameters"))
        self.drill_box.setMinimumWidth(250)

        # CUTOUT TOOL BOX
        self.cutout_box = QtWidgets.QGroupBox()
        self.cutout_box.setStyleSheet("""
                     QGroupBox
                     {
                         font-size: 16px;
                         font-weight: bold;
                     }
                     """)
        self.cutout_vlay = QtWidgets.QVBoxLayout()
        self.cutout_box.setTitle(_("Cutout Parameters"))
        self.cutout_box.setMinimumWidth(250)

        # Layout Constructor
        self.tool_description_box.setLayout(self.description_vlay)
        self.milling_box.setLayout(self.milling_vlay)
        self.ncc_box.setLayout(self.ncc_vlay)
        self.paint_box.setLayout(self.paint_vlay)
        self.iso_box.setLayout(self.iso_vlay)
        self.drill_box.setLayout(self.drill_vlay)
        self.cutout_box.setLayout(self.cutout_vlay)

        # One column reflows cleanly in narrow and detached windows.
        for box in (self.tool_description_box, self.milling_box, self.drill_box,
                    self.iso_box, self.paint_box, self.ncc_box, self.cutout_box):
            box.setStyleSheet('')
            param_hlay.addWidget(box)
        param_hlay.addStretch()

        # ###########################################################################
        # ################ Tool UI form #############################################
        # ###########################################################################
        self.grid_tool = GLay(v_spacing=5, h_spacing=3)
        self.description_vlay.addLayout(self.grid_tool)
        self.description_vlay.addStretch()

        # Tool Name
        self.name_label = FCLabel(_("Name"), bold=True)
        self.name_label.setToolTip(
            _("Tool name.\n"
              "This is not used in the app, it's function\n"
              "is to serve as a note for the user."))

        self.name_entry = FCEntry()
        self.name_entry.setObjectName('gdb_name')

        self.grid_tool.addWidget(self.name_label, 0, 0)
        self.grid_tool.addWidget(self.name_entry, 0, 1)

        # Tool Dia
        self.dia_label = FCLabel('%s:' % _('Diameter'))
        self.dia_label.setToolTip(
            '%s.' % _("Tool Diameter"))

        self.dia_entry = FCDoubleSpinner()
        self.dia_entry.set_range(0.0, 10000.0000)
        self.dia_entry.set_precision(self.decimals)
        self.dia_entry.setObjectName('gdb_dia')

        self.grid_tool.addWidget(self.dia_label, 1, 0)
        self.grid_tool.addWidget(self.dia_entry, 1, 1)

        # Tool Tolerance
        self.tol_label = FCLabel('%s:' % _("Diameter Tolerance"), bold=True)
        self.tol_label.setToolTip(
            _("Tool tolerance. This tool will be used if the desired tool diameter\n"
              "is within the tolerance specified here.")
        )
        self.grid_tool.addWidget(self.tol_label, 2, 0, 1, 2)

        # Tolerance Min Limit
        self.min_limit_label = FCLabel('%s:' % _("Min"))
        self.min_limit_label.setToolTip(
            _("Set the tool tolerance minimum.")
        )
        self.tol_min_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tol_min_entry.set_precision(self.decimals)
        self.tol_min_entry.set_range(0, 10000.0000)
        self.tol_min_entry.setSingleStep(0.1)
        self.tol_min_entry.setObjectName("gdb_tol_min")

        self.grid_tool.addWidget(self.min_limit_label, 4, 0)
        self.grid_tool.addWidget(self.tol_min_entry, 4, 1)

        # Tolerance Min Limit
        self.max_limit_label = FCLabel('%s:' % _("Max"))
        self.max_limit_label.setToolTip(
            _("Set the tool tolerance maximum.")
        )
        self.tol_max_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tol_max_entry.set_precision(self.decimals)
        self.tol_max_entry.set_range(0, 10000.0000)
        self.tol_max_entry.setSingleStep(0.1)
        self.tol_max_entry.setObjectName("gdb_tol_max")

        self.grid_tool.addWidget(self.max_limit_label, 6, 0)
        self.grid_tool.addWidget(self.tol_max_entry, 6, 1)

        # Tool Object Type
        self.tool_op_label = FCLabel('%s:' % _('Operation'), bold=True)
        self.tool_op_label.setToolTip(
            _("The kind of Application Tool where this tool is to be used."))

        self.tool_op_combo = FCComboBox2()
        self.tool_op_combo.addItems(
            [_("General"), _("Milling"), _("Drilling"), _('Isolation'), _('Paint'), _('NCC'), _('Cutout')])
        self.tool_op_combo.setObjectName('gdb_tool_target')

        self.grid_tool.addWidget(self.tool_op_label, 8, 0)
        self.grid_tool.addWidget(self.tool_op_combo, 8, 1)

        self.properties_label = FCLabel(_("Properties for"))
        self.properties_combo = QtWidgets.QComboBox()
        for target, name in enumerate(operation_names()[1:], 1):
            self.properties_combo.addItem(name, target)
        self.grid_tool.addWidget(self.properties_label, 10, 0)
        self.grid_tool.addWidget(self.properties_combo, 10, 1)
        self.advanced_cb = FCCheckBox(_("Show advanced settings"))
        self.grid_tool.addWidget(self.advanced_cb, 12, 0, 1, 2)

        # ###########################################################################
        # ############### MILLING UI form ###########################################
        # ###########################################################################
        self.grid0 = GLay(v_spacing=5, h_spacing=3)
        self.milling_vlay.addLayout(self.grid0)
        self.milling_vlay.addStretch()

        # Tool Shape
        self.shape_label = FCLabel('%s:' % _('Shape'))
        self.shape_label.setToolTip(
            _("Tool Shape. \n"
              "Can be:\n"
              "C1 ... C4 = circular tool with x flutes\n"
              "B = ball tip milling tool\n"
              "V = v-shape milling tool\n"
              "L = laser")
        )

        self.mill_shape_combo = FCComboBox2()
        self.mill_shape_combo.addItems(self.tool_job_options)
        self.mill_shape_combo.setObjectName('gdb_shape')

        self.grid0.addWidget(self.shape_label, 2, 0)
        self.grid0.addWidget(self.mill_shape_combo, 2, 1)

        # V-Dia
        self.vdia_label = FCLabel('%s:' % _("V-Dia"))
        self.vdia_label.setToolTip(
            _("V-Dia.\n"
              "Diameter of the tip for V-Shape Tools."))

        self.mill_vdia_entry = FCDoubleSpinner()
        self.mill_vdia_entry.set_range(0.0000, 10000.0000)
        self.mill_vdia_entry.set_precision(self.decimals)
        self.mill_vdia_entry.setObjectName('gdb_vdia')

        self.grid0.addWidget(self.vdia_label, 4, 0)
        self.grid0.addWidget(self.mill_vdia_entry, 4, 1)

        # V-Angle
        self.vangle_label = FCLabel('%s:' % _("V-Angle"))
        self.vangle_label.setToolTip(
            _("V-Agle.\n"
              "Angle at the tip for the V-Shape Tools."))

        self.mill_vangle_entry = FCDoubleSpinner()
        self.mill_vangle_entry.set_range(-360.0, 360.0)
        self.mill_vangle_entry.set_precision(self.decimals)
        self.mill_vangle_entry.setObjectName('gdb_vangle')

        self.grid0.addWidget(self.vangle_label, 6, 0)
        self.grid0.addWidget(self.mill_vangle_entry, 6, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid0.addWidget(separator_line, 8, 0, 1, 2)

        # Tool Type
        self.job_type_lbl = FCLabel('%s:' % _("Job"))
        self.job_type_lbl.setToolTip(
            _(
                "- Isolation -> informative - lower Feedrate as it uses a milling bit with a fine tip.\n"
                "- Roughing  -> informative - lower Feedrate and multiDepth cut.\n"
                "- Finishing -> informative - higher Feedrate, without multiDepth.\n"
                "- Polish -> adds a painting sequence over the whole area of the object"
            )
        )

        self.job_type_combo = FCComboBox2()
        self.job_type_combo.addItems(self.job_item_options)
        self.job_type_combo.setObjectName('gdb_job')

        self.grid0.addWidget(self.job_type_lbl, 10, 0)
        self.grid0.addWidget(self.job_type_combo, 10, 1)

        # Tool Offset
        self.tooloffset_label = FCLabel('%s:' % _('Tool Offset'))
        self.tooloffset_label.setToolTip(
            _("Tool Offset.\n"
              "Can be of a few types:\n"
              "Path = zero offset\n"
              "In = offset inside by half of tool diameter\n"
              "Out = offset outside by half of tool diameter\n"
              "Custom = custom offset using the Custom Offset value"))

        self.mill_tooloffset_type_combo = FCComboBox2()
        self.mill_tooloffset_type_combo.addItems(self.offset_item_options)
        self.mill_tooloffset_type_combo.setObjectName('gdb_tool_offset_type')

        self.grid0.addWidget(self.tooloffset_label, 12, 0)
        self.grid0.addWidget(self.mill_tooloffset_type_combo, 12, 1)

        # Custom Offset
        self.custom_offset_label = FCLabel('%s:' % _("Custom Offset"))
        self.custom_offset_label.setToolTip(
            _("Custom Offset.\n"
              "A value to be used as offset from the current path."))

        self.mill_custom_offset_entry = FCDoubleSpinner()
        self.mill_custom_offset_entry.set_range(-10000.0000, 10000.0000)
        self.mill_custom_offset_entry.set_precision(self.decimals)
        self.mill_custom_offset_entry.setObjectName('gdb_custom_offset')

        self.grid0.addWidget(self.custom_offset_label, 14, 0)
        self.grid0.addWidget(self.mill_custom_offset_entry, 14, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid0.addWidget(separator_line, 16, 0, 1, 2)

        # Cut Z
        self.cutz_label = FCLabel('%s:' % _("Cut Z"))
        self.cutz_label.setToolTip(
            _("Cutting Depth.\n"
              "The depth at which to cut into material."))

        self.mill_cutz_entry = FCDoubleSpinner()
        self.mill_cutz_entry.set_range(-10000.0000, 10000.0000)
        self.mill_cutz_entry.set_precision(self.decimals)
        self.mill_cutz_entry.setObjectName('gdb_cutz')

        self.grid0.addWidget(self.cutz_label, 18, 0)
        self.grid0.addWidget(self.mill_cutz_entry, 18, 1)

        # Multi Depth
        self.multidepth_label = FCLabel('%s:' % _("MultiDepth"))
        self.multidepth_label.setToolTip(
            _("Multi Depth.\n"
              "Selecting this will allow cutting in multiple passes,\n"
              "each pass adding a DPP parameter depth."))

        self.mill_multidepth_cb = FCCheckBox()
        self.mill_multidepth_cb.setObjectName('gdb_multidepth')

        self.grid0.addWidget(self.multidepth_label, 20, 0)
        self.grid0.addWidget(self.mill_multidepth_cb, 20, 1)

        # Depth Per Pass
        self.dpp_label = FCLabel('%s:' % _("Depth per pass"))
        self.dpp_label.setToolTip(
            _("DPP. Depth per Pass.\n"
              "The value used to cut into material on each pass."))

        self.mill_multidepth_entry = FCDoubleSpinner()
        self.mill_multidepth_entry.set_range(-10000.0000, 10000.0000)
        self.mill_multidepth_entry.set_precision(self.decimals)
        self.mill_multidepth_entry.setObjectName('gdb_multidepth_entry')

        self.grid0.addWidget(self.dpp_label, 22, 0)
        self.grid0.addWidget(self.mill_multidepth_entry, 22, 1)

        # Travel Z
        self.travelz_label = FCLabel('%s:' % _("Travel Z"))
        self.travelz_label.setToolTip(
            _("Clearance Height.\n"
              "Height at which the milling bit will travel between cuts,\n"
              "above the surface of the material, avoiding all fixtures."))

        self.mill_travelz_entry = FCDoubleSpinner()
        self.mill_travelz_entry.set_range(-10000.0000, 10000.0000)
        self.mill_travelz_entry.set_precision(self.decimals)
        self.mill_travelz_entry.setObjectName('gdb_travelz')

        self.grid0.addWidget(self.travelz_label, 24, 0)
        self.grid0.addWidget(self.mill_travelz_entry, 24, 1)

        # Extra Cut
        self.ecut_label = FCLabel('%s:' % _("ExtraCut"))
        self.ecut_label.setToolTip(
            _("Extra Cut.\n"
              "If checked, after a isolation is finished an extra cut\n"
              "will be added where the start and end of isolation meet\n"
              "such as that this point is covered by this extra cut to\n"
              "ensure a complete isolation."))

        self.mill_ecut_cb = FCCheckBox()
        self.mill_ecut_cb.setObjectName('gdb_ecut')

        self.grid0.addWidget(self.ecut_label, 26, 0)
        self.grid0.addWidget(self.mill_ecut_cb, 26, 1)

        # Extra Cut Length
        self.ecut_length_label = FCLabel('%s:' % _("E-Cut Length"))
        self.ecut_length_label.setToolTip(
            _("Extra Cut length.\n"
              "If checked, after a isolation is finished an extra cut\n"
              "will be added where the start and end of isolation meet\n"
              "such as that this point is covered by this extra cut to\n"
              "ensure a complete isolation. This is the length of\n"
              "the extra cut."))

        self.mill_ecut_length_entry = FCDoubleSpinner()
        self.mill_ecut_length_entry.set_range(0.0000, 10000.0000)
        self.mill_ecut_length_entry.set_precision(self.decimals)
        self.mill_ecut_length_entry.setObjectName('gdb_ecut_length')

        self.grid0.addWidget(self.ecut_length_label, 28, 0)
        self.grid0.addWidget(self.mill_ecut_length_entry, 28, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid0.addWidget(separator_line, 30, 0, 1, 2)

        # Feedrate X-Y
        self.frxy_label = FCLabel('%s:' % _("Feedrate X-Y"))
        self.frxy_label.setToolTip(
            _("Feedrate X-Y. Feedrate\n"
              "The speed on XY plane used while cutting into material."))

        self.mill_frxy_entry = FCDoubleSpinner()
        self.mill_frxy_entry.set_range(-9910000.0000, 9910000.0000)
        self.mill_frxy_entry.set_precision(self.decimals)
        self.mill_frxy_entry.setObjectName('gdb_frxy')

        self.grid0.addWidget(self.frxy_label, 32, 0)
        self.grid0.addWidget(self.mill_frxy_entry, 32, 1)

        # Feedrate Z
        self.frz_label = FCLabel('%s:' % _("Feedrate Z"))
        self.frz_label.setToolTip(
            _("Feedrate Z\n"
              "The speed on Z plane."))

        self.mill_frz_entry = FCDoubleSpinner()
        self.mill_frz_entry.set_range(-9910000.0000, 9910000.0000)
        self.mill_frz_entry.set_precision(self.decimals)
        self.mill_frz_entry.setObjectName('gdb_frz')

        self.grid0.addWidget(self.frz_label, 34, 0)
        self.grid0.addWidget(self.mill_frz_entry, 34, 1)

        # Feedrate Rapids
        self.frapids_label = FCLabel('%s:' % _("FR Rapids"))
        self.frapids_label.setToolTip(
            _("FR Rapids. Feedrate Rapids\n"
              "Speed used while moving as fast as possible.\n"
              "This is used only by some devices that can't use\n"
              "the G0 g-code command. Mostly 3D printers."))

        self.mill_frapids_entry = FCDoubleSpinner()
        self.mill_frapids_entry.set_range(0.0000, 10000.0000)
        self.mill_frapids_entry.set_precision(self.decimals)
        self.mill_frapids_entry.setObjectName('gdb_frapids')

        self.grid0.addWidget(self.frapids_label, 36, 0)
        self.grid0.addWidget(self.mill_frapids_entry, 36, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid0.addWidget(separator_line, 38, 0, 1, 2)

        # Spindle Spped
        self.spindle_label = FCLabel('%s:' % _("Spindle speed"))
        self.spindle_label.setToolTip(
            _("Spindle Speed.\n"
              "If it's left empty it will not be used.\n"
              "The speed of the spindle in RPM."))

        self.mill_spindle_entry = FCDoubleSpinner()
        self.mill_spindle_entry.set_range(-9910000.0000, 9910000.0000)
        self.mill_spindle_entry.set_precision(self.decimals)
        self.mill_spindle_entry.setObjectName('gdb_spindle')

        self.grid0.addWidget(self.spindle_label, 40, 0)
        self.grid0.addWidget(self.mill_spindle_entry, 40, 1)

        # Dwell
        self.dwell_label = FCLabel('%s:' % _("Dwell"))
        self.dwell_label.setToolTip(
            _("Dwell.\n"
              "Check this if a delay is needed to allow\n"
              "the spindle motor to reach its set speed."))

        self.mill_dwell_cb = FCCheckBox()
        self.mill_dwell_cb.setObjectName('gdb_dwell')

        self.grid0.addWidget(self.dwell_label, 42, 0)
        self.grid0.addWidget(self.mill_dwell_cb, 42, 1)

        # Dwell Time
        self.dwelltime_label = FCLabel('%s:' % _("Dwelltime"))
        self.dwelltime_label.setToolTip(
            _("Dwell Time.\n"
              "A delay used to allow the motor spindle reach its set speed."))

        self.mill_dwelltime_entry = FCDoubleSpinner()
        self.mill_dwelltime_entry.set_range(0.0000, 10000.0000)
        self.mill_dwelltime_entry.set_precision(self.decimals)
        self.mill_dwelltime_entry.setObjectName('gdb_dwelltime')

        self.grid0.addWidget(self.dwelltime_label, 44, 0)
        self.grid0.addWidget(self.mill_dwelltime_entry, 44, 1)

        # ###########################################################################
        # ############### NCC UI form ###############################################
        # ###########################################################################

        self.grid2 = GLay(v_spacing=5, h_spacing=3)
        self.ncc_vlay.addLayout(self.grid2)
        self.ncc_vlay.addStretch()

        # Operation
        op_label = FCLabel('%s:' % _('Operation'))
        op_label.setToolTip(
            _("The 'Operation' can be:\n"
              "- Isolation -> will ensure that the non-copper clearing is always complete.\n"
              "If it's not successful then the non-copper clearing will fail, too.\n"
              "- Clear -> the regular non-copper clearing.")
        )

        self.ncc_op_radio = RadioSet([
            {"label": _("Clear"), "value": "clear"},
            {"label": _("Isolation"), "value": "iso"}
        ], orientation='horizontal', compact=True)
        self.ncc_op_radio.setObjectName("gdb_n_operation")

        self.grid2.addWidget(op_label, 13, 0)
        self.grid2.addWidget(self.ncc_op_radio, 13, 1)

        # Milling Type Radio Button
        self.milling_type_label = FCLabel('%s:' % _('Milling Type'))
        self.milling_type_label.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )

        self.ncc_milling_type_radio = RadioSet([{'label': _('Climb'), 'value': 'cl'},
                                                {'label': _('Conventional'), 'value': 'cv'}])
        self.ncc_milling_type_radio.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )
        self.ncc_milling_type_radio.setObjectName("gdb_n_milling_type")

        self.grid2.addWidget(self.milling_type_label, 14, 0)
        self.grid2.addWidget(self.ncc_milling_type_radio, 14, 1)

        # Overlap Entry
        nccoverlabel = FCLabel('%s:' % _('Overlap'))
        nccoverlabel.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.\n"
              "Adjust the value starting with lower values\n"
              "and increasing it if areas that should be processed are still \n"
              "not processed.\n"
              "Lower values = faster processing, faster execution on CNC.\n"
              "Higher values = slow processing and slow execution on CNC\n"
              "due of too many paths.")
        )
        self.ncc_overlap_entry = FCDoubleSpinner(suffix='%')
        self.ncc_overlap_entry.set_precision(self.decimals)
        self.ncc_overlap_entry.setWrapping(True)
        self.ncc_overlap_entry.setRange(0.000, 99.9999)
        self.ncc_overlap_entry.setSingleStep(0.1)
        self.ncc_overlap_entry.setObjectName("gdb_n_overlap")

        self.grid2.addWidget(nccoverlabel, 15, 0)
        self.grid2.addWidget(self.ncc_overlap_entry, 15, 1)

        # Margin
        nccmarginlabel = FCLabel('%s:' % _('Margin'))
        nccmarginlabel.setToolTip(
            _("Bounding box margin.")
        )
        self.ncc_margin_entry = FCDoubleSpinner()
        self.ncc_margin_entry.set_precision(self.decimals)
        self.ncc_margin_entry.set_range(-10000.0000, 10000.0000)
        self.ncc_margin_entry.setObjectName("gdb_n_margin")

        self.grid2.addWidget(nccmarginlabel, 16, 0)
        self.grid2.addWidget(self.ncc_margin_entry, 16, 1)

        # Method
        methodlabel = FCLabel('%s:' % _('Method'))
        methodlabel.setToolTip(
            _("Algorithm for copper clearing:\n"
              "- Standard: Fixed step inwards.\n"
              "- Seed-based: Outwards from seed.\n"
              "- Line-based: Parallel lines.")
        )

        self.ncc_method_combo = FCComboBox2()
        self.ncc_method_combo.addItems(
            [_("Standard"), _("Seed"), _("Lines"), _("Combo")]
        )
        self.ncc_method_combo.setObjectName("gdb_n_method")

        self.grid2.addWidget(methodlabel, 17, 0)
        self.grid2.addWidget(self.ncc_method_combo, 17, 1)

        # Connect lines
        self.ncc_connect_cb = FCCheckBox('%s' % _("Connect"))
        self.ncc_connect_cb.setObjectName("gdb_n_connect")

        self.ncc_connect_cb.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )
        self.grid2.addWidget(self.ncc_connect_cb, 18, 0)

        # Contour
        self.ncc_contour_cb = FCCheckBox('%s' % _("Contour"))
        self.ncc_contour_cb.setObjectName("gdb_n_contour")

        self.ncc_contour_cb.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.")
        )
        self.grid2.addWidget(self.ncc_contour_cb, 18, 1)

        # ## NCC Offset choice
        self.ncc_choice_offset_cb = FCCheckBox('%s' % _("Offset"))
        self.ncc_choice_offset_cb.setObjectName("gdb_n_offset")

        self.ncc_choice_offset_cb.setToolTip(
            _("If used, it will add an offset to the copper features.\n"
              "The copper clearing will finish to a distance\n"
              "from the copper features.")
        )
        self.grid2.addWidget(self.ncc_choice_offset_cb, 19, 0)

        # ## NCC Offset Entry
        self.ncc_offset_spinner = FCDoubleSpinner()
        self.ncc_offset_spinner.set_range(0.00, 10.00)
        self.ncc_offset_spinner.set_precision(4)
        self.ncc_offset_spinner.setWrapping(True)
        self.ncc_offset_spinner.setObjectName("gdb_n_offset_value")

        units = self.app.app_units.upper()
        if units == 'MM':
            self.ncc_offset_spinner.setSingleStep(0.1)
        else:
            self.ncc_offset_spinner.setSingleStep(0.01)

        self.grid2.addWidget(self.ncc_offset_spinner, 19, 1)

        self.ncc_final_cleanup_cb = FCCheckBox(_("Final cleanup"))
        self.ncc_final_cleanup_cb.setObjectName("gdb_n_final_cleanup")
        self.ncc_final_cleanup_cb.setToolTip(
            _("After clearing, use the smallest selected tool with Final cleanup enabled\n"
              "to remove reachable residual copper. May cross cleared areas, but preserves\n"
              "copper clearance and the clearing boundary. Default: Off.")
        )
        self.grid2.addWidget(self.ncc_final_cleanup_cb, 20, 0, 1, 2)

        # ###########################################################################
        # ############### Paint UI form #############################################
        # ###########################################################################

        self.grid3 = GLay(v_spacing=5, h_spacing=3)
        self.paint_vlay.addLayout(self.grid3)
        self.paint_vlay.addStretch()

        # Overlap
        ovlabel = FCLabel('%s:' % _('Overlap'))
        ovlabel.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.\n"
              "Adjust the value starting with lower values\n"
              "and increasing it if areas that should be processed are still \n"
              "not processed.\n"
              "Lower values = faster processing, faster execution on CNC.\n"
              "Higher values = slow processing and slow execution on CNC\n"
              "due of too many paths.")
        )
        self.paint_overlap_entry = FCDoubleSpinner(suffix='%')
        self.paint_overlap_entry.set_precision(3)
        self.paint_overlap_entry.setWrapping(True)
        self.paint_overlap_entry.setRange(0.0000, 99.9999)
        self.paint_overlap_entry.setSingleStep(0.1)
        self.paint_overlap_entry.setObjectName('gdb_p_overlap')

        self.grid3.addWidget(ovlabel, 1, 0)
        self.grid3.addWidget(self.paint_overlap_entry, 1, 1)

        # Margin
        marginlabel = FCLabel('%s:' % _('Offset'))
        marginlabel.setToolTip(
            _("Distance by which to avoid\n"
              "the edges of the polygon to\n"
              "be painted.")
        )
        self.paint_offset_entry = FCDoubleSpinner()
        self.paint_offset_entry.set_precision(self.decimals)
        self.paint_offset_entry.set_range(-10000.0000, 10000.0000)
        self.paint_offset_entry.setObjectName('gdb_p_offset')

        self.grid3.addWidget(marginlabel, 2, 0)
        self.grid3.addWidget(self.paint_offset_entry, 2, 1)

        # Method
        methodlabel = FCLabel('%s:' % _('Method'))
        methodlabel.setToolTip(
            _("Algorithm for painting:\n"
              "- Standard: Fixed step inwards.\n"
              "- Seed-based: Outwards from seed.\n"
              "- Line-based: Parallel lines.\n"
              "- Laser-lines: Active only for Gerber objects.\n"
              "Will create lines that follow the traces.\n"
              "- Combo: In case of failure a new method will be picked from the above\n"
              "in the order specified.")
        )

        self.paint_method_combo = FCComboBox2()
        self.paint_method_combo.addItems(
            [_("Standard"), _("Seed"), _("Lines"), _("Laser_lines"), _("Combo")]
        )
        idx = self.paint_method_combo.findText(_("Laser_lines"))
        self.paint_method_combo.model().item(idx).setEnabled(False)

        self.paint_method_combo.setObjectName('gdb_p_method')

        self.grid3.addWidget(methodlabel, 7, 0)
        self.grid3.addWidget(self.paint_method_combo, 7, 1)

        # Connect lines
        self.path_connect_cb = FCCheckBox('%s' % _("Connect"))
        self.path_connect_cb.setObjectName('gdb_p_connect')
        self.path_connect_cb.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )

        self.paint_contour_cb = FCCheckBox('%s' % _("Contour"))
        self.paint_contour_cb.setObjectName('gdb_p_contour')
        self.paint_contour_cb.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.")
        )

        self.grid3.addWidget(self.path_connect_cb, 10, 0)
        self.grid3.addWidget(self.paint_contour_cb, 10, 1)

        self.paint_final_cleanup_cb = FCCheckBox(_("Final paint"))
        self.paint_final_cleanup_cb.setObjectName('gdb_p_final_cleanup')
        self.paint_final_cleanup_cb.setToolTip(_(
            "After painting, use the smallest selected tool with Final paint enabled\n"
            "to paint reachable gaps. May overlap previous passes, but keeps the whole\n"
            "tool inside the selected boundary and margins. Default: Off."))
        self.grid3.addWidget(self.paint_final_cleanup_cb, 11, 0, 1, 2)

        # ###########################################################################
        # ############### Isolation UI form #########################################
        # ###########################################################################

        self.grid4 = GLay(v_spacing=5, h_spacing=3)
        self.iso_vlay.addLayout(self.grid4)
        self.iso_vlay.addStretch()

        # Passes
        passlabel = FCLabel('%s:' % _('Passes'))
        passlabel.setToolTip(
            _("Width of the isolation gap in\n"
              "number (integer) of tool widths.")
        )
        self.iso_passes_entry = FCSpinner()
        self.iso_passes_entry.set_range(1, 9999)
        self.iso_passes_entry.setObjectName("gdb_i_passes")

        self.grid4.addWidget(passlabel, 0, 0)
        self.grid4.addWidget(self.iso_passes_entry, 0, 1)

        # Overlap Entry
        overlabel = FCLabel('%s:' % _('Overlap'))
        overlabel.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.")
        )
        self.iso_overlap_entry = FCDoubleSpinner(suffix='%')
        self.iso_overlap_entry.set_precision(self.decimals)
        self.iso_overlap_entry.setWrapping(True)
        self.iso_overlap_entry.set_range(0.0000, 99.9999)
        self.iso_overlap_entry.setSingleStep(0.1)
        self.iso_overlap_entry.setObjectName("gdb_i_overlap")

        self.grid4.addWidget(overlabel, 2, 0)
        self.grid4.addWidget(self.iso_overlap_entry, 2, 1)

        # Milling Type Radio Button
        self.iso_milling_type_label = FCLabel('%s:' % _('Milling Type'))
        self.iso_milling_type_label.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )

        self.iso_milling_type_radio = RadioSet([{'label': _('Climb'), 'value': 'cl'},
                                                {'label': _('Conventional'), 'value': 'cv'}])
        self.iso_milling_type_radio.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )
        self.iso_milling_type_radio.setObjectName("gdb_i_milling_type")

        self.grid4.addWidget(self.iso_milling_type_label, 4, 0)
        self.grid4.addWidget(self.iso_milling_type_radio, 4, 1)

        # Isolation Type
        self.iso_type_label = FCLabel('%s:' % _('Isolation Type'))
        self.iso_type_label.setToolTip(
            _("Choose how the isolation will be executed:\n"
              "- 'Full' -> complete isolation of polygons\n"
              "- 'Ext' -> will isolate only on the outside\n"
              "- 'Int' -> will isolate only on the inside\n"
              "'Exterior' isolation is almost always possible\n"
              "(with the right tool) but 'Interior'\n"
              "isolation can be done only when there is an opening\n"
              "inside of the polygon (e.g polygon is a 'doughnut' shape).")
        )
        self.iso_type_radio = RadioSet([{'label': _('Full'), 'value': 'full'},
                                        {'label': _('Ext'), 'value': 'ext'},
                                        {'label': _('Int'), 'value': 'int'}])
        self.iso_type_radio.setObjectName("gdb_i_iso_type")

        self.grid4.addWidget(self.iso_type_label, 8, 0)
        self.grid4.addWidget(self.iso_type_radio, 8, 1)

        # ###########################################################################
        # ################ DRILLING UI form #########################################
        # ###########################################################################
        self.grid5 = GLay(v_spacing=5, h_spacing=3)
        self.drill_vlay.addLayout(self.grid5)
        self.drill_vlay.addStretch()

        # Cut Z
        self.cutzlabel = FCLabel('%s:' % _('Cut Z'))
        self.cutzlabel.setToolTip(
            _("Drill depth (negative)\n"
              "below the copper surface.")
        )

        self.drill_cutz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_cutz_entry.set_precision(self.decimals)

        self.drill_cutz_entry.set_range(-10000.0000, 10000.0000)

        self.drill_cutz_entry.setSingleStep(0.1)
        self.drill_cutz_entry.setObjectName("gdb_e_cutz")

        self.grid5.addWidget(self.cutzlabel, 4, 0)
        self.grid5.addWidget(self.drill_cutz_entry, 4, 1)

        # Tool Offset
        self.tool_offset_label = FCLabel('%s:' % _('Offset Z'))
        self.tool_offset_label.setToolTip(
            _("Some drill bits (the larger ones) need to drill deeper\n"
              "to create the desired exit hole diameter due of the tip shape.\n"
              "The value here can compensate the Cut Z parameter.")
        )

        self.drill_offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_offset_entry.set_precision(self.decimals)
        self.drill_offset_entry.set_range(-10000.0000, 10000.0000)
        self.drill_offset_entry.setObjectName("gdb_e_offset")

        self.grid5.addWidget(self.tool_offset_label, 6, 0)
        self.grid5.addWidget(self.drill_offset_entry, 6, 1)

        # Multi-Depth
        self.multidepth_drill_label = FCLabel('%s:' % _("MultiDepth"))
        self.multidepth_drill_label.setToolTip(
            _(
                "Use multiple passes to limit\n"
                "the cut depth in each pass. Will\n"
                "cut multiple times until Cut Z is\n"
                "reached."
            )
        )
        self.drill_mpass_cb = FCCheckBox()
        self.drill_mpass_cb.setObjectName("gdb_e_multidepth")

        self.grid5.addWidget(self.multidepth_drill_label, 7, 0)
        self.grid5.addWidget(self.drill_mpass_cb, 7, 1)

        # Depth Per Pass
        self.dpp_drill_label = FCLabel('%s:' % _("Depth per pass"))
        self.dpp_drill_label.setToolTip(
            _("DPP. Depth per Pass.\n"
              "The value used to cut into material on each pass."))
        self.drill_maxdepth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_maxdepth_entry.set_precision(self.decimals)
        self.drill_maxdepth_entry.set_range(0, 10000.0000)
        self.drill_maxdepth_entry.setSingleStep(0.1)

        self.drill_maxdepth_entry.setToolTip(_("Depth of each pass (positive)."))
        self.drill_maxdepth_entry.setObjectName("gdb_e_depthperpass")

        self.grid5.addWidget(self.dpp_drill_label, 8, 0)
        self.grid5.addWidget(self.drill_maxdepth_entry, 8, 1)

        # Travel Z (z_move)
        self.travelzlabel = FCLabel('%s:' % _('Travel Z'))
        self.travelzlabel.setToolTip(
            _("Tool height when travelling\n"
              "across the XY plane.")
        )

        self.drill_travelz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_travelz_entry.set_precision(self.decimals)

        self.drill_travelz_entry.set_range(-10000.0000, 10000.0000)

        self.drill_travelz_entry.setSingleStep(0.1)
        self.drill_travelz_entry.setObjectName("gdb_e_travelz")

        self.grid5.addWidget(self.travelzlabel, 10, 0)
        self.grid5.addWidget(self.drill_travelz_entry, 10, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid5.addWidget(separator_line, 12, 0, 1, 2)

        # Excellon Feedrate Z
        self.frzlabel = FCLabel('%s:' % _('Feedrate Z'))
        self.frzlabel.setToolTip(
            _("Tool speed while drilling\n"
              "(in units per minute).\n"
              "So called 'Plunge' feedrate.\n"
              "This is for linear move G01.")
        )
        self.drill_feedrate_z_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_feedrate_z_entry.set_precision(self.decimals)
        self.drill_feedrate_z_entry.set_range(0.0, 10000.0000)
        self.drill_feedrate_z_entry.setSingleStep(0.1)
        self.drill_feedrate_z_entry.setObjectName("gdb_e_feedratez")

        self.grid5.addWidget(self.frzlabel, 14, 0)
        self.grid5.addWidget(self.drill_feedrate_z_entry, 14, 1)

        # Excellon Rapid Feedrate
        self.feedrate_rapid_label = FCLabel('%s:' % _('Feedrate Rapids'))
        self.feedrate_rapid_label.setToolTip(
            _("Tool speed while drilling\n"
              "(in units per minute).\n"
              "This is for the rapid move G00.\n"
              "It is useful only for Marlin,\n"
              "ignore for any other cases.")
        )
        self.drill_feedrate_rapid_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_feedrate_rapid_entry.set_precision(self.decimals)
        self.drill_feedrate_rapid_entry.set_range(0.0, 910000.0000)
        self.drill_feedrate_rapid_entry.setSingleStep(0.1)
        self.drill_feedrate_rapid_entry.setObjectName("gdb_e_fr_rapid")

        self.grid5.addWidget(self.feedrate_rapid_label, 16, 0)
        self.grid5.addWidget(self.drill_feedrate_rapid_entry, 16, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid5.addWidget(separator_line, 18, 0, 1, 2)

        # Spindlespeed
        self.spindle_label = FCLabel('%s:' % _('Spindle speed'))
        self.spindle_label.setToolTip(
            _("Speed of the spindle\n"
              "in RPM (optional)")
        )

        self.drill_spindlespeed_entry = FCSpinner(callback=self.confirmation_message_int)
        self.drill_spindlespeed_entry.set_range(0, 1000000)
        self.drill_spindlespeed_entry.set_step(100)
        self.drill_spindlespeed_entry.setObjectName("gdb_e_spindlespeed")

        self.grid5.addWidget(self.spindle_label, 20, 0)
        self.grid5.addWidget(self.drill_spindlespeed_entry, 20, 1)

        # Dwell
        self.dwell_drill_label = FCLabel('%s:' % _("Dwell"))
        self.dwell_drill_label.setToolTip(
            _("Dwell.\n"
              "Check this if a delay is needed to allow\n"
              "the spindle motor to reach its set speed."))

        self.drill_dwell_cb = FCCheckBox()
        self.drill_dwell_cb.setObjectName("gdb_e_dwell")

        self.grid5.addWidget(self.dwell_drill_label, 21, 0)
        self.grid5.addWidget(self.drill_dwell_cb, 21, 1)

        # Dwelltime
        self.dwelltime_drill_lbl = FCLabel('%s:' % _('Dwelltime'))
        self.dwelltime_drill_lbl.setToolTip(
            _("Dwell Time.\n"
              "A delay used to allow the motor spindle reach its set speed."))
        self.drill_dwelltime_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_dwelltime_entry.set_precision(self.decimals)
        self.drill_dwelltime_entry.set_range(0.0, 10000.0000)
        self.drill_dwelltime_entry.setSingleStep(0.1)
        self.drill_dwelltime_entry.setObjectName("gdb_e_dwelltime")

        self.grid5.addWidget(self.dwelltime_drill_lbl, 22, 0)
        self.grid5.addWidget(self.drill_dwelltime_entry, 22, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid5.addWidget(separator_line, 24, 0, 1, 2)

        # Drill slots
        self.drill_slots_drill_lbl = FCLabel('%s:' % _('Drill slots'))
        self.drill_slots_drill_lbl.setToolTip(
            _("If the selected tool has slots then they will be drilled.")
        )
        self.drill_slots_cb = FCCheckBox()
        self.drill_slots_cb.setObjectName("gdb_e_drill_slots")

        self.grid5.addWidget(self.drill_slots_drill_lbl, 26, 0,)
        self.grid5.addWidget(self.drill_slots_cb, 26, 1)

        # Drill Overlap
        self.drill_overlap_label = FCLabel('%s:' % _('Overlap'))
        self.drill_overlap_label.setToolTip(
            _("How much (percentage) of the tool diameter to overlap previous drill hole.")
        )

        self.drill_overlap_entry = FCDoubleSpinner(suffix='%', callback=self.confirmation_message)
        self.drill_overlap_entry.set_precision(self.decimals)
        self.drill_overlap_entry.set_range(0.0, 100.0000)
        self.drill_overlap_entry.setSingleStep(0.1)

        self.drill_overlap_entry.setObjectName("gdb_e_drill_slots_over")

        self.grid5.addWidget(self.drill_overlap_label, 28, 0)
        self.grid5.addWidget(self.drill_overlap_entry, 28, 1)

        # Last drill in slot
        self.last_drill_drill_lbl = FCLabel('%s:' % _('Last drill'))
        self.last_drill_drill_lbl.setToolTip(
            _("If the slot length is not completely covered by drill holes,\n"
              "add a drill hole on the slot end point.")
        )

        self.drill_last_drill_cb = FCCheckBox()
        self.drill_last_drill_cb.setObjectName("gdb_e_drill_last_drill")

        self.grid5.addWidget(self.last_drill_drill_lbl, 30, 0, 1, 2)
        self.grid5.addWidget(self.drill_last_drill_cb, 30, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid5.addWidget(separator_line, 32, 0, 1, 2)

        # Dwell at the bottom of the holes
        self.dwell_bottom_drill_lbl = FCLabel('%s:' % _('Dwell at bottom'))
        self.dwell_bottom_drill_lbl.setToolTip(
            _("Pause at the bottom of every hole, for the Dwelltime.\n"
              "Ignored by the laser preprocessors.")
        )
        self.drill_dwell_bottom_cb = FCCheckBox()
        self.drill_dwell_bottom_cb.setObjectName("gdb_e_dwell_bottom")

        self.grid5.addWidget(self.dwell_bottom_drill_lbl, 33, 0)
        self.grid5.addWidget(self.drill_dwell_bottom_cb, 33, 1)

        # Peck drilling
        self.peck_drill_lbl = FCLabel('%s:' % _('Peck drilling'))
        self.peck_drill_lbl.setToolTip(
            _("Drill each hole in steps. After each step the tool goes up to the\n"
              "Travel Z (it clears the chips), goes down fast to the Peck Retract\n"
              "clearance above the previous depth and feeds on.\n"
              "It replaces the Multi-Depth. Ignored by the laser preprocessors.")
        )
        self.drill_peck_cb = FCCheckBox()
        self.drill_peck_cb.setObjectName("gdb_e_peck")

        self.grid5.addWidget(self.peck_drill_lbl, 34, 0)
        self.grid5.addWidget(self.drill_peck_cb, 34, 1)

        self.peck_depth_drill_lbl = FCLabel('%s:' % _('Peck depth'))
        self.peck_depth_drill_lbl.setToolTip(_("Depth of each peck step (positive)."))
        self.drill_peck_depth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_peck_depth_entry.set_precision(self.decimals)
        self.drill_peck_depth_entry.set_range(0.0, 10000.0000)
        self.drill_peck_depth_entry.setSingleStep(0.1)
        self.drill_peck_depth_entry.setObjectName("gdb_e_peck_depth")

        self.grid5.addWidget(self.peck_depth_drill_lbl, 35, 0)
        self.grid5.addWidget(self.drill_peck_depth_entry, 35, 1)

        self.peck_retract_drill_lbl = FCLabel('%s:' % _('Peck retract'))
        self.peck_retract_drill_lbl.setToolTip(
            _("Clearance above the previous depth to which the tool goes down fast\n"
              "after the full retract, before it feeds on.")
        )
        self.drill_peck_retract_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.drill_peck_retract_entry.set_precision(self.decimals)
        self.drill_peck_retract_entry.set_range(0.0, 10000.0000)
        self.drill_peck_retract_entry.setSingleStep(0.1)
        self.drill_peck_retract_entry.setObjectName("gdb_e_peck_retract")

        self.grid5.addWidget(self.peck_retract_drill_lbl, 36, 0)
        self.grid5.addWidget(self.drill_peck_retract_entry, 36, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.grid5.addWidget(separator_line, 38, 0, 1, 2)

        # Spindle direction
        self.spindledir_drill_lbl = FCLabel('%s:' % _('Spindle direction'))
        self.spindledir_drill_lbl.setToolTip(
            _("The direction that the spindle is rotating.\n"
              "- CW = clockwise or\n"
              "- CCW = counter clockwise")
        )
        self.drill_spindledir_radio = RadioSet([{'label': _('CW'), 'value': 'CW'},
                                                {'label': _('CCW'), 'value': 'CCW'}], compact=True)
        self.drill_spindledir_radio.setObjectName("gdb_e_spindledir")

        self.grid5.addWidget(self.spindledir_drill_lbl, 39, 0)
        self.grid5.addWidget(self.drill_spindledir_radio, 39, 1)

        # Fast plunge
        self.fplunge_drill_lbl = FCLabel('%s:' % _('Fast Plunge'))
        self.fplunge_drill_lbl.setToolTip(
            _("The vertical move from Z_Toolchange to Z_move is done with G0,\n"
              "meaning the fastest speed available.\n"
              "WARNING: the move is done at Toolchange X,Y coords.")
        )
        self.drill_fplunge_cb = FCCheckBox()
        self.drill_fplunge_cb.setObjectName("gdb_e_f_plunge")

        self.grid5.addWidget(self.fplunge_drill_lbl, 40, 0)
        self.grid5.addWidget(self.drill_fplunge_cb, 40, 1)

        # Fast retract
        self.fretract_drill_lbl = FCLabel('%s:' % _('Fast Retract'))
        self.fretract_drill_lbl.setToolTip(
            _("Exit hole strategy.\n"
              " - When unchecked the drill bit travels slow (G1) up to zero depth and then\n"
              "as fast as possible (G0) to the Z Move (travel height).\n"
              " - When checked the travel from Z cut to Z_move is done as fast as possible (G0).")
        )
        self.drill_fretract_cb = FCCheckBox()
        self.drill_fretract_cb.setObjectName("gdb_e_f_retract")

        self.grid5.addWidget(self.fretract_drill_lbl, 41, 0)
        self.grid5.addWidget(self.drill_fretract_cb, 41, 1)

        # ###########################################################################
        # ################### Cutout UI form ########################################
        # ###########################################################################
        self.grid6 = GLay(v_spacing=5, h_spacing=3)
        self.cutout_vlay.addLayout(self.grid6)
        self.cutout_vlay.addStretch()

        # Margin
        self.cutout_margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutout_margin_entry.set_range(-10000.0000, 10000.0000)
        self.cutout_margin_entry.setSingleStep(0.1)
        self.cutout_margin_entry.set_precision(self.decimals)
        self.cutout_margin_entry.setObjectName('gdb_ct_margin')

        self.cutout_margin_label = FCLabel('%s:' % _("Margin"))
        self.cutout_margin_label.setToolTip(
            _("Margin over bounds. A positive value here\n"
              "will make the cutout of the PCB further from\n"
              "the actual PCB border")
        )
        self.grid6.addWidget(self.cutout_margin_label, 11, 0)
        self.grid6.addWidget(self.cutout_margin_entry, 11, 1)

        # Gapsize
        self.cutout_gapsize = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutout_gapsize.set_precision(self.decimals)
        self.cutout_gapsize.setObjectName('gdb_ct_gapsize')

        self.cutout_gapsize_label = FCLabel('%s:' % _("Gap size"))
        self.cutout_gapsize_label.setToolTip(
            _("The size of the bridge gaps in the cutout\n"
              "used to keep the board connected to\n"
              "the surrounding material (the one \n"
              "from which the PCB is cutout).")
        )
        self.grid6.addWidget(self.cutout_gapsize_label, 13, 0)
        self.grid6.addWidget(self.cutout_gapsize, 13, 1)

        # Gap Type
        self.gaptype_label = FCLabel('%s:' % _("Gap type"))
        self.gaptype_label.setToolTip(
            _("The type of gap:\n"
              "- Bridge -> the cutout will be interrupted by bridges\n"
              "- Thin -> same as 'bridge' but it will be thinner by partially milling the gap\n"
              "- M-Bites -> 'Mouse Bites' - same as 'bridge' but covered with drill holes")
        )

        self.cutout_gaptype_radio = RadioSet(
            [
                {'label': _('Bridge'), 'value': 0},
                {'label': _('Thin'), 'value': 1},
                {'label': "M-Bites", 'value': 2}
            ],
            compact=True
        )
        self.cutout_gaptype_radio.setObjectName('gdb_ct_gap_type')

        self.grid6.addWidget(self.gaptype_label, 15, 0)
        self.grid6.addWidget(self.cutout_gaptype_radio, 15, 1)

        # Thin gaps Depth
        self.thin_depth_label = FCLabel('%s:' % _("Depth"))
        self.thin_depth_label.setToolTip(
            _("The depth until the milling is done\n"
              "in order to thin the gaps.")
        )
        self.cutout_thin_depth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutout_thin_depth_entry.set_precision(self.decimals)
        self.cutout_thin_depth_entry.setObjectName('gdb_ct_gap_depth')

        self.cutout_thin_depth_entry.setRange(-10000.0000, 10000.0000)
        self.cutout_thin_depth_entry.setSingleStep(0.1)

        self.grid6.addWidget(self.thin_depth_label, 17, 0)
        self.grid6.addWidget(self.cutout_thin_depth_entry, 17, 1)

        # Mouse Bites Tool Diameter
        self.mb_dia_label = FCLabel('%s:' % _("Tool Diameter"))
        self.mb_dia_label.setToolTip(
            _("The drill hole diameter when doing mouse bites.")
        )
        self.cutout_mb_dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutout_mb_dia_entry.set_precision(self.decimals)
        self.cutout_mb_dia_entry.setRange(0, 100.0000)
        self.cutout_mb_dia_entry.setObjectName('gdb_ct_mb_dia')

        self.grid6.addWidget(self.mb_dia_label, 19, 0)
        self.grid6.addWidget(self.cutout_mb_dia_entry, 19, 1)

        # Mouse Bites Holes Spacing
        self.mb_spacing_label = FCLabel('%s:' % _("Spacing"))
        self.mb_spacing_label.setToolTip(
            _("The spacing between drill holes when doing mouse bites.")
        )
        self.cutout_mb_spacing_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutout_mb_spacing_entry.set_precision(self.decimals)
        self.cutout_mb_spacing_entry.setRange(0, 100.0000)
        self.cutout_mb_spacing_entry.setObjectName('gdb_ct_mb_spacing')

        self.grid6.addWidget(self.mb_spacing_label, 21, 0)
        self.grid6.addWidget(self.cutout_mb_spacing_entry, 21, 1)
        
        # How gaps wil be rendered:
        # lr    - left + right
        # tb    - top + bottom
        # 4     - left + right +top + bottom
        # 2lr   - 2*left + 2*right
        # 2tb   - 2*top + 2*bottom
        # 8     - 2*left + 2*right +2*top + 2*bottom

        # Surrounding convex box shape
        self.cutout_convex_box = FCCheckBox('%s' % _("Convex Shape"))
        # self.convex_box_label = FCLabel('%s' % _("Convex Sh."))
        self.cutout_convex_box.setToolTip(
            _("Create a convex shape surrounding the entire PCB.\n"
              "Used only if the source object type is Gerber.")
        )
        self.cutout_convex_box.setObjectName('gdb_ct_convex')

        self.grid6.addWidget(self.cutout_convex_box, 23, 0, 1, 2)

        # Gaps
        self.cutout_gaps_label = FCLabel('%s:' % _('Gaps'))
        self.cutout_gaps_label.setToolTip(
            _("Number of gaps used for the Automatic cutout.\n"
              "There can be maximum 8 bridges/gaps.\n"
              "The choices are:\n"
              "- None  - no gaps\n"
              "- lr    - left + right\n"
              "- tb    - top + bottom\n"
              "- 4     - left + right +top + bottom\n"
              "- 2lr   - 2*left + 2*right\n"
              "- 2tb  - 2*top + 2*bottom\n"
              "- 8     - 2*left + 2*right +2*top + 2*bottom")
        )

        self.cutout_gaps = FCComboBox()
        gaps_items = ['None', 'LR', 'TB', '4', '2LR', '2TB', '8']
        self.cutout_gaps.addItems(gaps_items)
        self.cutout_gaps.setObjectName('gdb_ct_gaps')

        self.grid6.addWidget(self.cutout_gaps_label, 25, 0)
        self.grid6.addWidget(self.cutout_gaps, 25, 1)

        # ####################################################################
        # ####################################################################
        # GUI for the lower part of the window
        # ####################################################################
        # ####################################################################

        new_vlay = QtWidgets.QVBoxLayout()
        self.g_lay.addLayout(new_vlay, 1, 0, 1, 2)

        self.buttons_frame = QtWidgets.QFrame()
        self.buttons_frame.setContentsMargins(0, 0, 0, 0)
        new_vlay.addWidget(self.buttons_frame)
        self.buttons_box = QtWidgets.QHBoxLayout()
        self.buttons_box.setContentsMargins(0, 0, 0, 0)
        self.buttons_frame.setLayout(self.buttons_box)
        self.buttons_frame.show()

        self.add_entry_btn = FCButton(_("Add"))
        self.add_entry_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/plus16.png'))
        self.add_entry_btn.setToolTip(
            _("Add a new tool in the Tools Database.\n"
              "It will be used in the Geometry UI.\n"
              "You can edit it after it is added.")
        )
        self.buttons_box.addWidget(self.add_entry_btn)

        self.copy_entry_btn = FCButton(_("Duplicate"))
        self.copy_entry_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/copy16.png'))
        self.buttons_box.addWidget(self.copy_entry_btn)

        # add_fct_entry_btn = FCButton(_("Add Paint/NCC Tool in DB"))
        # add_fct_entry_btn.setToolTip(
        #     _("Add a new tool in the Tools Database.\n"
        #       "It will be used in the Paint/NCC Tools UI.\n"
        #       "You can edit it after it is added.")
        # )
        # self.buttons_box.addWidget(add_fct_entry_btn)

        self.remove_entry_btn = FCButton(_("Delete"))
        self.remove_entry_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/trash16.png'))
        self.remove_entry_btn.setToolTip(
            _("Remove a selection of tools in the Tools Database.")
        )
        self.buttons_box.addWidget(self.remove_entry_btn)

        self.export_db_btn = FCButton(_("Export"))
        self.export_db_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/export.png'))
        self.export_db_btn.setToolTip(
            _("Save the Tools Database to a custom text file.")
        )
        self.buttons_box.addWidget(self.export_db_btn)

        self.import_db_btn = FCButton(_("Import"))
        self.import_db_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/import.png'))
        self.import_db_btn.setToolTip(
            _("Load the Tools Database information's from a custom text file.")
        )
        self.buttons_box.addWidget(self.import_db_btn)

        self.save_db_btn = FCButton(_("Save"))
        self.save_db_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.save_db_btn.setToolTip(
            _("Save the Tools Database information's.")
        )
        self.buttons_box.addWidget(self.save_db_btn)

        self.add_tool_from_db = FCButton(_("Add selected tools"), bold=True, color='green')
        self.add_tool_from_db.setToolTip(
            _("Insert a new tool in the Tools Table of the\n"
              "object/application tool after selecting a tool\n"
              "in the Tools Database.")
        )
        self.add_tool_from_db.hide()

        self.cancel_tool_from_db = FCButton(_("Cancel"))
        self.cancel_tool_from_db.hide()

        hlay = QtWidgets.QHBoxLayout()
        tree_layout.addLayout(hlay)
        hlay.addWidget(self.add_tool_from_db)
        hlay.addWidget(self.cancel_tool_from_db)
        self.count_label = FCLabel('')
        self.count_label.setWordWrap(True)
        tree_layout.addWidget(self.count_label)
        self.status_label = FCLabel('')
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                        QtWidgets.QSizePolicy.Policy.Maximum)
        self.status_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        new_vlay.addWidget(self.status_label)
        # hlay.addStretch()
        # ############################ FINSIHED GUI ###################################
        # #############################################################################

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
        

class ToolsDB2(QtWidgets.QWidget):

    mark_tools_rows = QtCore.pyqtSignal()

    def __init__(self, app, callback_on_tool_request, parent=None):
        super(ToolsDB2, self).__init__(parent)

        self.app = app
        self.app_ui = self.app.ui
        self.decimals = self.app.decimals

        self.on_tool_request = callback_on_tool_request
        self.library_units = self.app.app_units
        self.library_defaults = deepcopy(dict(_database_option_items(self.app.options)))

        self.app.tools_db_changed_flag = False

        '''
        dict to hold all the tools in the Tools DB
        format:
        {
            tool_id: {
                'name': 'new_tool'
                'tooldia': self.app.options["tools_mill_tooldia"]
                'data': dict()
            }
        }
        '''
        self.db_tool_dict = {}

        self.old_color = QtGui.QColor('black')

        # ##############################################################################
        # ##############################################################################
        # TOOLS DATABASE UI
        # ##############################################################################
        # ##############################################################################
        layout = GLay(v_spacing=5, h_spacing=3)
        self.setLayout(layout)
        self.ui = ToolsDB2UI(app=self.app, grid_layout=layout)

        # ##############################################################################
        # ##############################################################################
        # ########## SETUP THE DICTIONARIES THAT HOLD THE WIDGETS #####################
        # ##############################################################################
        # ##############################################################################

        self.form_fields = {
            "tool_target":      self.ui.tool_op_combo,
            "tol_min":          self.ui.tol_min_entry,
            "tol_max":          self.ui.tol_max_entry,
            "name":             self.ui.name_entry,
            "tooldia":          self.ui.dia_entry,

            # Milling
            "tools_mill_tool_shape":       self.ui.mill_shape_combo,
            "tools_mill_cutz":             self.ui.mill_cutz_entry,
            "tools_mill_multidepth":       self.ui.mill_multidepth_cb,
            "tools_mill_depthperpass":     self.ui.mill_multidepth_entry,
            "tools_mill_travelz":          self.ui.mill_travelz_entry,
            "tools_mill_feedrate":         self.ui.mill_frxy_entry,
            "tools_mill_feedrate_z":       self.ui.mill_frz_entry,
            "tools_mill_spindlespeed":     self.ui.mill_spindle_entry,
            "tools_mill_dwell":            self.ui.mill_dwell_cb,
            "tools_mill_dwelltime":        self.ui.mill_dwelltime_entry,

            "tools_mill_job_type":         self.ui.job_type_combo,
            "tools_mill_offset_type":      self.ui.mill_tooloffset_type_combo,
            "tools_mill_offset_value":     self.ui.mill_custom_offset_entry,
            "tools_mill_vtipdia":          self.ui.mill_vdia_entry,
            "tools_mill_vtipangle":        self.ui.mill_vangle_entry,
            "tools_mill_feedrate_rapid":   self.ui.mill_frapids_entry,
            "tools_mill_extracut":         self.ui.mill_ecut_cb,
            "tools_mill_extracut_length":  self.ui.mill_ecut_length_entry,

            # NCC
            "tools_ncc_operation":      self.ui.ncc_op_radio,
            "tools_ncc_milling_type":   self.ui.ncc_milling_type_radio,
            "tools_ncc_overlap":        self.ui.ncc_overlap_entry,
            "tools_ncc_margin":         self.ui.ncc_margin_entry,
            "tools_ncc_method":         self.ui.ncc_method_combo,
            "tools_ncc_connect":        self.ui.ncc_connect_cb,
            "tools_ncc_contour":        self.ui.ncc_contour_cb,
            "tools_ncc_final_cleanup":  self.ui.ncc_final_cleanup_cb,
            "tools_ncc_offset_choice":  self.ui.ncc_choice_offset_cb,
            "tools_ncc_offset_value":   self.ui.ncc_offset_spinner,

            # Paint
            "tools_paint_overlap":      self.ui.paint_overlap_entry,
            "tools_paint_offset":       self.ui.paint_offset_entry,
            "tools_paint_method":       self.ui.paint_method_combo,
            "tools_paint_connect":      self.ui.path_connect_cb,
            "tools_paint_contour":      self.ui.paint_contour_cb,
            "tools_paint_final_cleanup": self.ui.paint_final_cleanup_cb,

            # Isolation
            "tools_iso_passes":         self.ui.iso_passes_entry,
            "tools_iso_overlap":        self.ui.iso_overlap_entry,
            "tools_iso_milling_type":   self.ui.iso_milling_type_radio,
            "tools_iso_isotype":        self.ui.iso_type_radio,

            # Drilling
            "tools_drill_cutz":             self.ui.drill_cutz_entry,
            "tools_drill_multidepth":       self.ui.drill_mpass_cb,
            "tools_drill_depthperpass":     self.ui.drill_maxdepth_entry,
            "tools_drill_travelz":          self.ui.drill_travelz_entry,
            "tools_drill_feedrate_z":       self.ui.drill_feedrate_z_entry,

            "tools_drill_feedrate_rapid":   self.ui.drill_feedrate_rapid_entry,
            "tools_drill_spindlespeed":     self.ui.drill_spindlespeed_entry,
            "tools_drill_dwell":            self.ui.drill_dwell_cb,
            "tools_drill_dwelltime":        self.ui.drill_dwelltime_entry,

            "tools_drill_offset":           self.ui.drill_offset_entry,
            "tools_drill_drill_slots":      self.ui.drill_slots_cb,
            "tools_drill_drill_overlap":    self.ui.drill_overlap_entry,
            "tools_drill_last_drill":       self.ui.drill_last_drill_cb,
            "tools_drill_dwell_bottom":     self.ui.drill_dwell_bottom_cb,
            "tools_drill_peck":             self.ui.drill_peck_cb,
            "tools_drill_peck_depth":       self.ui.drill_peck_depth_entry,
            "tools_drill_peck_retract":     self.ui.drill_peck_retract_entry,
            "tools_drill_spindledir":       self.ui.drill_spindledir_radio,
            "tools_drill_f_plunge":         self.ui.drill_fplunge_cb,
            "tools_drill_f_retract":        self.ui.drill_fretract_cb,

            # Cutout
            "tools_cutout_margin":          self.ui.cutout_margin_entry,
            "tools_cutout_gapsize":         self.ui.cutout_gapsize,
            "tools_cutout_gaps_ff":         self.ui.cutout_gaps,
            "tools_cutout_convexshape":     self.ui.cutout_convex_box,

            "tools_cutout_gap_type":        self.ui.cutout_gaptype_radio,
            "tools_cutout_gap_depth":       self.ui.cutout_thin_depth_entry,
            "tools_cutout_mb_dia":          self.ui.cutout_mb_dia_entry,
            "tools_cutout_mb_spacing":      self.ui.cutout_mb_spacing_entry,

        }

        self.name2option = {
            "gdb_tool_target":      "tool_target",
            "gdb_tol_min":          "tol_min",
            "gdb_tol_max":          "tol_max",

            "gdb_name":             "name",
            "gdb_dia":              "tooldia",

            # Milling
            "gdb_shape":            "tools_mill_tool_shape",
            "gdb_cutz":             "tools_mill_cutz",
            "gdb_multidepth":       "tools_mill_multidepth",
            "gdb_multidepth_entry": "tools_mill_depthperpass",
            "gdb_travelz":          "tools_mill_travelz",
            "gdb_frxy":             "tools_mill_feedrate",
            "gdb_frz":              "tools_mill_feedrate_z",
            "gdb_spindle":          "tools_mill_spindlespeed",
            "gdb_dwell":            "tools_mill_dwell",
            "gdb_dwelltime":        "tools_mill_dwelltime",

            "gdb_job":              "tools_mill_job_type",
            "gdb_tool_offset_type": "tools_mill_offset_type",
            "gdb_custom_offset":    "tools_mill_offset_value",
            "gdb_vdia":             "tools_mill_vtipdia",
            "gdb_vangle":           "tools_mill_vtipangle",
            "gdb_frapids":          "tools_mill_feedrate_rapid",
            "gdb_ecut":             "tools_mill_extracut",
            "gdb_ecut_length":      "tools_mill_extracut_length",

            # NCC
            "gdb_n_operation":      "tools_ncc_operation",
            "gdb_n_overlap":        "tools_ncc_overlap",
            "gdb_n_margin":         "tools_ncc_margin",
            "gdb_n_method":         "tools_ncc_method",
            "gdb_n_connect":        "tools_ncc_connect",
            "gdb_n_contour":        "tools_ncc_contour",
            "gdb_n_final_cleanup":  "tools_ncc_final_cleanup",
            "gdb_n_offset":         "tools_ncc_offset_choice",
            "gdb_n_offset_value":   "tools_ncc_offset_value",
            "gdb_n_milling_type":   "tools_ncc_milling_type",

            # Paint
            'gdb_p_overlap':        "tools_paint_overlap",
            'gdb_p_offset':         "tools_paint_offset",
            'gdb_p_method':         "tools_paint_method",
            'gdb_p_connect':        "tools_paint_connect",
            'gdb_p_contour':        "tools_paint_contour",
            'gdb_p_final_cleanup':  "tools_paint_final_cleanup",

            # Isolation
            "gdb_i_passes":         "tools_iso_passes",
            "gdb_i_overlap":        "tools_iso_overlap",
            "gdb_i_milling_type":   "tools_iso_milling_type",
            "gdb_i_iso_type":       "tools_iso_isotype",

            # Drilling
            "gdb_e_cutz":               "tools_drill_cutz",
            "gdb_e_multidepth":         "tools_drill_multidepth",
            "gdb_e_depthperpass":       "tools_drill_depthperpass",
            "gdb_e_travelz":            "tools_drill_travelz",

            "gdb_e_feedratez":          "tools_drill_feedrate_z",
            "gdb_e_fr_rapid":           "tools_drill_feedrate_rapid",
            "gdb_e_spindlespeed":       "tools_drill_spindlespeed",
            "gdb_e_dwell":              "tools_drill_dwell",
            "gdb_e_dwelltime":          "tools_drill_dwelltime",

            "gdb_e_offset":             "tools_drill_offset",
            "gdb_e_drill_slots":        "tools_drill_drill_slots",
            "gdb_e_drill_slots_over":   "tools_drill_drill_overlap",
            "gdb_e_drill_last_drill":   "tools_drill_last_drill",
            "gdb_e_dwell_bottom":       "tools_drill_dwell_bottom",
            "gdb_e_peck":               "tools_drill_peck",
            "gdb_e_peck_depth":         "tools_drill_peck_depth",
            "gdb_e_peck_retract":       "tools_drill_peck_retract",
            "gdb_e_spindledir":         "tools_drill_spindledir",
            "gdb_e_f_plunge":           "tools_drill_f_plunge",
            "gdb_e_f_retract":          "tools_drill_f_retract",

            # Cutout
            "gdb_ct_margin":            "tools_cutout_margin",
            "gdb_ct_gapsize":           "tools_cutout_gapsize",
            "gdb_ct_gaps":              "tools_cutout_gaps_ff",
            "gdb_ct_convex":            "tools_cutout_convexshape",

            "gdb_ct_gap_type":          "tools_cutout_gap_type",
            "gdb_ct_gap_depth":         "tools_cutout_gap_depth",
            "gdb_ct_mb_dia":            "tools_cutout_mb_dia",
            "gdb_ct_mb_spacing":        "tools_cutout_mb_spacing"

        }

        self.current_toolid = None
        self.context_target = None
        self._revision = None
        self._loaded_ids = set()
        self._raw_records = {}
        self.model = ToolLibraryModel(self)
        self.proxy = ToolLibraryFilter(self)
        self.proxy.setSourceModel(self.model)
        self.ui.tree_widget.setModel(self.proxy)
        self.ui.tree_widget.setColumnHidden(0, True)
        self.ui.tree_widget.setSortingEnabled(True)
        self.ui.tree_widget.sortByColumn(1, QtCore.Qt.SortOrder.AscendingOrder)
        header = self.ui.tree_widget.header()
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.apply_filters)
        self.ui.search_entry.textChanged.connect(lambda: self._search_timer.start())
        self.ui.filter_combo.currentIndexChanged.connect(self.apply_filters)
        self.ui.properties_combo.currentIndexChanged.connect(self.on_tool_target_changed)
        self._setup_property_details()

        # variable to show if double clicking and item will trigger adding a tool from DB
        self.ok_to_add = False

        # ##############################################################################
        # ######################## SIGNALS #############################################
        # ##############################################################################

        self.ui.add_entry_btn.clicked.connect(self.on_tool_add)
        self.ui.copy_entry_btn.clicked.connect(self.on_tool_copy)
        self.ui.remove_entry_btn.clicked.connect(self.on_tool_delete)
        self.ui.export_db_btn.clicked.connect(self.on_export_tools_db_file)
        self.ui.import_db_btn.clicked.connect(self.on_import_tools_db_file)
        self.ui.save_db_btn.clicked.connect(self.on_save_db_btn_click)
        # closebtn.clicked.connect(self.accept)

        self.ui.add_tool_from_db.clicked.connect(self.on_tool_requested_from_app)
        self.ui.cancel_tool_from_db.clicked.connect(self.on_cancel_tool)

        # self.ui.tree_widget.selectionModel().selectionChanged.connect(self.on_list_selection_change)
        self.ui.tree_widget.selectionModel().currentChanged.connect(self.on_list_selection_change)
        self.ui.tree_widget.selectionModel().selectionChanged.connect(self.update_selection_state)
        self.ui.tree_widget.customContextMenuRequested.connect(self.on_menu_request)
        self.ui.tree_widget.doubleClicked.connect(self.on_item_double_clicked)

        self.ui.tool_op_combo.currentIndexChanged.connect(self.on_tool_target_changed)

        self._db_load_valid = False
        self._close_preapproved = False
        self._close_decision = None
        self.setup_db_ui()

    def _setup_property_details(self):
        self.ui.grid0.removeWidget(self.ui.shape_label)
        self.ui.grid0.removeWidget(self.ui.mill_shape_combo)
        self.ui.grid_tool.addWidget(self.ui.shape_label, 9, 0)
        self.ui.grid_tool.addWidget(self.ui.mill_shape_combo, 9, 1)
        advanced = {
            'tol_min', 'tol_max', 'tools_mill_job_type', 'tools_mill_offset_type',
            'tools_mill_offset_value', 'tools_mill_feedrate_rapid', 'tools_mill_dwell',
            'tools_mill_dwelltime', 'tools_mill_extracut', 'tools_mill_extracut_length',
            'tools_drill_feedrate_rapid', 'tools_drill_dwell', 'tools_drill_dwelltime',
            'tools_drill_offset', 'tools_drill_drill_slots', 'tools_drill_drill_overlap',
            'tools_drill_last_drill', 'tools_drill_dwell_bottom', 'tools_drill_peck',
            'tools_drill_peck_depth', 'tools_drill_peck_retract', 'tools_drill_spindledir',
            'tools_drill_f_plunge', 'tools_drill_f_retract'
        }
        self._detail_rows = {}

        def find_row(layout, widget):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item.widget() is widget and isinstance(layout, QtWidgets.QGridLayout):
                    row = layout.getItemPosition(index)[0]
                    return [layout.itemAt(i).widget() for i in range(layout.count())
                            if layout.getItemPosition(i)[0] == row and layout.itemAt(i).widget()]
                if item.layout():
                    found = find_row(item.layout(), widget)
                    if found:
                        return found
            return []

        groups = (self.ui.tool_description_box, self.ui.milling_box, self.ui.drill_box)
        for key in advanced | {'tools_mill_vtipdia', 'tools_mill_vtipangle',
                               'tools_mill_depthperpass', 'tools_drill_depthperpass'}:
            widget = self.form_fields[key]
            for box in groups:
                row_widgets = find_row(box.layout(), widget)
                if row_widgets:
                    self._detail_rows[key] = row_widgets
                    break
        self.ui.advanced_cb.toggled.connect(self.update_property_visibility)
        self.ui.mill_shape_combo.currentIndexChanged.connect(self.update_property_visibility)
        self.ui.mill_multidepth_cb.toggled.connect(self.update_property_visibility)
        self.ui.drill_mpass_cb.toggled.connect(self.update_property_visibility)
        for box in groups:
            for frame in box.findChildren(QtWidgets.QFrame):
                if frame.frameShape() == QtWidgets.QFrame.Shape.HLine:
                    frame.hide()
        units = self.app.app_units.lower()
        lengths = ('tooldia', 'tol_min', 'tol_max', 'tools_mill_cutz', 'tools_mill_depthperpass',
                   'tools_mill_travelz', 'tools_mill_offset_value', 'tools_mill_vtipdia',
                   'tools_mill_extracut_length', 'tools_drill_cutz', 'tools_drill_depthperpass',
                   'tools_drill_travelz', 'tools_drill_offset', 'tools_ncc_margin',
                   'tools_ncc_offset_value', 'tools_paint_offset', 'tools_cutout_margin',
                   'tools_cutout_gapsize', 'tools_cutout_gap_depth', 'tools_cutout_mb_dia',
                   'tools_cutout_mb_spacing')
        for key in lengths:
            self.form_fields[key].setSuffix(' ' + units)
        for key, widget in self.form_fields.items():
            if 'feedrate' in key:
                widget.setSuffix(' %s/min' % units)
            if key.endswith('spindlespeed'):
                widget.setSuffix(' rpm')
        self.update_property_visibility()

    def update_property_visibility(self, *args):
        for key, widgets in self._detail_rows.items():
            if key in ('tools_mill_vtipdia', 'tools_mill_vtipangle'):
                visible = self.ui.mill_shape_combo.get_value() == 5
            elif key == 'tools_mill_depthperpass':
                visible = self.ui.mill_multidepth_cb.isChecked()
            elif key == 'tools_drill_depthperpass':
                visible = self.ui.drill_mpass_cb.isChecked()
            else:
                visible = self.ui.advanced_cb.isChecked()
            for widget in widgets:
                widget.setVisible(visible)
        self.ui.tol_label.setVisible(self.ui.advanced_cb.isChecked())

    def selected_tool_ids(self):
        return [str(index.data()) for index in self.ui.tree_widget.selectionModel().selectedRows(0)
                if str(index.data()) in self.db_tool_dict]

    def ensure_tools_loaded(self, tool_ids):
        """Load the stored properties; a bad record stays in the file untouched and is marked as skipped."""
        pending = {key for key in tool_ids if key not in self._loaded_ids and key not in self.model.skipped}
        loaded = self.parse_raw_records(pending)
        self.db_tool_dict.update(loaded)
        self._loaded_ids.update(loaded)
        for key in pending - set(loaded):
            self.model.update_tool(key)
        skipped = [key for key in tool_ids if key in self.model.skipped]
        if skipped:
            self.ui.status_label.setText(_("Skipped tool %s: %s") % (skipped[0], self.model.skipped[skipped[0]]))
        return not skipped

    def parse_raw_records(self, tool_ids):
        records = {}
        skipped = {}
        for key in tool_ids:
            try:
                records[key] = json.loads(self._raw_records[key])
            except (KeyError, TypeError, ValueError) as error:
                skipped[key] = str(error)
        records = normalize_tools_database(records, self.library_defaults, skipped=skipped)
        for key, reason in skipped.items():
            self.app.log.error('Tools database: skipped record %s: %s' % (key, reason))
        self.model.skipped.update(skipped)
        return records

    def all_tool_records(self):
        records = {key: tool for key, tool in self.db_tool_dict.items() if key in self._loaded_ids}
        records.update(self.parse_raw_records(set(self.db_tool_dict) - set(records) - set(self.model.skipped)))
        return {key: records[key] for key in self.db_tool_dict if key in records}

    def select_tool(self, tool_id):
        row = self.model.rows.get(str(tool_id))
        if row is None:
            return False
        index = self.proxy.mapFromSource(self.model.index(row, 0))
        if not index.isValid():
            return False
        selection = self.ui.tree_widget.selectionModel()
        selection.setCurrentIndex(index, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect |
                                  QtCore.QItemSelectionModel.SelectionFlag.Rows)
        self.ui.tree_widget.scrollTo(index)
        self.on_list_selection_change(index, QtCore.QModelIndex())
        return True

    def apply_filters(self, *args):
        self._search_timer.stop()
        target = self.context_target if self.context_target is not None else self.ui.filter_combo.currentData()
        self.proxy.set_filters(self.ui.search_entry.text(), target)
        selection = self.ui.tree_widget.selectionModel()
        if not selection.selectedRows() and self.proxy.rowCount():
            self.select_tool(str(self.proxy.index(0, 0).data()))
        self.update_selection_state()

    def update_selection_state(self, *args):
        selected = self.selected_tool_ids()
        count = len(selected)
        self.ui.param_area.setEnabled(count == 1 and selected[0] not in self.model.skipped)
        self.ui.copy_entry_btn.setEnabled(count > 0)
        self.ui.remove_entry_btn.setEnabled(count > 0)
        self.ui.add_tool_from_db.setEnabled(count > 0 and self._db_load_valid)
        self.ui.count_label.setText(_('%d shown / %d tools · %d selected') %
                                   (self.proxy.rowCount(), len(self.db_tool_dict), count))
        if not self.db_tool_dict:
            self.ui.context_label.setText(_("Empty library — add a tool or import a database"))
        else:
            self.ui.context_label.setText(
                _("Select tools for %s") % operation_names()[self.context_target]
                if self.context_target is not None else _("Tool library"))

    def set_context(self, source='app'):
        targets = {'app': None, 'milling': 1, 'drill': 2, 'iso': 3, 'paint': 4, 'ncc': 5, 'cutout': 6}
        self.context_target = targets[source]
        self.ok_to_add = self.context_target is not None
        self.ui.buttons_frame.setVisible(not self.ok_to_add)
        self.ui.filter_combo.setEnabled(not self.ok_to_add)
        self.ui.add_tool_from_db.setVisible(self.ok_to_add)
        self.ui.cancel_tool_from_db.setVisible(self.ok_to_add)
        for widget in self.form_fields.values():
            if isinstance(widget, (FCEntry, FCSpinner, FCDoubleSpinner)):
                widget.setReadOnly(self.ok_to_add)
            else:
                widget.setEnabled(not self.ok_to_add)
        self.ui.context_label.setText(
            _("Select tools for %s") % operation_names()[self.context_target] if self.ok_to_add else _("Tool library"))
        with QtCore.QSignalBlocker(self.ui.filter_combo):
            self.ui.filter_combo.setCurrentIndex(self.ui.filter_combo.findData(self.context_target))
        self.ui.search_entry.clear()
        self.apply_filters()
        self.on_tool_target_changed()
        if self._db_load_valid:
            self.ui.status_label.setText(_("Units: %s · %s") % (self.library_units, self.app.tools_database_path()))

    def on_menu_request(self, pos):

        menu = QtWidgets.QMenu()

        sort_by_id = menu.addAction(QtGui.QIcon(self.app.resource_location + '/desc_sort32.png'), _("Sort by Target"))
        sort_by_id.triggered.connect(self.on_sort_target)

        sort_by_dia = menu.addAction(QtGui.QIcon
                                     (self.app.resource_location + '/desc_sort32.png'), _("Sort by Diameter"))
        sort_by_dia.triggered.connect(self.on_sort_dia)

        if self.context_target is not None:
            menu.exec(self.ui.tree_widget.viewport().mapToGlobal(pos))
            return

        menu.addSeparator()

        add_tool = menu.addAction(QtGui.QIcon(self.app.resource_location + '/plus16.png'), _("Add to DB"))
        add_tool.triggered.connect(self.on_tool_add)

        copy_tool = menu.addAction(QtGui.QIcon(self.app.resource_location + '/copy16.png'), _("Copy from DB"))
        copy_tool.triggered.connect(self.on_tool_copy)

        delete_tool = menu.addAction(QtGui.QIcon(self.app.resource_location + '/delete32.png'), _("Delete from DB"))
        delete_tool.triggered.connect(self.on_tool_delete)

        menu.addSeparator()

        save_changes = menu.addAction(QtGui.QIcon(self.app.resource_location + '/save_as.png'), _("Save changes"))
        save_changes.triggered.connect(self.on_save_changes)

        replace_library = menu.addAction(_("Replace library from file…"))
        replace_library.triggered.connect(lambda: self.on_import_tools_db_file(replace=True))

        # tree_item = self.ui.tree_widget.itemAt(pos)
        menu.exec(self.ui.tree_widget.viewport().mapToGlobal(pos))

    def on_save_changes(self):
        return self.on_save_tools_db()

    def on_item_double_clicked(self, index):
        if self.ok_to_add:
            self.on_tool_requested_from_app()

    def on_list_selection_change(self, current, previous):
        if not current.isValid():
            return
        tool_id = str(current.siblingAtColumn(0).data())
        if tool_id not in self.db_tool_dict:
            return
        if not self.ensure_tools_loaded([tool_id]):
            self.update_selection_state()
            return
        self.ui_disconnect()
        self.current_toolid = int(tool_id)
        self.storage_to_form(self.db_tool_dict[tool_id])
        self.on_tool_target_changed(val=self.db_tool_dict[tool_id]['data']['tool_target'])
        self.ui_connect()

    def on_sort_target(self):
        self.ui.tree_widget.sortByColumn(2, QtCore.Qt.SortOrder.AscendingOrder)

    def on_sort_dia(self):
        self.ui.tree_widget.sortByColumn(3, QtCore.Qt.SortOrder.AscendingOrder)

    def storage_to_form(self, dict_storage):
        self.ui_disconnect()
        values = dict(dict_storage['data'])
        values.update(name=dict_storage['name'], tooldia=dict_storage['tooldia'])
        for key, widget in self.form_fields.items():
            if key in values:
                widget.set_value(values[key])
        self.ui_connect()

    def form_to_storage(self, tool):
        self.ui_disconnect()

        widget_changed = self.sender()
        wdg_objname = widget_changed.objectName()
        option_changed = self.name2option[wdg_objname]
        tooluid_item = int(tool)

        for tooluid_key, tooluid_val in self.db_tool_dict.items():
            if int(tooluid_key) == tooluid_item:
                new_option_value = self.form_fields[option_changed].get_value()
                if option_changed in tooluid_val:
                    tooluid_val[option_changed] = new_option_value
                if option_changed in tooluid_val['data']:
                    tooluid_val['data'][option_changed] = new_option_value
        self.ui_connect()

    def _set_db_load_controls(self, enabled):
        for widget in (self.ui.save_db_btn, self.ui.export_db_btn, self.ui.add_tool_from_db):
            widget.setEnabled(enabled)
        self.ui.import_db_btn.setEnabled(True)

    def setup_db_ui(self):

        # set the old color for the Tools Database Tab
        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.tabText(idx) == _("Tools Database"):
                self.old_color = self.app.ui.plot_tab_area.tabBar.tabTextColor(idx)

        filename = self.app.tools_database_path()

        try:
            if Path(filename).suffix.lower() == '.sqlite3':
                candidate, self._raw_records, metadata = read_library(filename, units=self.app.app_units)
                self._loaded_ids = set()
                self._revision = int(metadata['revision'])
            else:
                candidate = load_tools_database(filename, self.library_defaults, units=self.library_units)
                self._loaded_ids = set(candidate)
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % _("Could not load the file."))
            self.ui.status_label.setText(str(error))
            self._set_db_load_controls(False)
            return

        self.db_tool_dict = candidate
        self.model.skipped.clear()
        self._db_load_valid = True
        self._set_db_load_controls(True)

        self.app.inform.emit('[success] %s: %s' % (_("Loaded Tools DB from"), filename))

        self.build_db_ui()

    def build_db_ui(self):
        self.ui_disconnect()
        selected_id = str(self.current_toolid) if self.current_toolid is not None else None
        with QtCore.QSignalBlocker(self.ui.tree_widget.selectionModel()):
            self.model.sync(self.db_tool_dict)
            self.apply_filters()
            if not self.select_tool(selected_id) and self.proxy.rowCount():
                self.select_tool(str(self.proxy.index(0, 0).data()))
        self.update_selection_state()
        self.ui_connect()

    def on_tool_target_changed(self, index=None, val=None):
        tool_target = self.ui.tool_op_combo.get_value() if val is None else val
        general = tool_target == 0 and self.context_target is None
        self.ui.properties_label.setVisible(general)
        self.ui.properties_combo.setVisible(general)
        target = self.context_target or tool_target or self.ui.properties_combo.currentData()
        for operation, box in ((1, self.ui.milling_box), (2, self.ui.drill_box),
                               (3, self.ui.iso_box), (4, self.ui.paint_box),
                               (5, self.ui.ncc_box), (6, self.ui.cutout_box)):
            box.setVisible(operation == target or (operation == 1 and target != 2))
        self.update_tree_target()

    def on_tool_add(self):
        """
        Add a tool in the DB Tool Table
        :return: None
        """

        if self.context_target is not None:
            return
        if self.library_units != self.app.app_units:
            self.ui.status_label.setText(_("Application units changed. Reopen the tool library before adding tools."))
            return
        target = self.ui.filter_combo.currentData() or 0
        default_data = {key: deepcopy(value) for key, value in self.library_defaults.items()
                        if key.startswith(OPERATION_PREFIXES[target])}
        default_data.update(plot=True, tool_target=target, tol_min=0.0, tol_max=0.0,
                            seg_x=_database_option(self.app, 'geometry_seg_x'),
                            seg_y=_database_option(self.app, 'geometry_seg_y'))

        temp = []
        for k, v in self.db_tool_dict.items():
            name = v.get('name', '')
            if name.startswith('new_tool_'):
                try:
                    temp.append(int(name[len('new_tool_'):]))
                except ValueError:
                    pass

        if temp:
            new_name = "new_tool_%d" % int(max(temp) + 1)
        else:
            new_name = "new_tool_1"

        dict_elem = {'name': new_name}
        tool_diameter = _database_option(self.app, "tools_mill_tooldia")
        if isinstance(tool_diameter, (int, float)) and not isinstance(tool_diameter, bool):
            dict_elem['tooldia'] = tool_diameter
        else:
            try:
                tools_string = tool_diameter.split(",")
                tools_diameters = [safe_eval(a) for a in tools_string if a != '']
                dict_elem['tooldia'] = tools_diameters[0] if tools_diameters else 0.0
            except Exception as e:
                self.app.log.error("ToolDB.on_tool_add() --> %s" % str(e))
                return

        dict_elem['data'] = default_data
        default_data['tool_target'] = self.context_target or self.ui.filter_combo.currentData() or 0

        new_toolid = max((int(key) for key in self.db_tool_dict), default=0) + 1
        self.db_tool_dict[str(new_toolid)] = normalize_tools_database(
            {str(new_toolid): dict_elem}, self.library_defaults)[str(new_toolid)]
        self._loaded_ids.add(str(new_toolid))
        self.current_toolid = new_toolid

        # add the new entry to the Tools DB table
        self.ui.search_entry.clear()
        self.build_db_ui()

        self.select_tool(str(new_toolid))
        self.on_tool_target_changed(val=dict_elem['data']['tool_target'])
        self.ui.name_entry.setFocus()
        self.ui.name_entry.selectAll()
        self.on_tools_db_edited()
        self.app.inform.emit('[success] %s' % _("Tool added to DB."))

    def on_tool_copy(self):
        """
        Copy a selection of Tools in the Tools DB table
        :return:
        """
        if self.context_target is not None:
            return
        selected_items = self.selected_tool_ids()
        if not selected_items:
            return
        self.ensure_tools_loaded(selected_items)

        new_tool_id = max((int(key) for key in self.db_tool_dict), default=0)
        copied = False
        for old_tool_id in selected_items:
            if old_tool_id not in self._loaded_ids:
                continue
            new_tool_id += 1
            self.db_tool_dict[str(new_tool_id)] = deepcopy(self.db_tool_dict[old_tool_id])
            self._loaded_ids.add(str(new_tool_id))
            copied = True

        if not copied:
            return

        self.current_toolid = new_tool_id

        self.build_db_ui()
        self.on_tools_db_edited()
        self.app.inform.emit('[success] %s' % _("Tool copied from Tools DB."))

    def on_tool_delete(self):
        """
        Delete a selection of Tools in the Tools DB table
        :return:
        """
        if self.context_target is not None:
            return
        selected_items = self.selected_tool_ids()
        if not selected_items:
            return

        removed = False
        for tool_id in selected_items:
            if tool_id in self.db_tool_dict:
                self.db_tool_dict.pop(tool_id)
                removed = True

        if not removed:
            return
        if str(self.current_toolid) not in self.db_tool_dict:
            self.current_toolid = None

        self.build_db_ui()

        self.on_tools_db_edited()
        self.app.inform.emit('[success] %s' % _("Tool removed from Tools DB."))

    def on_export_tools_db_file(self):
        self.app.defaults.report_usage("on_export_tools_db_file")
        self.app.log.debug("on_export_tools_db_file()")

        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        filter__ = _("Tools library (*.sqlite3);;JSON tools (*.FlatDB *.json *.TXT);;All Files (*.*)")

        filename, _f = FCFileSaveDialog.get_saved_filename(
            caption=_("Export Tools Database"),
            directory='{l_save}/FlatCAM_{n}_{date}'.format(l_save=str(self.app.get_last_save_folder()),
                                                           n=_("Tools_Database"),
                                                           date=date),
            ext_filter=filter__)

        filename = str(filename)

        if filename == "":
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return False
        if not Path(filename).suffix:
            filename += '.json' if '*.json' in _f else '.sqlite3'
        if not self._db_load_valid:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Tools DB is not valid."))
            return False

        try:
            candidate = normalize_tools_database(self.all_tool_records(), self.library_defaults)
            write_tools_database(filename, candidate, units=self.library_units)
        except Exception as error:
            self.app.log.error("App.on_export_tools_db_file() --> %s" % str(error))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed to write Tools DB to file."))
            return False

        self.app.inform.emit('[success] %s: %s' % (_("Exported Tools DB to"), filename))
        if self.model.skipped:
            self.ui.status_label.setText(_("Export skipped %d bad tools.") % len(self.model.skipped))
        return True

    def on_import_tools_db_file(self, replace=False):
        self.app.defaults.report_usage("on_import_tools_db_file")
        self.app.log.debug("on_import_tools_db_file()")

        filter__ = _("Tools library (*.sqlite3 *.FlatDB *.json *.TXT);;All Files (*.*)")
        filename, _f = QtWidgets.QFileDialog.getOpenFileName(
            caption=_("Import tools (legacy files use %s)") % self.library_units, filter=filter__)

        if filename == "":
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return False

        try:
            candidate = load_tools_database(filename, self.library_defaults, units=self.library_units)
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load Tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % _("Failed to load Tools DB file."))
            self.ui.status_label.setText(str(error))
            return False

        if replace and self.app.tools_db_changed_flag and not self.confirm_close():
            return False
        if not replace:
            # Tools that are already in the library, under any ID, are not added again.
            existing = list(self.all_tool_records().values())
            merged = deepcopy(self.db_tool_dict)
            imported_ids = set()
            new_id = max((int(key) for key in set(merged) | set(candidate)), default=0)
            for key, tool in candidate.items():
                if tool in existing:
                    continue
                if key in merged:
                    new_id += 1
                    key = str(new_id)
                merged[key] = tool
                imported_ids.add(key)
            candidate = merged
            self._loaded_ids.update(imported_ids)
        else:
            self._loaded_ids = set(candidate)
            self._raw_records = {}
            self.model.skipped.clear()
        self.db_tool_dict = candidate
        self.current_toolid = None
        self._db_load_valid = True
        self._set_db_load_controls(True)
        with QtCore.QSignalBlocker(self.ui.search_entry), QtCore.QSignalBlocker(self.ui.filter_combo):
            self.ui.search_entry.clear()
            self.ui.filter_combo.setCurrentIndex(0)
        self.build_db_ui()
        self.on_tools_db_edited()
        self.app.inform.emit('[success] %s: %s' % (_("Loaded Tools DB from"), filename))
        return True

    def on_save_tools_db(self, silent=False):
        self.app.log.debug("ToolsDB.on_save_button() --> Saving Tools Database to file.")

        filename = self.app.tools_database_path()
        if not self._db_load_valid:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Tools DB is not valid."))
            return False

        try:
            if self.library_units != self.app.app_units:
                raise ValueError(_("Application units changed. Reopen the tool library before saving it."))
            candidate = normalize_tools_database(
                {key: tool for key, tool in self.db_tool_dict.items() if key in self._loaded_ids},
                self.library_defaults)
            revision = write_tools_database(filename, candidate, units=self.app.app_units,
                                            expected_revision=self._revision, retained_ids=self.db_tool_dict)
        except Exception as error:
            self.app.log.error("ToolsDB.on_save_tools_db() --> %s" % str(error))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed to write Tools DB to file."))
            self.ui.status_label.setText(str(error))
            return False

        self.db_tool_dict.update(candidate)
        self._revision = revision
        self.model.sync(self.db_tool_dict)
        self.app.tools_db_changed_flag = False
        self.ui.status_label.setText(_("Saved · Units: %s · %s") % (self.app.app_units, filename))
        self.ui.save_db_btn.restore_stylesheet()
        for idx in range(self.app_ui.plot_tab_area.count()):
            if self.app_ui.plot_tab_area.tabText(idx) == _("Tools Database"):
                self.app_ui.plot_tab_area.tabBar.setTabTextColor(idx, self.old_color)

        if not silent:
            self.app.inform.emit('[success] %s' % _("Saved Tools DB."))
        return True

    def on_save_db_btn_click(self):
        return self.on_save_tools_db()

    def on_calculate_tooldia(self):
        if self.ui.mill_shape_combo.get_value() == 5:
            tip_dia = float(self.ui.mill_vdia_entry.get_value())
            half_tip_angle = float(self.ui.mill_vangle_entry.get_value()) / 2.0
            cut_z = float(self.ui.mill_cutz_entry.get_value())
            cut_z = -cut_z if cut_z < 0 else cut_z

            # calculated tool diameter so the cut_z parameter is obeyed
            tool_dia = tip_dia + (2 * cut_z * math.tan(math.radians(half_tip_angle)))

            self.ui.dia_entry.set_value(tool_dia)

    def ui_connect(self):
        # make sure that we don't make multiple connections to the widgets
        self.ui_disconnect()

        self.ui.name_entry.editingFinished.connect(self.update_tree_name)
        self.ui.dia_entry.editingFinished.connect(self.update_tree_tooldia)

        for key in self.form_fields:
            wdg = self.form_fields[key]

            # FCEntry
            if isinstance(wdg, FCEntry):
                wdg.textChanged.connect(self.update_storage)

            # ComboBox
            if isinstance(wdg, FCComboBox):
                wdg.currentIndexChanged.connect(self.update_storage)

            # CheckBox
            if isinstance(wdg, FCCheckBox):
                wdg.toggled.connect(self.update_storage)

            # FCRadio
            if isinstance(wdg, RadioSet):
                wdg.activated_custom.connect(self.update_storage)

            # SpinBox, DoubleSpinBox
            if isinstance(wdg, FCSpinner) or isinstance(wdg, FCDoubleSpinner):
                wdg.valueChanged.connect(self.update_storage)

        # connect the calculate tooldia method to the controls
        # if the tool shape is 'V' the tool dia will be calculated to obey Cut Z parameter
        self.ui.mill_shape_combo.currentIndexChanged.connect(self.on_calculate_tooldia)
        self.ui.mill_cutz_entry.valueChanged.connect(self.on_calculate_tooldia)
        self.ui.mill_vdia_entry.valueChanged.connect(self.on_calculate_tooldia)
        self.ui.mill_vangle_entry.valueChanged.connect(self.on_calculate_tooldia)

    def ui_disconnect(self):
        try:
            self.ui.name_entry.editingFinished.disconnect(self.update_tree_name)
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.dia_entry.editingFinished.disconnect(self.update_tree_tooldia)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.mill_shape_combo.currentIndexChanged.disconnect(self.on_calculate_tooldia)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.mill_cutz_entry.valueChanged.disconnect(self.on_calculate_tooldia)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.mill_vdia_entry.valueChanged.disconnect(self.on_calculate_tooldia)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.mill_vangle_entry.valueChanged.disconnect(self.on_calculate_tooldia)
        except (TypeError, AttributeError):
            pass

        for key in self.form_fields:
            wdg = self.form_fields[key]

            # FCEntry
            if isinstance(wdg, FCEntry):
                try:
                    wdg.textChanged.disconnect(self.update_storage)
                except (TypeError, AttributeError):
                    pass

            # ComboBox
            if isinstance(wdg, FCComboBox):
                try:
                    wdg.currentIndexChanged.disconnect(self.update_storage)
                except (TypeError, AttributeError):
                    pass

            # CheckBox
            if isinstance(wdg, FCCheckBox):
                try:
                    wdg.toggled.disconnect(self.update_storage)
                except (TypeError, AttributeError):
                    pass

            # FCRadio
            if isinstance(wdg, RadioSet):
                try:
                    wdg.activated_custom.disconnect(self.update_storage)
                except (TypeError, AttributeError):
                    pass

            # SpinBox, DoubleSpinBox
            if isinstance(wdg, FCSpinner) or isinstance(wdg, FCDoubleSpinner):
                try:
                    wdg.valueChanged.disconnect(self.update_storage)
                except (TypeError, AttributeError):
                    pass

    def update_tree_name(self):
        self.model.update_tool(self.current_toolid)

    def update_tree_target(self):
        self.model.update_tool(self.current_toolid)

    def update_tree_tooldia(self):
        self.model.update_tool(self.current_toolid)

    def update_storage(self):
        """
        Update the dictionary that is the storage of the tools 'database'
        :return:
        """
        wdg = self.sender()
        if wdg is None:
            return
        if isinstance(wdg, FCButton) or isinstance(wdg, QtGui.QAction):
            self.on_tools_db_edited()
            return
        if not isinstance(wdg, QtWidgets.QWidget):
            return

        option = self.name2option.get(wdg.objectName())
        tool_id = str(self.current_toolid)
        if option is None or tool_id not in self.db_tool_dict:
            return

        tool = self.db_tool_dict[tool_id]
        storage = tool if option in ('name', 'tooldia') else tool['data']
        selected_items = self.selected_tool_ids()
        if len(selected_items) != 1:
            with QtCore.QSignalBlocker(wdg):
                wdg.set_value(storage[option])
            wdg.clearFocus()
            if len(selected_items) > 1:
                msg = '[ERROR_NOTCL] %s: %s' % \
                      (_("To change tool properties select only one tool. Tools currently selected"),
                       str(len(selected_items)))
                self.app.inform.emit(msg)
            return

        value = wdg.get_value()
        if storage.get(option) != value:
            storage[option] = value
            if option == 'tool_target':
                for key, default in self.library_defaults.items():
                    if key.startswith(OPERATION_PREFIXES[value]):
                        tool['data'].setdefault(key, deepcopy(default))
                self.storage_to_form(tool)
            if option not in ('name', 'tooldia'):
                self.model.update_tool(tool_id)
            self.on_tools_db_edited()

    def _close_database_container(self):
        try:
            plot_area = self.app_ui.plot_tab_area
        except (AttributeError, RuntimeError):
            return False

        try:
            for idx in range(plot_area.count()):
                try:
                    if plot_area.widget(idx) is self and plot_area.tabText(idx) == _("Tools Database"):
                        close_tab = getattr(plot_area, 'closeTab', None)
                        return callable(close_tab) and close_tab(idx) is not False
                except (AttributeError, RuntimeError):
                    continue
        except (AttributeError, RuntimeError):
            pass

        try:
            detached_tabs = plot_area.detachedTabs
        except (AttributeError, RuntimeError):
            return False

        for name, detached_window in list(detached_tabs.items()):
            try:
                if detached_window.contentWidget is not self:
                    continue
                if not self.confirm_close():
                    return False
                if detached_window.close() is False:
                    return False

                self._close_preapproved = True
                try:
                    for idx in range(plot_area.count()):
                        if plot_area.widget(idx) is self and plot_area.tabText(idx) == _("Tools Database"):
                            close_tab = getattr(plot_area, 'closeTab', None)
                            return callable(close_tab) and close_tab(idx) is not False
                finally:
                    self._close_preapproved = False
                return False
            except (AttributeError, RuntimeError):
                try:
                    del detached_tabs[name]
                except (KeyError, RuntimeError):
                    pass
        return False

    def on_tool_requested_from_app(self):
        if not self._db_load_valid:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Tools DB is not valid."))
            return False
        if not self.selected_tool_ids():
            self.app.inform.emit('[WARNING_NOTCL] %s...' % _("No Tool/row selected in the Tools Database table"))
            return False

        if not self.db_tool_dict:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Tools DB empty."))
            return False

        try:
            if self.library_units != self.app.app_units:
                raise ValueError(_("Application units changed. Reopen the tool library before using it."))
            if not self.ensure_tools_loaded(self.selected_tool_ids()):
                return False
            selected_tools = list(normalize_tools_database(
                {key: self.db_tool_dict[key] for key in self.selected_tool_ids()}, self.library_defaults).values())
            if self.context_target is not None:
                for tool in selected_tools:
                    if tool['data']['tool_target'] not in (0, self.context_target):
                        raise ValueError(_("Selected tool isn't compatible with this operation."))
                    tool['data'] = {key: value for key, value in tool['data'].items()
                                    if not key.startswith('tools_') or key.startswith(OPERATION_PREFIXES[self.context_target])}
        except (TypeError, ValueError) as error:
            self.ui.status_label.setText(str(error))
            return False

        if not selected_tools:
            return False

        dirty_before_request = bool(self.app.tools_db_changed_flag)
        close_decision = None
        if dirty_before_request:
            if not self.confirm_close():
                return False
            close_decision = self._close_decision

        try:
            for index, selected_tool in enumerate(selected_tools):
                try:
                    result = self.on_tool_request(tool=selected_tool)
                except Exception as error:
                    self.app.log.error("ToolsDB2.on_tool_requested_from_app() --> %s" % str(error))
                    result = 'fail'
                if result in ('fail', False):
                    if close_decision == 'discarded' and dirty_before_request:
                        self.app.tools_db_changed_flag = True
                    if index:
                        self.app.inform.emit('[ERROR_NOTCL] %s' %
                                             _("Tool request partially completed; the database remains open."))
                    return False

            self._close_preapproved = True
            try:
                closed = self._close_database_container()
            except Exception as error:
                self.app.log.error("ToolsDB2.on_tool_requested_from_app() --> %s" % str(error))
                closed = False
            if not closed and close_decision == 'discarded' and dirty_before_request:
                self.app.tools_db_changed_flag = True
            return closed
        finally:
            self._close_preapproved = False
            self._close_decision = None

    def on_tools_db_edited(self, silent=None):
        """
        Executed whenever a tool is edited in Tools Database.
        Will color the text of the Tools Database tab to Red color.

        :return:
        """

        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.tabText(idx) == _("Tools Database"):
                self.app.ui.plot_tab_area.tabBar.setTabTextColor(idx, QtGui.QColor('red'))

        self.ui.save_db_btn.color = 'red'

        was_dirty = self.app.tools_db_changed_flag
        self.app.tools_db_changed_flag = True
        self.ui.status_label.setText(_("Unsaved changes · Units: %s") % self.library_units)
        if silent is None and not was_dirty:
            msg = '[WARNING_NOTCL] %s' % _("Tools in Tools Database edited but not saved.")
            self.app.inform[str, bool].emit(msg, False)

    def confirm_close(self):
        if self._close_preapproved:
            self._close_preapproved = False
            return True
        self._close_decision = None
        if not self.app.tools_db_changed_flag:
            self._close_decision = 'clean'
            return True

        msgbox = FCMessageBox(parent=self.app_ui)
        title = _("Save Tools Database")
        msgbox.setWindowTitle(title)
        msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText(_("One or more Tools are edited.\nDo you want to save?"))
        msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/save_as.png'))

        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        bt_cancel = msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msgbox.setDefaultButton(bt_yes)
        msgbox.exec()
        response = msgbox.clickedButton()

        if response == bt_yes:
            saved = self.on_save_tools_db()
            self._close_decision = 'saved' if saved else 'save_failed'
            return saved
        if response == bt_no:
            self.app.tools_db_changed_flag = False
            self._close_decision = 'discarded'
            return True
        if response == bt_cancel:
            self._close_decision = 'cancelled'
            return False
        self._close_decision = 'cancelled'
        return False

    def on_cancel_tool(self):
        if self._close_database_container():
            self.app.inform.emit('%s' % _("Cancelled adding tool from DB."))
            return True
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = (QtCore.Qt.Orientation.Vertical if event.size().width() < 850
                       else QtCore.Qt.Orientation.Horizontal)
        if self.ui.splitter.orientation() != orientation:
            self.ui.splitter.setOrientation(orientation)
            self.ui.splitter.setSizes([220, max(260, event.size().height() - 260)] if
                                      orientation == QtCore.Qt.Orientation.Vertical else [400, 600])

    # def resize_new_plugin_table_widget(self, min_size, max_size):
    #     """
    #     Resize the table widget responsible for adding new tool in the Tool Database
    #
    #     :param min_size: passed by rangeChanged signal or the self.new_plugin_table_widget.horizontalScrollBar()
    #     :param max_size: passed by rangeChanged signal or the self.new_plugin_table_widget.horizontalScrollBar()
    #     :return:
    #     """
    #     t_height = self.t_height
    #     if max_size > min_size:
    #         t_height = self.t_height + self.new_plugin_table_widget.verticalScrollBar().height()
    #
    #     self.new_plugin_table_widget.setMaximumHeight(t_height)

    def closeEvent(self, QCloseEvent):
        super().closeEvent(QCloseEvent)


# class ToolsDB(QtWidgets.QWidget):
#
#     mark_tools_rows = QtCore.pyqtSignal()
#
#     def __init__(self, app, callback_on_edited, callback_on_tool_request, parent=None):
#         super(ToolsDB, self).__init__(parent)
#
#         self.app = app
#         self.decimals = 4
#         self.callback_app = callback_on_edited
#
#         self.on_tool_request = callback_on_tool_request
#
#         self.offset_item_options = ["Path", "In", "Out", "Custom"]
#         self.type_item_options = ["Iso", "Rough", "Finish"]
#         self.tool_type_item_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]
#
#         '''
#         dict to hold all the tools in the Tools DB
#         format:
#         {
#             tool_id: {
#                 'name': 'new_tool'
#                 'tooldia': self.app.options["tools_mill_tooldia"]
#                 'offset': 'Path'
#                 'offset_value': 0.0
#                 'type':  'Rough',
#                 'tool_type': 'C1'
#                 'data': dict()
#             }
#         }
#         '''
#         self.db_tool_dict = {}
#
#         # layouts
#         layout = QtWidgets.QVBoxLayout()
#         self.setLayout(layout)
#
#         table_hlay = QtWidgets.QHBoxLayout()
#         layout.addLayout(table_hlay)
#
#         self.table_widget = FCTable(drag_drop=True)
#         self.table_widget.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
#         table_hlay.addWidget(self.table_widget)
#
#         # set the number of columns and the headers tool tips
#         self.configure_table()
#
#         # pal = QtGui.QPalette()
#         # pal.setColor(QtGui.QPalette.Background, Qt.white)
#
#         # New Bookmark
#         new_vlay = QtWidgets.QVBoxLayout()
#         layout.addLayout(new_vlay)
#
#         # new_tool_lbl = FCLabel('<b>%s</b>' % _("New Tool"))
#         # new_vlay.addWidget(new_tool_lbl, alignment=QtCore.Qt.AlignBottom)
#
#         self.buttons_frame = QtWidgets.QFrame()
#         self.buttons_frame.setContentsMargins(0, 0, 0, 0)
#         layout.addWidget(self.buttons_frame)
#         self.buttons_box = QtWidgets.QHBoxLayout()
#         self.buttons_box.setContentsMargins(0, 0, 0, 0)
#         self.buttons_frame.setLayout(self.buttons_box)
#         self.buttons_frame.show()
#
#         add_entry_btn = FCButton(_("Add Geometry Tool in DB"))
#         add_entry_btn.setToolTip(
#             _("Add a new tool in the Tools Database.\n"
#               "It will be used in the Geometry UI.\n"
#               "You can edit it after it is added.")
#         )
#         self.buttons_box.addWidget(add_entry_btn)
#
#         # add_fct_entry_btn = FCButton(_("Add Paint/NCC Tool in DB"))
#         # add_fct_entry_btn.setToolTip(
#         #     _("Add a new tool in the Tools Database.\n"
#         #       "It will be used in the Paint/NCC Tools UI.\n"
#         #       "You can edit it after it is added.")
#         # )
#         # self.buttons_box.addWidget(add_fct_entry_btn)
#
#         remove_entry_btn = FCButton(_("Delete Tool from DB"))
#         remove_entry_btn.setToolTip(
#             _("Remove a selection of tools in the Tools Database.")
#         )
#         self.buttons_box.addWidget(remove_entry_btn)
#
#         export_db_btn = FCButton(_("Export DB"))
#         export_db_btn.setToolTip(
#             _("Save the Tools Database to a custom text file.")
#         )
#         self.buttons_box.addWidget(export_db_btn)
#
#         import_db_btn = FCButton(_("Import DB"))
#         import_db_btn.setToolTip(
#             _("Load the Tools Database information's from a custom text file.")
#         )
#         self.buttons_box.addWidget(import_db_btn)
#
#         self.add_tool_from_db = FCButton(_("Transfer the Tool"))
#         self.add_tool_from_db.setToolTip(
#             _("Add a new tool in the Tools Table of the\n"
#               "active Geometry object after selecting a tool\n"
#               "in the Tools Database.")
#         )
#         self.add_tool_from_db.hide()
#
#         self.cancel_tool_from_db = FCButton(_("Cancel"))
#         self.cancel_tool_from_db.hide()
#
#         hlay = QtWidgets.QHBoxLayout()
#         layout.addLayout(hlay)
#         hlay.addWidget(self.add_tool_from_db)
#         hlay.addWidget(self.cancel_tool_from_db)
#         hlay.addStretch()
#
#         # ##############################################################################
#         # ######################## SIGNALS #############################################
#         # ##############################################################################
#
#         add_entry_btn.clicked.connect(self.on_tool_add)
#         remove_entry_btn.clicked.connect(self.on_tool_delete)
#         export_db_btn.clicked.connect(self.on_export_tools_db_file)
#         import_db_btn.clicked.connect(self.on_import_tools_db_file)
#         # closebtn.clicked.connect(self.accept)
#
#         self.add_tool_from_db.clicked.connect(self.on_tool_requested_from_app)
#         self.cancel_tool_from_db.clicked.connect(self.on_cancel_tool)
#
#         self.setup_db_ui()
#
#     def configure_table(self):
#         self.table_widget.setColumnCount(27)
#         # self.table_widget.setColumnWidth(0, 20)
#         self.table_widget.setHorizontalHeaderLabels(
#             [
#                 '#',
#                 _("Tool Name"),
#                 _("Tool Dia"),
#                 _("Tool Offset"),
#                 _("Custom Offset"),
#                 _("Tool Type"),
#                 _("Tool Shape"),
#                 _("Cut Z"),
#                 _("MultiDepth"),
#                 _("DPP"),
#                 _("V-Dia"),
#                 _("V-Angle"),
#                 _("Travel Z"),
#                 _("FR"),
#                 _("FR Z"),
#                 _("FR Rapids"),
#                 _("Spindle Speed"),
#                 _("Dwell"),
#                 _("Dwelltime"),
#                 _("Preprocessor"),
#                 _("ExtraCut"),
#                 _("E-Cut Length"),
#                 _("Toolchange"),
#                 _("Toolchange XY"),
#                 _("Toolchange Z"),
#                 _("Start Z"),
#                 _("End Z"),
#             ]
#         )
#         self.table_widget.horizontalHeaderItem(0).setToolTip(
#             _("Tool Index."))
#         self.table_widget.horizontalHeaderItem(1).setToolTip(
#             _("Tool name.\n"
#               "This is not used in the app, it's function\n"
#               "is to serve as a note for the user."))
#         self.table_widget.horizontalHeaderItem(2).setToolTip(
#             _("Tool Diameter."))
#         self.table_widget.horizontalHeaderItem(3).setToolTip(
#             _("Tool Offset.\n"
#               "Can be of a few types:\n"
#               "Path = zero offset\n"
#               "In = offset inside by half of tool diameter\n"
#               "Out = offset outside by half of tool diameter\n"
#               "Custom = custom offset using the Custom Offset value"))
#         self.table_widget.horizontalHeaderItem(4).setToolTip(
#             _("Custom Offset.\n"
#               "A value to be used as offset from the current path."))
#         self.table_widget.horizontalHeaderItem(5).setToolTip(
#             _("Tool Type.\n"
#               "Can be:\n"
#               "Iso = isolation cut\n"
#               "Rough = rough cut, low feedrate, multiple passes\n"
#               "Finish = finishing cut, high feedrate"))
#         self.table_widget.horizontalHeaderItem(6).setToolTip(
#             _("Tool Shape. \n"
#               "Can be:\n"
#               "C1 ... C4 = circular tool with x flutes\n"
#               "B = ball tip milling tool\n"
#               "V = v-shape milling tool"))
#         self.table_widget.horizontalHeaderItem(7).setToolTip(
#             _("Cutting Depth.\n"
#               "The depth at which to cut into material."))
#         self.table_widget.horizontalHeaderItem(8).setToolTip(
#             _("Multi Depth.\n"
#               "Selecting this will allow cutting in multiple passes,\n"
#               "each pass adding a DPP parameter depth."))
#         self.table_widget.horizontalHeaderItem(9).setToolTip(
#             _("DPP. Depth per Pass.\n"
#               "The value used to cut into material on each pass."))
#         self.table_widget.horizontalHeaderItem(10).setToolTip(
#             _("V-Dia.\n"
#               "Diameter of the tip for V-Shape Tools."))
#         self.table_widget.horizontalHeaderItem(11).setToolTip(
#             _("V-Agle.\n"
#               "Angle at the tip for the V-Shape Tools."))
#         self.table_widget.horizontalHeaderItem(12).setToolTip(
#             _("Clearance Height.\n"
#               "Height at which the milling bit will travel between cuts,\n"
#               "above the surface of the material, avoiding all fixtures."))
#         self.table_widget.horizontalHeaderItem(13).setToolTip(
#             _("FR. Feedrate\n"
#               "The speed on XY plane used while cutting into material."))
#         self.table_widget.horizontalHeaderItem(14).setToolTip(
#             _("FR Z. Feedrate Z\n"
#               "The speed on Z plane."))
#         self.table_widget.horizontalHeaderItem(15).setToolTip(
#             _("FR Rapids. Feedrate Rapids\n"
#               "Speed used while moving as fast as possible.\n"
#               "This is used only by some devices that can't use\n"
#               "the G0 g-code command. Mostly 3D printers."))
#         self.table_widget.horizontalHeaderItem(16).setToolTip(
#             _("Spindle Speed.\n"
#               "If it's left empty it will not be used.\n"
#               "The speed of the spindle in RPM."))
#         self.table_widget.horizontalHeaderItem(17).setToolTip(
#             _("Dwell.\n"
#               "Check this if a delay is needed to allow\n"
#               "the spindle motor to reach its set speed."))
#         self.table_widget.horizontalHeaderItem(18).setToolTip(
#             _("Dwell Time.\n"
#               "A delay used to allow the motor spindle reach its set speed."))
#         self.table_widget.horizontalHeaderItem(19).setToolTip(
#             _("Preprocessor.\n"
#               "A selection of files that will alter the generated G-code\n"
#               "to fit for a number of use cases."))
#         self.table_widget.horizontalHeaderItem(20).setToolTip(
#             _("Extra Cut.\n"
#               "If checked, after a isolation is finished an extra cut\n"
#               "will be added where the start and end of isolation meet\n"
#               "such as that this point is covered by this extra cut to\n"
#               "ensure a complete isolation."))
#         self.table_widget.horizontalHeaderItem(21).setToolTip(
#             _("Extra Cut length.\n"
#               "If checked, after a isolation is finished an extra cut\n"
#               "will be added where the start and end of isolation meet\n"
#               "such as that this point is covered by this extra cut to\n"
#               "ensure a complete isolation. This is the length of\n"
#               "the extra cut."))
#         self.table_widget.horizontalHeaderItem(22).setToolTip(
#             _("Toolchange.\n"
#               "It will create a toolchange event.\n"
#               "The kind of toolchange is determined by\n"
#               "the preprocessor file."))
#         self.table_widget.horizontalHeaderItem(23).setToolTip(
#             _("Toolchange XY.\n"
#               "A set of coordinates in the format (x, y).\n"
#               "Will determine the cartesian position of the point\n"
#               "where the tool change event take place."))
#         self.table_widget.horizontalHeaderItem(24).setToolTip(
#             _("Toolchange Z.\n"
#               "The position on Z plane where the tool change event take place."))
#         self.table_widget.horizontalHeaderItem(25).setToolTip(
#             _("Start Z.\n"
#               "If it's left empty it will not be used.\n"
#               "A position on Z plane to move immediately after job start."))
#         self.table_widget.horizontalHeaderItem(26).setToolTip(
#             _("End Z.\n"
#               "A position on Z plane to move immediately after job stop."))
#
#     def setup_db_ui(self):
#         filename = self.app.tools_database_path()
#
#         # load the database tools from the file
#         try:
#             with open(filename) as f:
#                 tools = f.read()
#         except IOError:
#             self.app.log.error("Could not load tools DB file.")
#             self.app.inform.emit('[ERROR] %s' % _("Could not load Tools DB file."))
#             return
#
#         try:
#             self.db_tool_dict = json.loads(tools)
#         except Exception:
#             e = sys.exc_info()[0]
#             self.app.log.error(str(e))
#             self.app.inform.emit('[ERROR] %s' % _("Failed to parse Tools DB file."))
#             return
#
#         self.app.inform.emit('[success] %s: %s' % (_("Loaded Tools DB from"), filename))
#
#         self.build_db_ui()
#
#         self.table_widget.setupContextMenu()
#         self.table_widget.addContextMenu(
#             _("Add to DB"), self.on_tool_add, icon=QtGui.QIcon(self.app.resource_location + "/plus16.png"))
#         self.table_widget.addContextMenu(
#             _("Copy from DB"), self.on_tool_copy, icon=QtGui.QIcon(self.app.resource_location + "/copy16.png"))
#         self.table_widget.addContextMenu(
#             _("Delete from DB"), self.on_tool_delete, icon=QtGui.QIcon(self.app.resource_location + "/delete32.png"))
#
#     def build_db_ui(self):
#         self.ui_disconnect()
#         self.table_widget.setRowCount(len(self.db_tool_dict))
#
#         nr_crt = 0
#
#         for toolid, dict_val in self.db_tool_dict.items():
#             row = nr_crt
#             nr_crt += 1
#
#             t_name = dict_val['name']
#             try:
#                 self.add_plugin_table_line(row, name=t_name, widget=self.table_widget, tooldict=dict_val)
#             except Exception as e:
#                 self.app.log.debug("ToolDB.build_db_ui.add_plugin_table_line() --> %s" % str(e))
#             vertical_header = self.table_widget.verticalHeader()
#             vertical_header.hide()
#
#             horizontal_header = self.table_widget.horizontalHeader()
#             horizontal_header.setMinimumSectionSize(10)
#             horizontal_header.setDefaultSectionSize(70)
#
#             self.table_widget.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
#             for x in range(27):
#                 self.table_widget.resizeColumnToContents(x)
#
#             horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
#             # horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
#             # horizontal_header.setSectionResizeMode(13, QtWidgets.QHeaderView.ResizeMode.Fixed)
#
#             horizontal_header.resizeSection(0, 20)
#             # horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
#             # horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
#
#         self.ui_connect()
#
#     def add_plugin_table_line(self, row, name, widget, tooldict):
#         data = tooldict['data']
#
#         nr_crt = row + 1
#         id_item = QtWidgets.QTableWidgetItem('%d' % int(nr_crt))
#         # id_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
#         flags = id_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable
#         id_item.setFlags(flags)
#         widget.setItem(row, 0, id_item)  # Tool name/id
#
#         tool_name_item = QtWidgets.QTableWidgetItem(name)
#         widget.setItem(row, 1, tool_name_item)
#
#         dia_item = FCDoubleSpinner()
#         dia_item.set_precision(self.decimals)
#         dia_item.setSingleStep(0.1)
#         dia_item.set_range(0.0, 10000.0000)
#         dia_item.set_value(float(tooldict['tooldia']))
#         widget.setCellWidget(row, 2, dia_item)
#
#         tool_offset_item = FCComboBox()
#         for item in self.offset_item_options:
#             tool_offset_item.addItem(item)
#         tool_offset_item.set_value(tooldict['offset'])
#         widget.setCellWidget(row, 3, tool_offset_item)
#
#         c_offset_item = FCDoubleSpinner()
#         c_offset_item.set_precision(self.decimals)
#         c_offset_item.setSingleStep(0.1)
#         c_offset_item.set_range(-10000.0000, 10000.0000)
#         c_offset_item.set_value(float(tooldict['offset_value']))
#         widget.setCellWidget(row, 4, c_offset_item)
#
#         tt_item = FCComboBox()
#         for item in self.type_item_options:
#             tt_item.addItem(item)
#         tt_item.set_value(tooldict['type'])
#         widget.setCellWidget(row, 5, tt_item)
#
#         tshape_item = FCComboBox()
#         for item in self.tool_type_item_options:
#             tshape_item.addItem(item)
#         tshape_item.set_value(tooldict['data']['tools_mill_tool_shape'])
#         widget.setCellWidget(row, 6, tshape_item)
#
#         cutz_item = FCDoubleSpinner()
#         cutz_item.set_precision(self.decimals)
#         cutz_item.setSingleStep(0.1)

#         cutz_item.set_range(-10000.0000, -0.0000)
#
#         cutz_item.set_value(float(data['cutz']))
#         widget.setCellWidget(row, 7, cutz_item)
#
#         multidepth_item = FCCheckBox()
#         multidepth_item.set_value(data['multidepth'])
#         widget.setCellWidget(row, 8, multidepth_item)
#
#         # to make the checkbox centered, but it can no longer have its value accessed - needs a fix using findchild()
#         # multidepth_item = QtWidgets.QWidget()
#         # cb = FCCheckBox()
#         # cb.set_value(data['multidepth'])
#         # qhboxlayout = QtWidgets.QHBoxLayout(multidepth_item)
#         # qhboxlayout.addWidget(cb)
#         # qhboxlayout.setAlignment(QtCore.Qt.AlignCenter)
#         # qhboxlayout.setContentsMargins(0, 0, 0, 0)
#         # widget.setCellWidget(row, 8, multidepth_item)
#
#         depth_per_pass_item = FCDoubleSpinner()
#         depth_per_pass_item.set_precision(self.decimals)
#         depth_per_pass_item.setSingleStep(0.1)
#         depth_per_pass_item.set_range(0.0, 10000.0000)
#         depth_per_pass_item.set_value(float(data['depthperpass']))
#         widget.setCellWidget(row, 9, depth_per_pass_item)
#
#         vtip_dia_item = FCDoubleSpinner()
#         vtip_dia_item.set_precision(self.decimals)
#         vtip_dia_item.setSingleStep(0.1)
#         vtip_dia_item.set_range(0.0, 10000.0000)
#         vtip_dia_item.set_value(float(data['vtipdia']))
#         widget.setCellWidget(row, 10, vtip_dia_item)
#
#         vtip_angle_item = FCDoubleSpinner()
#         vtip_angle_item.set_precision(self.decimals)
#         vtip_angle_item.setSingleStep(0.1)
#         vtip_angle_item.set_range(-360.0, 360.0)
#         vtip_angle_item.set_value(float(data['vtipangle']))
#         widget.setCellWidget(row, 11, vtip_angle_item)
#
#         travelz_item = FCDoubleSpinner()
#         travelz_item.set_precision(self.decimals)
#         travelz_item.setSingleStep(0.1)

#         travelz_item.set_range(0.0000, 10000.0000)
#
#         travelz_item.set_value(float(data['travelz']))
#         widget.setCellWidget(row, 12, travelz_item)
#
#         fr_item = FCDoubleSpinner()
#         fr_item.set_precision(self.decimals)
#         fr_item.set_range(0.0, 10000.0000)
#         fr_item.set_value(float(data['feedrate']))
#         widget.setCellWidget(row, 13, fr_item)
#
#         frz_item = FCDoubleSpinner()
#         frz_item.set_precision(self.decimals)
#         frz_item.set_range(0.0, 10000.0000)
#         frz_item.set_value(float(data['feedrate_z']))
#         widget.setCellWidget(row, 14, frz_item)
#
#         frrapids_item = FCDoubleSpinner()
#         frrapids_item.set_precision(self.decimals)
#         frrapids_item.set_range(0.0, 10000.0000)
#         frrapids_item.set_value(float(data['feedrate_rapid']))
#         widget.setCellWidget(row, 15, frrapids_item)
#
#         spindlespeed_item = FCSpinner()
#         spindlespeed_item.set_range(0, 1000000)
#         spindlespeed_item.set_value(int(data['spindlespeed']))
#         spindlespeed_item.set_step(100)
#         widget.setCellWidget(row, 16, spindlespeed_item)
#
#         dwell_item = FCCheckBox()
#         dwell_item.set_value(data['dwell'])
#         widget.setCellWidget(row, 17, dwell_item)
#
#         dwelltime_item = FCDoubleSpinner()
#         dwelltime_item.set_precision(self.decimals)
#         dwelltime_item.set_range(0.0000, 10000.0000)
#         dwelltime_item.set_value(float(data['dwelltime']))
#         widget.setCellWidget(row, 18, dwelltime_item)
#
#         pp_item = FCComboBox()
#         for item in self.app.preprocessors:
#             pp_item.addItem(item)
#         pp_item.set_value(data['ppname_g'])
#         widget.setCellWidget(row, 19, pp_item)
#
#         ecut_item = FCCheckBox()
#         ecut_item.set_value(data['extracut'])
#         widget.setCellWidget(row, 20, ecut_item)
#
#         ecut_length_item = FCDoubleSpinner()
#         ecut_length_item.set_precision(self.decimals)
#         ecut_length_item.set_range(0.0000, 10000.0000)
#         ecut_length_item.set_value(data['extracut_length'])
#         widget.setCellWidget(row, 21, ecut_length_item)
#
#         toolchange_item = FCCheckBox()
#         toolchange_item.set_value(data['toolchange'])
#         widget.setCellWidget(row, 22, toolchange_item)
#
#         toolchangexy_item = QtWidgets.QTableWidgetItem(str(data['toolchangexy']) if data['toolchangexy'] else '')
#         widget.setItem(row, 23, toolchangexy_item)
#
#         toolchangez_item = FCDoubleSpinner()
#         toolchangez_item.set_precision(self.decimals)
#         toolchangez_item.setSingleStep(0.1)

#         toolchangez_item.set_range(0.0000, 10000.0000)
#
#         toolchangez_item.set_value(float(data['toolchangez']))
#         widget.setCellWidget(row, 24, toolchangez_item)
#
#         startz_item = QtWidgets.QTableWidgetItem(str(data['startz']) if data['startz'] else '')
#         widget.setItem(row, 25, startz_item)
#
#         endz_item = FCDoubleSpinner()
#         endz_item.set_precision(self.decimals)
#         endz_item.setSingleStep(0.1)

#         endz_item.set_range(0.0000, 10000.0000)
#
#         endz_item.set_value(float(data['endz']))
#         widget.setCellWidget(row, 26, endz_item)
#
#     def on_tool_add(self):
#         """
#         Add a tool in the DB Tool Table
#         :return: None
#         """
#
#         default_data = {}
#         default_data.update({
#             "cutz": float(self.app.options["geometry_cutz"]),
#             "multidepth": self.app.options["geometry_multidepth"],
#             "depthperpass": float(self.app.options["geometry_depthperpass"]),
#             "vtipdia": float(self.app.options["geometry_vtipdia"]),
#             "vtipangle": float(self.app.options["geometry_vtipangle"]),
#             "travelz": float(self.app.options["geometry_travelz"]),
#             "feedrate": float(self.app.options["geometry_feedrate"]),
#             "feedrate_z": float(self.app.options["geometry_feedrate_z"]),
#             "feedrate_rapid": float(self.app.options["geometry_feedrate_rapid"]),
#             "spindlespeed": self.app.options["geometry_spindlespeed"],
#             "dwell": self.app.options["geometry_dwell"],
#             "dwelltime": float(self.app.options["geometry_dwelltime"]),
#             "ppname_g": self.app.options["geometry_ppname_g"],
#             "extracut": self.app.options["geometry_extracut"],
#             "extracut_length": float(self.app.options["geometry_extracut_length"]),
#             "toolchange": self.app.options["geometry_toolchange"],
#             "toolchangexy": self.app.options["geometry_toolchangexy"],
#             "toolchangez": float(self.app.options["geometry_toolchangez"]),
#             "startz": self.app.options["geometry_startz"],
#             "endz": float(self.app.options["geometry_endz"])
#         })
#
#         dict_elem = {}
#         dict_elem['name'] = 'new_tool'
#         if type(self.app.options["tools_mill_tooldia"]) == float:
#             dict_elem['tooldia'] = self.app.options["tools_mill_tooldia"]
#         else:
#             try:
#                 tools_string = self.app.options["tools_mill_tooldia"].split(",")
#                 tools_diameters = [eval(a) for a in tools_string if a != '']
#                 dict_elem['tooldia'] = tools_diameters[0] if tools_diameters else 0.0
#             except Exception as e:
#                 self.app.log.debug("ToolDB.on_tool_add() --> %s" % str(e))
#                 return
#
#         dict_elem['offset'] = 'Path'
#         dict_elem['offset_value'] = 0.0
#         dict_elem['type'] = 'Rough'
#         dict_elem['data']['tools_mill_tool_shape'] = 'C1'
#         dict_elem['data'] = default_data
#
#         new_toolid = len(self.db_tool_dict) + 1
#         self.db_tool_dict[new_toolid] = deepcopy(dict_elem)
#
#         # add the new entry to the Tools DB table
#         self.build_db_ui()
#         self.callback_on_edited()
#         self.app.inform.emit('[success] %s' % _("Tool added to DB."))
#
#     def on_tool_copy(self):
#         """
#         Copy a selection of Tools in the Tools DB table
#         :return:
#         """
#         new_tool_id = self.table_widget.rowCount() + 1
#         for model_index in self.table_widget.selectionModel().selectedRows():
#             # index = QtCore.QPersistentModelIndex(model_index)
#             old_tool_id = self.table_widget.item(model_index.row(), 0).text()
#             new_tool_id += 1
#
#             for toolid, dict_val in list(self.db_tool_dict.items()):
#                 if int(old_tool_id) == int(toolid):
#                     self.db_tool_dict.update({
#                         new_tool_id: deepcopy(dict_val)
#                     })
#
#         self.build_db_ui()
#         self.callback_on_edited()
#         self.app.inform.emit('[success] %s' % _("Tool copied from Tools DB."))
#
#     def on_tool_delete(self):
#         """
#         Delete a selection of Tools in the Tools DB table
#         :return:
#         """
#         for model_index in self.table_widget.selectionModel().selectedRows():
#             # index = QtCore.QPersistentModelIndex(model_index)
#             pluginName_to_remove = self.table_widget.item(model_index.row(), 0).text()
#
#             for toolid, dict_val in list(self.db_tool_dict.items()):
#                 if int(pluginName_to_remove) == int(toolid):
#                     # remove from the storage
#                     self.db_tool_dict.pop(toolid, None)
#
#         self.build_db_ui()
#         self.callback_on_edited()
#         self.app.inform.emit('[success] %s' % _("Tool removed from Tools DB."))
#
#     def on_export_tools_db_file(self):
#         self.app.defaults.report_usage("on_export_tools_db_file")
#         self.app.log.debug("on_export_tools_db_file()")
#
#         date = str(datetime.today()).rpartition('.')[0]
#         date = ''.join(c for c in date if c not in ':-')
#         date = date.replace(' ', '_')
#
#         filter__ = "Text File (*.TXT);;All Files (*.*)"
#         filename, _f = FCFileSaveDialog.get_saved_filename(caption=_("Export Tools Database"),
#                                                            directory='{l_save}/FlatCAM_{n}_{date}'.format(
#                                                                l_save=str(self.app.get_last_save_folder()),
#                                                                n=_("Tools_Database"),
#                                                                date=date),
#                                                            ext_filter=filter__)
#
#         filename = str(filename)
#
#         if filename == "":
#             self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
#             return
#         else:
#             try:
#                 f = open(filename, 'w')
#                 f.close()
#             except PermissionError:
#                 self.app.inform.emit('[WARNING] %s' %
#                                      _("Permission denied, saving not possible.\n"
#                                        "Most likely another app is holding the file open and not accessible."))
#                 return
#             except IOError:
#                 self.app.log.debug('Creating a new Tools DB file ...')
#                 f = open(filename, 'w')
#                 f.close()
#             except Exception:
#                 e = sys.exc_info()[0]
#                 self.app.log.error("Could not load Tools DB file.")
#                 self.app.log.error(str(e))
#                 self.app.inform.emit('[ERROR_NOTCL] %s' % _("Could not load Tools DB file."))
#                 return
#
#             # Save update options
#             try:
#                 # Save Tools DB in a file
#                 try:
#                     with open(filename, "w") as f:
#                         json.dump(self.db_tool_dict, f, default=to_dict, indent=2)
#                 except Exception as e:
#                     self.app.log.debug("App.on_save_tools_db() --> %s" % str(e))
#                     self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed to write Tools DB to file."))
#                     return
#             except Exception:
#                 self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed to write Tools DB to file."))
#                 return
#
#         self.app.inform.emit('[success] %s: %s' % (_("Exported Tools DB to"), filename))
#
#     def on_import_tools_db_file(self):
#         self.app.defaults.report_usage("on_import_tools_db_file")
#         self.app.log.debug("on_import_tools_db_file()")
#
#         filter__ = "Text File (*.TXT);;All Files (*.*)"
#         filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import FlatCAM Tools DB"), filter=filter__)
#
#         if filename == "":
#             self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
#         else:
#             try:
#                 with open(filename) as f:
#                     tools_in_db = f.read()
#             except IOError:
#                 self.app.log.error("Could not load Tools DB file.")
#                 self.app.inform.emit('[ERROR_NOTCL] %s' % _("Could not load Tools DB file."))
#                 return
#
#             try:
#                 self.db_tool_dict = json.loads(tools_in_db)
#             except Exception:
#                 e = sys.exc_info()[0]
#                 self.app.log.error(str(e))
#                 self.app.inform.emit('[ERROR] %s' % _("Failed to parse Tools DB file."))
#                 return
#
#             self.app.inform.emit('[success] %s: %s' % (_("Loaded Tools DB from"), filename))
#             self.build_db_ui()
#             self.callback_on_edited()
#
#     def on_save_tools_db(self, silent=False):
#         self.app.log.debug("ToolsDB.on_save_button() --> Saving Tools Database to file.")
#
#         filename = self.app.tools_database_path()
#
#         # Preferences save, update the color of the Tools DB Tab text
#         for idx in range(self.app_ui.plot_tab_area.count()):
#             if self.app_ui.plot_tab_area.tabText(idx) == _("Tools Database"):
#                 self.app_ui.plot_tab_area.tabBar.setTabTextColor(idx, QtGui.QColor('black'))
#
#                 # Save Tools DB in a file
#                 try:
#                     f = open(filename, "w")
#                     json.dump(self.db_tool_dict, f, default=to_dict, indent=2)
#                     f.close()
#                 except Exception as e:
#                     self.app.log.debug("ToolsDB.on_save_tools_db() --> %s" % str(e))
#                     self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed to write Tools DB to file."))
#                     return
#
#                 if not silent:
#                     self.app.inform.emit('[success] %s' % _("Saved Tools DB."))
#
#     def ui_connect(self):
#         try:
#             try:
#                 self.table_widget.itemChanged.disconnect(self.callback_on_edited)
#             except (TypeError, AttributeError):
#                 pass
#             self.table_widget.itemChanged.connect(self.callback_on_edited)
#         except AttributeError:
#             pass
#
#         for row in range(self.table_widget.rowCount()):
#             for col in range(self.table_widget.columnCount()):
#                 # ComboBox
#                 try:
#                     try:
#                         self.table_widget.cellWidget(row, col).currentIndexChanged.disconnect(self.callback_on_edited)
#                     except (TypeError, AttributeError):
#                         pass
#                     self.table_widget.cellWidget(row, col).currentIndexChanged.connect(self.callback_on_edited)
#                 except AttributeError:
#                     pass
#
#                 # CheckBox
#                 try:
#                     try:
#                         self.table_widget.cellWidget(row, col).toggled.disconnect(self.callback_on_edited)
#                     except (TypeError, AttributeError):
#                         pass
#                     self.table_widget.cellWidget(row, col).toggled.connect(self.callback_on_edited)
#                 except AttributeError:
#                     pass
#
#                 # SpinBox, DoubleSpinBox
#                 try:
#                     try:
#                         self.table_widget.cellWidget(row, col).valueChanged.disconnect(self.callback_on_edited)
#                     except (TypeError, AttributeError):
#                         pass
#                     self.table_widget.cellWidget(row, col).valueChanged.connect(self.callback_on_edited)
#                 except AttributeError:
#                     pass
#
#     def ui_disconnect(self):
#         try:
#             self.table_widget.itemChanged.disconnect(self.callback_on_edited)
#         except (TypeError, AttributeError):
#             pass
#
#         for row in range(self.table_widget.rowCount()):
#             for col in range(self.table_widget.columnCount()):
#                 # ComboBox
#                 try:
#                     self.table_widget.cellWidget(row, col).currentIndexChanged.disconnect(self.callback_on_edited)
#                 except (TypeError, AttributeError):
#                     pass
#
#                 # CheckBox
#                 try:
#                     self.table_widget.cellWidget(row, col).toggled.disconnect(self.callback_on_edited)
#                 except (TypeError, AttributeError):
#                     pass
#
#                 # SpinBox, DoubleSpinBox
#                 try:
#                     self.table_widget.cellWidget(row, col).valueChanged.disconnect(self.callback_on_edited)
#                 except (TypeError, AttributeError):
#                     pass
#
#     def callback_on_edited(self):
#
#         # update the dictionary storage self.db_tool_dict
#         self.db_tool_dict.clear()
#         dict_elem = {}
#         default_data = {}
#
#         for row in range(self.table_widget.rowCount()):
#             new_toolid = row + 1
#             for col in range(self.table_widget.columnCount()):
#                 column_header_text = self.table_widget.horizontalHeaderItem(col).text()
#                 if column_header_text == _('Tool Name'):
#                     dict_elem['name'] = self.table_widget.item(row, col).text()
#                 elif column_header_text == _('Tool Dia'):
#                     dict_elem['tooldia'] = self.table_widget.cellWidget(row, col).get_value()
#                 elif column_header_text == _('Tool Offset'):
#                     dict_elem['offset'] = self.table_widget.cellWidget(row, col).get_value()
#                 elif column_header_text == _('Custom Offset'):
#                     dict_elem['offset_value'] = self.table_widget.cellWidget(row, col).get_value()
#                 elif column_header_text == _('Tool Type'):
#                     dict_elem['type'] = self.table_widget.cellWidget(row, col).get_value()
#                 elif column_header_text == _('Tool Shape'):
#                     dict_elem['data']['tools_mill_tool_shape'] = self.table_widget.cellWidget(row, col).get_value()
#                 else:
#                     if column_header_text == _('Cut Z'):
#                         default_data['cutz'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('MultiDepth'):
#                         default_data['multidepth'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('DPP'):
#                         default_data['depthperpass'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('V-Dia'):
#                         default_data['vtipdia'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('V-Angle'):
#                         default_data['vtipangle'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Travel Z'):
#                         default_data['travelz'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('FR'):
#                         default_data['feedrate'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('FR Z'):
#                         default_data['feedrate_z'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('FR Rapids'):
#                         default_data['feedrate_rapid'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Spindle Speed'):
#                         default_data['spindlespeed'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Dwell'):
#                         default_data['dwell'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Dwelltime'):
#                         default_data['dwelltime'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Preprocessor'):
#                         default_data['ppname_g'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('ExtraCut'):
#                         default_data['extracut'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _("E-Cut Length"):
#                         default_data['extracut_length'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Toolchange'):
#                         default_data['toolchange'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Toolchange XY'):
#                         default_data['toolchangexy'] = self.table_widget.item(row, col).text()
#                     elif column_header_text == _('Toolchange Z'):
#                         default_data['toolchangez'] = self.table_widget.cellWidget(row, col).get_value()
#                     elif column_header_text == _('Start Z'):
#                         default_data['startz'] = float(self.table_widget.item(row, col).text()) \
#                             if self.table_widget.item(row, col).text() != '' else None
#                     elif column_header_text == _('End Z'):
#                         default_data['endz'] = self.table_widget.cellWidget(row, col).get_value()
#
#             dict_elem['data'] = default_data
#             self.db_tool_dict.update(
#                 {
#                     new_toolid: deepcopy(dict_elem)
#                 }
#             )
#
#         self.callback_app()
#
#     def on_tool_requested_from_app(self):
#         if not self.table_widget.selectionModel().selectedRows():
#             self.app.inform.emit('[WARNING_NOTCL] %s...' % _("No Tool/row selected in the Tools Database table"))
#             return
#
#         model_index_list = self.table_widget.selectionModel().selectedRows()
#         for model_index in model_index_list:
#             selected_row = model_index.row()
#             tool_uid = selected_row + 1
#             for key in self.db_tool_dict.keys():
#                 if str(key) == str(tool_uid):
#                     selected_tool = self.db_tool_dict[key]
#                     self.on_tool_request(tool=selected_tool)
#
#     def on_cancel_tool(self):
#         for idx in range(self.app_ui.plot_tab_area.count()):
#             if self.app_ui.plot_tab_area.tabText(idx) == _("Tools Database"):
#                 wdg = self.app_ui.plot_tab_area.widget(idx)
#                 wdg.deleteLater()
#                 self.app_ui.plot_tab_area.removeTab(idx)
#         self.app.inform.emit('%s' % _("Cancelled adding tool from DB."))
#
#     # def resize_new_plugin_table_widget(self, min_size, max_size):
#     #     """
#     #     Resize the table widget responsible for adding new tool in the Tool Database
#     #
#     #     :param min_size: passed by rangeChanged signal or the self.new_plugin_table_widget.horizontalScrollBar()
#     #     :param max_size: passed by rangeChanged signal or the self.new_plugin_table_widget.horizontalScrollBar()
#     #     :return:
#     #     """
#     #     t_height = self.t_height
#     #     if max_size > min_size:
#     #         t_height = self.t_height + self.new_plugin_table_widget.verticalScrollBar().height()
#     #
#     #     self.new_plugin_table_widget.setMaximumHeight(t_height)
#
#     def closeEvent(self, QCloseEvent):
#         super().closeEvent(QCloseEvent)
