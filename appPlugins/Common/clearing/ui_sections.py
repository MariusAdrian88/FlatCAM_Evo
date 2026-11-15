# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Builders of the UI sections that the NCC and Paint panels share.

A builder creates the widgets of one section, puts them in the grid / box it is given and sets each of them as an
attribute of the UI object under the same name the panels always used. What differs per tool (tooltips, rows,
objectNames, ranges) is passed in. Plain functions, no inheritance."""

import builtins
import gettext

from PyQt6 import QtGui, QtWidgets, QtCore

import appTranslation as fcTranslate
from appGUI.GUIElements import (
    FCButton, FCCheckBox, FCComboBox, FCComboBox2, FCDoubleSpinner, FCFrame, FCLabel, FCTable, GLay,
    OptionalInputSection, RadioSet,
)

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def _method_texts():
    """Never called: the literals let the string extraction see the method names that build_method translates."""
    return [_("Standard"), _("Seed"), _("Lines"), _("Laser_lines"), _("Combo"), _("Crosshatch")]


def _put(ui, name, widget, object_name=None):
    """Set ``widget`` on ``ui`` as ``name``; an objectName is set only when one is given."""
    if object_name:
        widget.setObjectName(object_name)
    setattr(ui, name, widget)
    return widget


def _units_step(ui, entry):
    entry.setSingleStep(0.1 if ui.app.app_units.upper() == 'MM' else 0.01)


# ---------------------------------------------------------------------------------------------------------------
# tool table
# ---------------------------------------------------------------------------------------------------------------
def build_tool_table(ui, tools_box, *, label_tooltip, number_tooltip, table_row, order_label_bold, sorting_off):
    """The 'Tools Table' label, its frame, the table and the tool order. Returns the grid of the frame."""
    _put(ui, 'tools_table_label', FCLabel('%s' % _("Tools Table"), color='green', bold=True)).setToolTip(label_tooltip)
    tools_box.addWidget(ui.tools_table_label)

    frame = FCFrame()
    tools_box.addWidget(frame)
    grid = GLay(v_spacing=5, h_spacing=3)
    frame.setLayout(grid)

    table = _put(ui, 'tools_table', FCTable(drag_drop=True))
    grid.addWidget(table, table_row, 0, 1, 2)
    table.setColumnCount(4)
    # the 4th column is reserved (and hidden) for the tool ID
    table.setHorizontalHeaderLabels(['#', _('Diameter'), _('Shape'), ''])
    table.setColumnHidden(3, True)
    if sorting_off:
        table.setSortingEnabled(False)

    table.horizontalHeaderItem(0).setToolTip(number_tooltip)
    table.horizontalHeaderItem(1).setToolTip(
        _("Tool Diameter. Its value\n"
          "is the cut width into the material."))
    table.horizontalHeaderItem(2).setToolTip(
        _("Tool Shape. \n"
          "Can be:\n"
          "C1 ... C4 = circular tool with x flutes\n"
          "B = ball tip milling tool\n"
          "V = v-shape milling tool\n"
          "L = laser"))

    _put(ui, 'order_label', FCLabel('%s:' % _('Tool order'), bold=order_label_bold)).setToolTip(
        _("This set the way that the tools in the tools table are used.\n"
          "'Default' --> means that the used order is the one in the tool table\n"
          "'Forward' --> means that the tools will be ordered from small to big\n"
          "'Reverse' --> means that the tools will ordered from big to small\n\n"
          "WARNING: using rest machining will automatically set the order\n"
          "in reverse and disable this control.")
    )
    combo = _put(ui, 'order_combo', FCComboBox2())
    combo.addItems([_('Default'), _('Forward'), _('Reverse')])
    grid.addWidget(ui.order_label, 4, 0)
    grid.addWidget(combo, 4, 1)
    return grid


def build_add_tool(ui, tool_grid, *, span, dia_tooltip, dia_object_name, dia_min):
    """The 'Add from DB' frame: the new tool diameter and the search / pick / delete buttons.
    ``span`` is the number of columns of its grid. Returns that grid."""
    frame = _put(ui, 'add_tool_frame', QtWidgets.QFrame())
    frame.setContentsMargins(0, 0, 0, 0)
    tool_grid.addWidget(frame, 6, 0, 1, 2)

    grid = GLay(v_spacing=5, h_spacing=3)
    grid.setContentsMargins(0, 0, 0, 0)
    frame.setLayout(grid)

    separator_line = QtWidgets.QFrame()
    separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
    separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
    grid.addWidget(separator_line, 0, 0, 1, span)

    grid.addWidget(_put(ui, 'tool_sel_label', FCLabel('%s' % _('Add from DB'), bold=True)), 2, 0, 1, span)

    label = _put(ui, 'new_tooldia_lbl', FCLabel('%s:' % _('Tool Dia')))
    label.setToolTip(dia_tooltip)
    entry = _put(ui, 'new_tooldia_entry', FCDoubleSpinner(callback=ui.confirmation_message))
    entry.set_precision(ui.decimals)
    entry.set_range(dia_min, 10000.0000)
    entry.setObjectName(dia_object_name)
    grid.addWidget(label, 4, 0)
    grid.addWidget(entry, 4, 1)

    button_grid = GLay(v_spacing=5, h_spacing=3)
    button_grid.setColumnStretch(0, 1)
    button_grid.setColumnStretch(1, 0)
    grid.addLayout(button_grid, 6, 0, 1, span)

    search = _put(ui, 'search_and_add_btn', FCButton(_('Search and Add')))
    search.setIcon(QtGui.QIcon(ui.app.resource_location + '/plus16.png'))
    search.setToolTip(
        _("Add a new tool to the Tool Table\n"
          "with the diameter specified above.\n"
          "This is done by a background search\n"
          "in the Tools Database. If nothing is found\n"
          "in the Tools DB then a default tool is added.")
    )
    button_grid.addWidget(search, 0, 0)

    pick = _put(ui, 'addtool_from_db_btn', FCButton(_('Pick from DB')))
    pick.setIcon(QtGui.QIcon(ui.app.resource_location + '/search_db32.png'))
    pick.setToolTip(
        _("Add a new tool to the Tool Table\n"
          "from the Tools Database.\n"
          "Tools database administration in in:\n"
          "Menu: Options -> Tools Database")
    )
    button_grid.addWidget(pick, 1, 0)

    delete = _put(ui, 'deltool_btn', FCButton())
    delete.setIcon(QtGui.QIcon(ui.app.resource_location + '/trash16.png'))
    delete.setToolTip(
        _("Delete a selection of tools in the Tool Table\n"
          "by first selecting a row in the Tool Table.")
    )
    delete.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)
    button_grid.addWidget(delete, 0, 1, 2, 1)
    return grid


# ---------------------------------------------------------------------------------------------------------------
# the parameters of one tool
# ---------------------------------------------------------------------------------------------------------------
def build_overlap(ui, grid, row, prefix, *, precision, label_attr=None):
    """Overlap label + entry (percent). ``label_attr`` is the attribute of the label, None when it has none."""
    label = FCLabel('%s:' % _('Overlap'))
    label.setToolTip(
        _("How much (percentage) of the tool width to overlap each tool pass.\n"
          "Adjust the value starting with lower values\n"
          "and increasing it if areas that should be processed are still \n"
          "not processed.\n"
          "Lower values = faster processing, faster execution on CNC.\n"
          "Higher values = slow processing and slow execution on CNC\n"
          "due of too many paths.")
    )
    if label_attr:
        setattr(ui, label_attr, label)
    entry = _put(ui, 'overlap_entry', FCDoubleSpinner(callback=ui.confirmation_message, suffix='%'))
    entry.set_precision(precision)
    entry.setWrapping(True)
    entry.setRange(0.0, 99.9999)
    entry.setSingleStep(0.1)
    entry.setObjectName(prefix + 'overlap')
    grid.addWidget(label, row, 0)
    grid.addWidget(entry, row, 1)


def build_method(ui, grid, row, prefix, methods, *, tooltip, label_attr=None):
    """Method combo (items from the MethodMap) at ``row`` and the Crosshatch angle at ``row + 1``."""
    label = FCLabel('%s:' % _('Method'))
    label.setToolTip(tooltip)
    if label_attr:
        setattr(ui, label_attr, label)
    combo = _put(ui, 'method_combo', FCComboBox2())
    combo.addItems([_(methods.name(method_id)) for method_id in methods])
    combo.setObjectName(prefix + 'method')
    grid.addWidget(label, row, 0)
    grid.addWidget(combo, row, 1)

    _put(ui, 'crosshatch_angle_label', FCLabel('%s:' % _('Angle'))).setToolTip(
        _("Direction of the first family of lines of the Crosshatch method, in degrees.\n"
          "The second family is at 90 degrees from it.")
    )
    angle = _put(ui, 'crosshatch_angle_entry', FCDoubleSpinner(callback=ui.confirmation_message))
    angle.set_precision(ui.decimals)
    angle.setWrapping(True)
    angle.setRange(0.0, 179.9999)
    angle.setSingleStep(45)
    angle.setObjectName(prefix + 'crosshatch_angle')
    grid.addWidget(ui.crosshatch_angle_label, row + 1, 0)
    grid.addWidget(angle, row + 1, 1)


def build_offset(ui, grid, row, *, rest, tooltip, choice, min_value, max_value, precision,
                 check_object_name=None, entry_object_name=None, wrapping=False, step_by_units=False):
    """Offset of the tool (``rest`` False) or of the rest machining (True).

    ``choice``: a checkbox switches the entry on (attributes ``[rest_]offset_choice_cb`` and ``[rest_]ois_offset``);
    otherwise a label sits in front of the entry (``[rest_]offset_label``)."""
    head = 'rest_' if rest else ''
    if choice:
        check = _put(ui, head + 'offset_choice_cb', FCCheckBox('%s' % _("Offset")), check_object_name)
        check.setToolTip(tooltip)
        grid.addWidget(check, row, 0)
    else:
        label = _put(ui, head + 'offset_label', FCLabel('%s:' % _('Offset')))
        label.setToolTip(tooltip)
        grid.addWidget(label, row, 0)
    entry = _put(ui, head + 'offset_entry', FCDoubleSpinner(callback=ui.confirmation_message), entry_object_name)
    entry.set_range(min_value, max_value)
    entry.set_precision(precision)
    if wrapping:
        entry.setWrapping(True)
    if step_by_units:
        _units_step(ui, entry)
    grid.addWidget(entry, row, 1)
    if choice:
        setattr(ui, head + 'ois_offset', OptionalInputSection(getattr(ui, head + 'offset_choice_cb'), [entry]))


def build_connect_contour(ui, grid, row, prefix, *, rest=False):
    """Connect (column 0) and Contour (column 1) checkboxes. ``rest``: the 2 of the rest machining."""
    head = 'rest_' if rest else ''
    connect = _put(ui, head + 'connect_cb', FCCheckBox('%s' % _("Connect")), None if rest else prefix + 'connect')
    contour = _put(ui, head + 'contour_cb', FCCheckBox('%s' % _("Contour")), None if rest else prefix + 'contour')
    if rest:
        connect.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.\n"
              "A change applies to all tools in the table.")
        )
        contour.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.\n"
              "A change applies to all tools in the table.")
        )
    else:
        connect.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )
        contour.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.")
        )
    grid.addWidget(connect, row, 0)
    grid.addWidget(contour, row, 1)


def build_direction(ui, grid, row, prefix):
    """The clearing direction: label + None / Climb / Conv radio."""
    label = _put(ui, 'clear_dir_label', FCLabel('%s:' % _('Direction')))
    label.setToolTip(
        _("Cut direction of the clearing passes, for a spindle turning clockwise (M3):\n"
          "- Climb: the wall stays on the right of the tool.\n"
          "- Conventional: the wall stays on the left.\n"
          "Lines, Crosshatch zig-zag lines and Laser Lines are not oriented.\n"
          "With a direction the G-code does not reverse paths:\n"
          "multi-depth open paths retract and return to the start.")
    )
    radio = _put(ui, 'clear_dir_radio', RadioSet([{'label': _('None'), 'value': 'none'},
                                                  {'label': _('Climb'), 'value': 'climb'},
                                                  {'label': _('Conv'), 'value': 'conv'}], compact=True))
    radio.setToolTip(label.toolTip())
    radio.setObjectName(prefix + 'clear_milling')
    grid.addWidget(label, row, 0)
    grid.addWidget(radio, row, 1)


def build_final_cleanup(ui, grid, row, prefix, *, text, tooltip):
    """The 'Final cleanup' / 'Final paint' checkbox over the 2 columns."""
    check = _put(ui, 'final_cleanup_cb', FCCheckBox(text), prefix + 'final_cleanup')
    check.setToolTip(tooltip)
    grid.addWidget(check, row, 0, 1, 2)


# ---------------------------------------------------------------------------------------------------------------
# the parameters common to all tools
# ---------------------------------------------------------------------------------------------------------------
def build_rest(ui, grid, prefix, *, tooltip):
    """The 'Rest Machining' checkbox at the top of the common parameters."""
    check = _put(ui, 'rest_cb', FCCheckBox('%s' % _("Rest Machining")), prefix + 'rest_machining')
    check.setToolTip(tooltip)
    grid.addWidget(check, 0, 0, 1, 2)


def build_selection(ui, grid, row, prefix, items, *, tooltip, reference_tooltip, label_attr=None):
    """The selection combo at ``row``, then (rows +2, +4, +6) the reference type, the reference object and the area
    shape. The last 4 start hidden."""
    label = FCLabel('%s:' % _("Selection"))
    label.setToolTip(tooltip)
    if label_attr:
        setattr(ui, label_attr, label)
    combo = _put(ui, 'select_method_combo', FCComboBox2())
    combo.addItems(items)
    combo.setObjectName(prefix + 'selection')
    grid.addWidget(label, row, 0)
    grid.addWidget(combo, row, 1)

    _put(ui, 'reference_type_label', FCLabel('%s:' % _("Type"))).setToolTip(reference_tooltip)
    type_combo = _put(ui, 'reference_type_combo', FCComboBox2())
    type_combo.addItems([_("Gerber"), _("Excellon"), _("Geometry")])
    grid.addWidget(ui.reference_type_label, row + 2, 0)
    grid.addWidget(type_combo, row + 2, 1)

    reference = _put(ui, 'reference_combo', FCComboBox())
    reference.setModel(ui.app.collection)
    reference.setRootModelIndex(ui.app.collection.index(0, 0, QtCore.QModelIndex()))
    reference.is_last = True
    grid.addWidget(reference, row + 4, 0, 1, 2)
    reference.hide()
    type_combo.hide()
    ui.reference_type_label.hide()

    _put(ui, 'area_shape_label', FCLabel('%s:' % _("Shape"))).setToolTip(
        _("The kind of selection shape used for area selection.")
    )
    shape = _put(ui, 'area_shape_radio', RadioSet([{'label': _("Square"), 'value': 'square'},
                                                   {'label': _("Polygon"), 'value': 'polygon'}], compact=True))
    grid.addWidget(ui.area_shape_label, row + 6, 0)
    grid.addWidget(shape, row + 6, 1)
    ui.area_shape_label.hide()
    shape.hide()


def build_validity(ui, grid, row, prefix, *, tooltip):
    """The 'Check validity' checkbox."""
    check = _put(ui, 'valid_cb', FCCheckBox(label=_('Check validity')), prefix + 'check')
    check.setToolTip(tooltip)
    grid.addWidget(check, row, 0, 1, 2)


def build_exclusion(ui, tools_box, *, label_tooltip, check_tooltip, check_object_name=None):
    """The 'Exclusion areas' label and frame. Returns the grid of the frame."""
    _put(ui, 'excl_label', FCLabel('%s' % _("Exclusion areas"), color='red', bold=True)).setToolTip(label_tooltip)
    tools_box.addWidget(ui.excl_label)

    frame = FCFrame()
    tools_box.addWidget(frame)
    grid = GLay(v_spacing=5, h_spacing=3)
    frame.setLayout(grid)

    check = _put(ui, 'excl_cb', FCCheckBox(_("Use exclusion areas")), check_object_name)
    check.setToolTip(check_tooltip)
    grid.addWidget(check, 0, 0, 1, 2)

    _put(ui, 'excl_shape_label', FCLabel('%s:' % _("Shape"))).setToolTip(
        _("The kind of shape used to draw an exclusion zone."))
    shape = _put(ui, 'excl_shape_radio', RadioSet([{'label': _("Square"), 'value': 'square'},
                                                   {'label': _("Polygon"), 'value': 'polygon'}], compact=True))
    grid.addWidget(ui.excl_shape_label, 2, 0)
    grid.addWidget(shape, 2, 1)

    _put(ui, 'excl_count_label', FCLabel('%s:' % _("Zones"))).setToolTip(
        _("The number of exclusion zones of the selected object."))
    count = _put(ui, 'excl_count_value', FCLabel('0'))
    grid.addWidget(ui.excl_count_label, 4, 0)
    grid.addWidget(count, 4, 1)

    add = _put(ui, 'excl_add_button', FCButton(_("Add area")))
    add.setToolTip(
        _("Draw exclusion zones on the canvas; they are added to the selected object.\n"
          "Left click to draw, right click or Escape to finish.")
    )
    delete = _put(ui, 'excl_delete_button', FCButton(_("Delete all")))
    delete.setToolTip(_("Remove all the exclusion zones of the selected object."))
    grid.addWidget(add, 6, 0)
    grid.addWidget(delete, 6, 1)
    return grid


def exclusion_on(ui):
    """The exclusion zones apply: Advanced mode and the Exclusion Areas check box is set."""
    return bool(ui.level.isChecked() and ui.excl_cb.get_value())
