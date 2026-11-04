from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    RadioSet, FCDoubleSpinner, FCTable, opposite_direction

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class PunchUI:

    pluginName = _("Punch Gerber")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.units = self.app.app_units.lower()
        self.layout = layout
        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)
        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)
        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.title_box.addWidget(title_label)
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(_(
            "Beginner Mode - many parameters are hidden.\n"
            "Advanced Mode - full control.\n"
            "Permanent change is done in 'Preferences' menu."))
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)
        self.obj_combo_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.obj_combo_label.setToolTip('%s.' % _("Gerber into which to punch holes"))
        self.tools_box.addWidget(self.obj_combo_label)
        grid0 = GLay(v_spacing=5, h_spacing=3)
        self.tools_box.addLayout(grid0)
        self.gerber_object_combo = FCComboBox()
        self.gerber_object_combo.setModel(self.app.collection)
        self.gerber_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.gerber_object_combo.is_last = False
        self.gerber_object_combo.obj_type = "Gerber"
        grid0.addWidget(self.gerber_object_combo, 0, 0, 1, 2)
        self.padt_label = FCLabel('%s' % _("Processed Pads Type"), color='blue', bold=True)
        self.padt_label.setToolTip(
            _("The type of pads shape to be processed.\n"
              "If the PCB has many SMD pads with rectangular pads,\n"
              "disable the Rectangular aperture."))
        self.tools_box.addWidget(self.padt_label)
        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)
        pad_all_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(pad_all_grid)
        pad_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0])
        pad_all_grid.addLayout(pad_grid, 0, 0)
        self.select_all_cb = FCCheckBox('%s' % _("All"))
        self.select_all_cb.setToolTip(_("Process all Pads."))
        pad_grid.addWidget(self.select_all_cb, 0, 0)
        self.circular_cb = FCCheckBox('%s' % _("Circular"))
        self.circular_cb.setToolTip(_("Process Circular Pads."))
        pad_grid.addWidget(self.circular_cb, 1, 0)
        self.oblong_cb = FCCheckBox('%s' % _("Oblong"))
        self.oblong_cb.setToolTip(_("Process Oblong Pads."))
        pad_grid.addWidget(self.oblong_cb, 2, 0)
        self.square_cb = FCCheckBox('%s' % _("Square"))
        self.square_cb.setToolTip(_("Process Square Pads."))
        pad_grid.addWidget(self.square_cb, 3, 0)
        self.rectangular_cb = FCCheckBox('%s' % _("Rectangular"))
        self.rectangular_cb.setToolTip(_("Process Rectangular Pads."))
        pad_grid.addWidget(self.rectangular_cb, 4, 0)
        self.other_cb = FCCheckBox('%s' % _("Others"))
        self.other_cb.setToolTip(_("Process pads not in the categories above."))
        pad_grid.addWidget(self.other_cb, 5, 0)
        self.apertures_table = FCTable()
        pad_all_grid.addWidget(self.apertures_table, 0, 1)
        self.apertures_table.setColumnCount(4)
        self.apertures_table.setHorizontalHeaderLabels([_('Code'), _('Type'), _('Size'), 'M'])
        self.apertures_table.setSortingEnabled(False)
        self.apertures_table.setRowCount(0)
        self.apertures_table.resizeColumnsToContents()
        self.apertures_table.resizeRowsToContents()
        self.apertures_table.horizontalHeaderItem(0).setToolTip(_("Aperture Code"))
        self.apertures_table.horizontalHeaderItem(1).setToolTip(_("Type of aperture: circular, rectangle, macros etc"))
        self.apertures_table.horizontalHeaderItem(2).setToolTip(_("Aperture Size:"))
        self.apertures_table.horizontalHeaderItem(3).setToolTip(_("Mark the aperture instances on canvas."))
        sizePolicy = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding, QtWidgets.QSizePolicy.Policy.Preferred)
        self.apertures_table.setSizePolicy(sizePolicy)
        self.apertures_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        self.method_label = FCLabel('%s' % _("Method"), color='red', bold=True)
        self.method_label.setToolTip(
            _("The punch hole source can be:\n"
              "- Excellon Object-> the Excellon object drills center will serve as reference.\n"
              "- Fixed Diameter -> will try to use the pads center as reference adding fixed diameter holes.\n"
              "- Fixed Annular Ring -> will try to keep a set annular ring.\n"
              "- Proportional -> will make a Gerber punch hole having the diameter a percentage of the pad size\n"
              "  (the diameter for circular pads, the smaller side for oblong and rectangular pads)."))
        self.tools_box.addWidget(self.method_label)
        m_frame = FCFrame()
        self.tools_box.addWidget(m_frame)
        grid1 = GLay(v_spacing=5, h_spacing=3)
        m_frame.setLayout(grid1)
        self.method_punch = RadioSet(
            [
                {'label': _('Excellon'), 'value': 'exc'},
                {'label': _("Fixed Diameter"), 'value': 'fixed'},
                {'label': _("Proportional"), 'value': 'prop'},
                {'label': _("Fixed Annular Ring"), 'value': 'ring'}
            ], orientation='vertical', compact=True)
        grid1.addWidget(self.method_punch, 0, 0, 1, 2)
        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 2, 0, 1, 2)
        self.exc_label = FCLabel('%s' % _("Excellon"), bold=True)
        self.exc_label.setToolTip(_("Remove the geometry of Excellon from the Gerber to create the holes in pads."))
        self.exc_combo = FCComboBox()
        self.exc_combo.setModel(self.app.collection)
        self.exc_combo.setRootModelIndex(self.app.collection.index(1, 0, QtCore.QModelIndex()))
        self.exc_combo.is_last = True
        self.exc_combo.obj_type = "Excellon"
        grid1.addWidget(self.exc_label, 4, 0, 1, 2)
        grid1.addWidget(self.exc_combo, 6, 0, 1, 2)
        # Fixed Diameter section
        self.fixed_label = FCLabel('%s' % _("Fixed Diameter"), bold=True)
        self.tools_box.addWidget(self.fixed_label)
        self.fixed_frame = FCFrame()
        self.tools_box.addWidget(self.fixed_frame)
        fix_grid = GLay(v_spacing=5, h_spacing=3)
        self.fixed_frame.setLayout(fix_grid)
        self.dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.dia_entry.set_precision(self.decimals)
        self.dia_entry.set_range(0.0000, 10000.0000)
        self.dia_label = FCLabel('%s:' % _("Value"))
        self.dia_label.setToolTip(_("Fixed hole diameter."))
        fix_grid.addWidget(self.dia_label, 0, 0)
        fix_grid.addWidget(self.dia_entry, 0, 1)
        fix_grid.addWidget(FCLabel("%s" % self.units), 0, 2)

        # Fixed Annular Ring section
        self.ring_label = FCLabel('%s' % _("Fixed Annular Ring"), bold=True)
        self.ring_label.setToolTip(
            _("The size of annular ring.\n"
              "The copper sliver between the hole exterior\n"
              "and the margin of the copper pad."))
        self.tools_box.addWidget(self.ring_label)
        self.ring_frame = FCFrame()
        self.tools_box.addWidget(self.ring_frame)
        self.grid1 = GLay(v_spacing=5, h_spacing=3)
        self.ring_frame.setLayout(self.grid1)
        self.circular_ring_label = FCLabel('%s:' % _("Circular"))
        self.circular_ring_label.setToolTip(_("The size of annular ring for circular pads."))
        self.circular_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.circular_ring_entry.set_precision(self.decimals)
        self.circular_ring_entry.set_range(0.0000, 10000.0000)
        self.grid1.addWidget(self.circular_ring_label, 0, 0)
        self.grid1.addWidget(self.circular_ring_entry, 0, 1)
        self.grid1.addWidget(FCLabel("%s" % self.units), 0, 2)
        self.oblong_ring_label = FCLabel('%s:' % _("Oblong"))
        self.oblong_ring_label.setToolTip(_("The size of annular ring for oblong pads."))
        self.oblong_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.oblong_ring_entry.set_precision(self.decimals)
        self.oblong_ring_entry.set_range(0.0000, 10000.0000)
        self.grid1.addWidget(self.oblong_ring_label, 1, 0)
        self.grid1.addWidget(self.oblong_ring_entry, 1, 1)
        self.grid1.addWidget(FCLabel("%s" % self.units), 1, 2)
        self.square_ring_label = FCLabel('%s:' % _("Square"))
        self.square_ring_label.setToolTip(_("The size of annular ring for square pads."))
        self.square_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.square_ring_entry.set_precision(self.decimals)
        self.square_ring_entry.set_range(0.0000, 10000.0000)
        self.grid1.addWidget(self.square_ring_label, 2, 0)
        self.grid1.addWidget(self.square_ring_entry, 2, 1)
        self.grid1.addWidget(FCLabel("%s" % self.units), 2, 2)
        self.rectangular_ring_label = FCLabel('%s:' % _("Rectangular"))
        self.rectangular_ring_label.setToolTip(_("The size of annular ring for rectangular pads."))
        self.rectangular_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.rectangular_ring_entry.set_precision(self.decimals)
        self.rectangular_ring_entry.set_range(0.0000, 10000.0000)
        self.grid1.addWidget(self.rectangular_ring_label, 3, 0)
        self.grid1.addWidget(self.rectangular_ring_entry, 3, 1)
        self.grid1.addWidget(FCLabel("%s" % self.units), 3, 2)
        self.other_ring_label = FCLabel('%s:' % _("Others"))
        self.other_ring_label.setToolTip(_("The size of annular ring for other pads."))
        self.other_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.other_ring_entry.set_precision(self.decimals)
        self.other_ring_entry.set_range(0.0000, 10000.0000)
        self.grid1.addWidget(self.other_ring_label, 4, 0)
        self.grid1.addWidget(self.other_ring_entry, 4, 1)
        self.grid1.addWidget(FCLabel("%s" % self.units), 4, 2)

        # Proportional section
        self.prop_label = FCLabel('%s' % _("Proportional Diameter"), bold=True)
        self.tools_box.addWidget(self.prop_label)
        self.prop_frame = FCFrame()
        self.tools_box.addWidget(self.prop_frame)
        prop_grid = GLay(v_spacing=5, h_spacing=3)
        self.prop_frame.setLayout(prop_grid)
        self.factor_entry = FCDoubleSpinner(callback=self.confirmation_message, suffix='%')
        self.factor_entry.set_precision(self.decimals)
        self.factor_entry.set_range(1.0000, 99.0000)
        self.factor_entry.setSingleStep(0.1)
        self.factor_label = FCLabel('%s:' % _("Value"))
        self.factor_label.setToolTip(
            _("Proportional Diameter.\n"
              "The hole diameter will be a percentage of the pad size:\n"
              "the diameter for circular pads, the smaller side for oblong and rectangular pads."))
        prop_grid.addWidget(self.factor_label, 0, 0)
        prop_grid.addWidget(self.factor_entry, 0, 1)
        self.sel_label = FCLabel('%s' % _("Selection"), color='green', bold=True)
        self.tools_box.addWidget(self.sel_label)
        self.s_frame = FCFrame()
        self.tools_box.addWidget(self.s_frame)
        grid2 = GLay(v_spacing=5, h_spacing=3)
        self.s_frame.setLayout(grid2)
        self.punch_type_label = FCLabel('%s:' % _("Type"))
        self.punch_type_label.setToolTip(
            _("When the manual type is chosen, the pads to be punched\n"
              "are selected on the canvas but only those that\n"
              "are in the processed pads."))
        self.punch_type_radio = RadioSet([
            {"label": _("Automatic"), "value": "a"},
            {"label": _("Manual"), "value": "m"},
        ])
        grid2.addWidget(self.punch_type_label, 0, 0)
        grid2.addWidget(self.punch_type_radio, 0, 1)
        sel_hlay = QtWidgets.QHBoxLayout()
        self.sel_all_btn = FCButton(_("Select All"))
        self.sel_all_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/select_all.png'))
        self.sel_all_btn.setToolTip(_("Select all available."))
        self.clear_all_btn = FCButton(_("Deselect All"))
        self.clear_all_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/deselect_all32.png'))
        self.clear_all_btn.setToolTip(_("Clear the selection."))
        sel_hlay.addWidget(self.sel_all_btn)
        sel_hlay.addWidget(self.clear_all_btn)
        grid2.addLayout(sel_hlay, 2, 0, 1, 2)
        self.punch_object_button = FCButton(_("Punch Gerber"), bold=True)
        self.punch_object_button.setIcon(QtGui.QIcon(self.app.resource_location + '/punch32.png'))
        self.punch_object_button.setToolTip(
            _("Create a new Gerber object with holes punched in the selected pads."))
        self.tools_box.addWidget(self.punch_object_button)
        self.layout.addStretch(1)
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(_("Will reset the tool parameters."))
        self.layout.addWidget(self.reset_button)
        self.circular_ring_entry.setEnabled(False)
        self.oblong_ring_entry.setEnabled(False)
        self.square_ring_entry.setEnabled(False)
        self.rectangular_ring_entry.setEnabled(False)
        self.other_ring_entry.setEnabled(False)
        self.dia_entry.hide()
        self.dia_label.hide()
        self.factor_label.hide()
        self.factor_entry.hide()
        self.circular_cb.stateChanged.connect(lambda state: self.circular_ring_entry.setDisabled(not state))
        self.oblong_cb.stateChanged.connect(lambda state: self.oblong_ring_entry.setDisabled(not state))
        self.square_cb.stateChanged.connect(lambda state: self.square_ring_entry.setDisabled(not state))
        self.rectangular_cb.stateChanged.connect(lambda state: self.rectangular_ring_entry.setDisabled(not state))
        self.other_cb.stateChanged.connect(lambda state: self.other_ring_entry.setDisabled(not state))

    def build_apertures_table(self, tools, sorted_apertures):
        self.apertures_table.setRowCount(0)
        selected = []
        for ap_code in sorted_apertures:
            ap_type = tools[ap_code]['type']
            if ap_type == 'C':
                if self.circular_cb.get_value() is False:
                    continue
            elif ap_type == 'R':
                if not (self.square_cb.get_value() is True or self.rectangular_cb.get_value() is True):
                    continue
            elif ap_type == 'O':
                if self.oblong_cb.get_value() is False:
                    continue
            elif self.other_cb.get_value() is not True:
                continue
            selected.append(ap_code)
        self.apertures_table.setRowCount(len(selected))
        for row, ap_code in enumerate(selected):
            ap_type = tools[ap_code]['type']
            ap_code_item = QtWidgets.QTableWidgetItem(str(ap_code))
            ap_code_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            ap_type_item = QtWidgets.QTableWidgetItem(str(ap_type))
            ap_type_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            try:
                if ap_type in ('R', 'O'):
                    w_val = self.app.dec_format(float(tools[ap_code]['width']), self.decimals)
                    h_val = self.app.dec_format(float(tools[ap_code]['height']), self.decimals)
                    ap_size_item = QtWidgets.QTableWidgetItem('%sx%s' % (w_val, h_val))
                elif tools[ap_code]['size'] is not None:
                    size_val = self.app.dec_format(float(tools[ap_code]['size']), self.decimals)
                    ap_size_item = QtWidgets.QTableWidgetItem(str(size_val))
                else:
                    ap_size_item = QtWidgets.QTableWidgetItem('')
            except (KeyError, TypeError, ValueError):
                ap_size_item = QtWidgets.QTableWidgetItem('')
            ap_size_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            mark_item = FCCheckBox()
            mark_item.setLayoutDirection(opposite_direction())
            empty_plot_item = QtWidgets.QTableWidgetItem('')
            empty_plot_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.apertures_table.setItem(row, 0, ap_code_item)
            self.apertures_table.setItem(row, 1, ap_type_item)
            self.apertures_table.setItem(row, 2, ap_size_item)
            self.apertures_table.setItem(row, 3, empty_plot_item)
            self.apertures_table.setCellWidget(row, 3, mark_item)
        self.apertures_table.selectColumn(0)
        self.apertures_table.resizeColumnsToContents()
        self.apertures_table.resizeRowsToContents()
        self.apertures_table.verticalHeader().hide()
        horizontal_header = self.apertures_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.apertures_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.apertures_table.setSortingEnabled(False)

    def on_level_changed(self, checked):
        if not checked:
            self.level.setText('%s' % _('Beginner'))
            self.level.setStyleSheet("QToolButton { color: green; }")
            self.sel_label.hide()
            self.s_frame.hide()
        else:
            self.level.setText('%s' % _('Advanced'))
            self.level.setStyleSheet("QToolButton { color: red; }")
            self.sel_label.show()
            self.s_frame.show()

    def on_method(self, val):
        self.exc_label.hide()
        self.exc_combo.hide()
        self.fixed_label.hide()
        self.fixed_frame.hide()
        self.dia_label.hide()
        self.dia_entry.hide()
        self.ring_label.hide()
        self.ring_frame.hide()
        self.prop_label.hide()
        self.prop_frame.hide()
        self.factor_label.hide()
        self.factor_entry.hide()
        if val == 'exc':
            self.exc_label.show()
            self.exc_combo.show()
        elif val == 'fixed':
            self.fixed_label.show()
            self.fixed_frame.show()
            self.dia_label.show()
            self.dia_entry.show()
        elif val == 'ring':
            self.ring_label.show()
            self.ring_frame.show()
        elif val == 'prop':
            self.prop_label.show()
            self.prop_frame.show()
            self.factor_label.show()
            self.factor_entry.show()

    def on_punch_type(self, val):
        if val == 'm':
            self.sel_all_btn.show()
            self.clear_all_btn.show()
        else:
            self.sel_all_btn.hide()
            self.clear_all_btn.hide()

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
