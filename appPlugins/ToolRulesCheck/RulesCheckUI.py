# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 09/27/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    FCDoubleSpinner, OptionalInputSection, FCTable
from appPlugins.ToolRulesCheck.RulesCheckGen import PRESETS, results_status

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


ROLE_LOCATION = QtCore.Qt.ItemDataRole.UserRole


class NumItem(QtWidgets.QTableWidgetItem):
    """A table cell that sorts by its number, not by its text."""

    def __init__(self, value, decimals):
        super().__init__('%.*f' % (decimals, value))
        self.number = float(value)
        self.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        if isinstance(other, NumItem):
            return self.number < other.number
        return super().__lt__(other)


class RulesUI:

    pluginName = _("Check Rules")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.units = str(self.app.app_units or '')
        self.layout = layout

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.layout.addWidget(title_label)

        # https://www.w3schools.com/colors/colors_names.asp

        # #############################################################################################################
        # Select All Frame
        # #############################################################################################################
        select_all_label = FCLabel('%s' % _("Select All"), color='CornflowerBlue', bold=True)
        self.layout.addWidget(select_all_label)

        sel_frame = FCFrame()
        self.layout.addWidget(sel_frame)

        sel_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[1, 0])
        sel_frame.setLayout(sel_grid)

        # Select All Gerber
        gerber_title_lbl = FCLabel('%s:' % _("Gerber"))
        self.all_gerber_obj_cb = FCCheckBox()

        sel_grid.addWidget(gerber_title_lbl, 0, 0)
        sel_grid.addWidget(self.all_gerber_obj_cb, 0, 1)

        # Select All Excellon
        excellon_title_lbl = FCLabel('%s:' % _("Excellon"))
        self.all_exc_obj_cb = FCCheckBox()

        sel_grid.addWidget(excellon_title_lbl, 2, 0)
        sel_grid.addWidget(self.all_exc_obj_cb, 2, 1)

        # Select All Copper Rules
        copper_rules_lbl = FCLabel('%s %s:' % (_("Copper"), _("Rules")))
        self.all_copper_rules_cb = FCCheckBox()

        sel_grid.addWidget(copper_rules_lbl, 4, 0)
        sel_grid.addWidget(self.all_copper_rules_cb, 4, 1)

        # Select All Silk Rules
        silk_rules_lbl = FCLabel('%s %s:' % (_("Silk"), _("Rules")))
        self.all_silk_rules_cb = FCCheckBox()

        sel_grid.addWidget(silk_rules_lbl, 6, 0)
        sel_grid.addWidget(self.all_silk_rules_cb, 6, 1)

        # Select All Soldermask Rules
        solder_rules_lbl = FCLabel('%s %s:' % (_("Soldermask"), _("Rules")))
        self.all_solder_rules_cb = FCCheckBox()

        sel_grid.addWidget(solder_rules_lbl, 8, 0)
        sel_grid.addWidget(self.all_solder_rules_cb, 8, 1)

        # Select All Holes Rules
        holes_rules_lbl = FCLabel('%s %s:' % (_("Holes"), _("Rules")))
        self.all_holes_rules_cb = FCCheckBox()

        sel_grid.addWidget(holes_rules_lbl, 10, 0)
        sel_grid.addWidget(self.all_holes_rules_cb, 10, 1)

        # #############################################################################################################
        # Top Gerber Frame
        # #############################################################################################################
        top_label = FCLabel('%s' % _("Top"), color='red', bold=True)
        self.layout.addWidget(top_label)

        top_frame = FCFrame()
        self.layout.addWidget(top_frame)

        self.top_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 3, 0])
        top_frame.setLayout(self.top_grid)

        self.copper_t_object, self.copper_t_cb = self._object_row(
            self.top_grid, 0, _("Copper"), "Gerber", 0, _("The Top Gerber Copper object for which rules are checked."))
        self.sm_t_object, self.sm_t_cb = self._object_row(
            self.top_grid, 2, _("Solder Mask"), "Gerber", 0,
            _("The Top Gerber Solder Mask object for which rules are checked."))
        self.ss_t_object, self.ss_t_cb = self._object_row(
            self.top_grid, 4, _("Silk"), "Gerber", 0, _("The Top Gerber Silkscreen object for which rules are checked."))

        # #############################################################################################################
        # Bottom Gerber Frame
        # #############################################################################################################
        bottom_label = FCLabel('%s' % _("Bottom"), color='blue', bold=True)
        self.layout.addWidget(bottom_label)

        bottom_frame = FCFrame()
        self.layout.addWidget(bottom_frame)

        self.bottom_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 3, 0])
        bottom_frame.setLayout(self.bottom_grid)

        self.copper_b_object, self.copper_b_cb = self._object_row(
            self.bottom_grid, 0, _("Copper"), "Gerber", 0,
            _("The Bottom Gerber Copper object for which rules are checked."))
        self.sm_b_object, self.sm_b_cb = self._object_row(
            self.bottom_grid, 2, _("Solder Mask"), "Gerber", 0,
            _("The Bottom Gerber Solder Mask object for which rules are checked."))
        self.ss_b_object, self.ss_b_cb = self._object_row(
            self.bottom_grid, 4, _("Silk"), "Gerber", 0,
            _("The Bottom Gerber Silkscreen object for which rules are checked."))

        # #############################################################################################################
        # Outline Frame
        # #############################################################################################################
        outline_label = FCLabel('%s' % _("Outline"), color='green', bold=True)
        self.layout.addWidget(outline_label)

        outline_frame = FCFrame()
        self.layout.addWidget(outline_frame)

        self.outline_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 3, 0])
        outline_frame.setLayout(self.outline_grid)

        self.outline_object, self.out_cb = self._object_row(
            self.outline_grid, 0, _("Outline"), "Gerber", 0,
            _("The Gerber Outline (Cutout) object for which rules are checked."))

        # #############################################################################################################
        # Excellon Frame
        # #############################################################################################################
        exc_label = FCLabel('%s' % _("Excellon"), color='brown', bold=True)
        exc_label.setToolTip(
            _("Excellon objects for which to check rules.")
        )
        self.layout.addWidget(exc_label)

        exc_frame = FCFrame()
        self.layout.addWidget(exc_frame)

        self.exc_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 3, 0])
        exc_frame.setLayout(self.exc_grid)

        self.e1_object, self.e1_cb = self._object_row(
            self.exc_grid, 0, "PTH", "Excellon", 1,
            _("Excellon object for which to check rules.\n"
              "Holds the plated holes or a general Excellon file content."))
        self.e2_object, self.e2_cb = self._object_row(
            self.exc_grid, 2, "NPTH", "Excellon", 1,
            _("Excellon object for which to check rules.\n"
              "Holds the non-plated holes."))

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.layout.addWidget(separator_line)

        # Presets: fill the rule values; editing a value goes back to Custom
        preset_label = FCLabel('%s:' % _("Preset"), bold=True)
        preset_label.setToolTip(
            _("Fill the rule values with typical values of a process.\n"
              "These are typical published values, not a guarantee of the manufacturer."))
        self.preset_combo = FCComboBox()
        self.preset_combo.addItems([_("Custom")] + list(PRESETS))
        self.preset_combo.setToolTip(preset_label.toolTip())
        preset_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1])
        preset_grid.addWidget(preset_label, 0, 0)
        preset_grid.addWidget(self.preset_combo, 0, 1)
        self.layout.addLayout(preset_grid)

        # #############################################################################################################
        # Copper Rules Frame
        # #############################################################################################################
        rules_copper_label = FCLabel('%s %s' % (_("Copper"), _("Rules")), color='darkorange', bold=True)
        self.layout.addWidget(rules_copper_label)

        copper_frame = FCFrame()
        self.layout.addWidget(copper_frame)

        self.copper_grid = GLay(v_spacing=5, h_spacing=3)
        copper_frame.setLayout(self.copper_grid)

        (self.trace_size_cb, self.trace_size_lbl, self.trace_size_entry, self.trace_size_units,
         self.ts) = self._rule_row(
            self.copper_grid, 0, _("Trace Size"),
            _("This checks if the minimum size for traces is met."),
            _("Minimum acceptable trace width. Flashed pads are not traces."))

        (self.clearance_copper2copper_cb, self.clearance_copper2copper_lbl, self.clearance_copper2copper_entry,
         self.clearance_copper2copper_units, self.c2c) = self._rule_row(
            self.copper_grid, 4, _("Copper to Copper clearance"),
            _("This checks if the minimum clearance between copper\n"
              "features is met."),
            _("Minimum acceptable gap between two copper features of the same layer."))

        (self.clearance_copper2ol_cb, self.clearance_copper2ol_lbl, self.clearance_copper2ol_entry,
         self.clearance_copper2ol_units, self.c2ol) = self._rule_row(
            self.copper_grid, 8, _("Copper to Outline clearance"),
            _("This checks if the minimum clearance between copper\n"
              "features and the outline is met."),
            _("Minimum acceptable distance between copper features and the board outline."))

        (self.ring_integrity_cb, self.ring_integrity_lbl, self.ring_integrity_entry,
         self.ring_integrity_units, self.anr) = self._rule_row(
            self.copper_grid, 12, _("Minimum Annular Ring"),
            _("This checks if the minimum copper ring left by drilling\n"
              "a hole into a pad is met."),
            _("Minimum acceptable width of the copper ring around a hole."))

        (self.iso_cb, self.iso_lbl, self.iso_entry, self.iso_units, self.iso) = self._rule_row(
            self.copper_grid, 16, _("Copper gap vs isolation tool"),
            _("This checks if the gaps between copper features are wide\n"
              "enough for the isolation tool to pass."),
            _("Diameter of the isolation tool. Copper gaps narrower than this cannot be isolated."))

        # #############################################################################################################
        # Silk Frame
        # #############################################################################################################
        silk_copper_label = FCLabel('%s %s' % (_("Silk"), _("Rules")), color='teal', bold=True)
        self.layout.addWidget(silk_copper_label)

        silk_frame = FCFrame()
        self.layout.addWidget(silk_frame)

        self.silk_grid = GLay(v_spacing=5, h_spacing=3)
        silk_frame.setLayout(self.silk_grid)

        (self.clearance_silk2silk_cb, self.clearance_silk2silk_lbl, self.clearance_silk2silk_entry,
         self.clearance_silk2silk_units, self.s2s) = self._rule_row(
            self.silk_grid, 0, _("Silk to Silk Clearance"),
            _("This checks if the minimum clearance between silkscreen\n"
              "features and silkscreen features is met."),
            _("Minimum acceptable gap between two silkscreen features of the same layer."))

        (self.clearance_silk2sm_cb, self.clearance_silk2sm_lbl, self.clearance_silk2sm_entry,
         self.clearance_silk2sm_units, self.s2sm) = self._rule_row(
            self.silk_grid, 4, _("Silk to Solder Mask Clearance"),
            _("This checks if the minimum clearance between silkscreen\n"
              "features and soldermask features is met."),
            _("Minimum acceptable distance between silkscreen features and solder mask openings."))

        (self.clearance_silk2ol_cb, self.clearance_silk2ol_lbl, self.clearance_silk2ol_entry,
         self.clearance_silk2ol_units, self.s2ol) = self._rule_row(
            self.silk_grid, 8, _("Silk to Outline Clearance"),
            _("This checks if the minimum clearance between silk\n"
              "features and the outline is met."),
            _("Minimum acceptable distance between silkscreen features and the board outline."))

        # #############################################################################################################
        # Soldermask Frame
        # #############################################################################################################
        sm_copper_label = FCLabel('%s %s' % (_("Soldermask"), _("Rules")), color='magenta', bold=True)
        self.layout.addWidget(sm_copper_label)

        solder_frame = FCFrame()
        self.layout.addWidget(solder_frame)

        self.solder_grid = GLay(v_spacing=5, h_spacing=3)
        solder_frame.setLayout(self.solder_grid)

        (self.clearance_sm2sm_cb, self.clearance_sm2sm_lbl, self.clearance_sm2sm_entry,
         self.clearance_sm2sm_units, self.sm2sm) = self._rule_row(
            self.solder_grid, 0, _("Minimum Solder Mask Sliver"),
            _("This checks if the minimum clearance between soldermask\n"
              "features and soldermask features is met."),
            _("Minimum acceptable width of the solder mask left between two openings."))

        # #############################################################################################################
        # Holes Frame
        # #############################################################################################################
        holes_copper_label = FCLabel('%s %s' % (_("Holes"), _("Rules")), color='brown', bold=True)
        self.layout.addWidget(holes_copper_label)

        holes_frame = FCFrame()
        self.layout.addWidget(holes_frame)

        self.holes_grid = GLay(v_spacing=5, h_spacing=3)
        holes_frame.setLayout(self.holes_grid)

        (self.clearance_d2d_cb, self.clearance_d2d_lbl, self.clearance_d2d_entry,
         self.clearance_d2d_units, self.d2d) = self._rule_row(
            self.holes_grid, 0, _("Hole to Hole Clearance"),
            _("This checks if the minimum clearance between a drill hole\n"
              "and another drill hole is met."),
            _("Minimum acceptable wall between the edges of two holes or slots."))

        (self.drill_size_cb, self.drill_size_lbl, self.drill_size_entry,
         self.drill_size_units, self.ds) = self._rule_row(
            self.holes_grid, 4, _("Hole Size"),
            _("This checks if the drill holes\n"
              "sizes are above the threshold."),
            _("Minimum acceptable drill diameter."))

        (self.slot_size_cb, self.slot_size_lbl, self.slot_size_entry,
         self.slot_size_units, self.sl) = self._rule_row(
            self.holes_grid, 8, _("Minimum Slot Width"),
            _("This checks if the width of the slots is above the threshold."),
            _("Minimum acceptable slot width (the tool diameter that cuts the slot)."))

        (self.hole2outline_cb, self.hole2outline_lbl, self.hole2outline_entry,
         self.hole2outline_units, self.h2o) = self._rule_row(
            self.holes_grid, 12, _("Hole to Outline clearance"),
            _("This checks if the minimum distance between a hole\n"
              "and the board outline is met."),
            _("Minimum acceptable distance between the edge of a hole and the board outline."))

        (self.hole2copper_cb, self.hole2copper_lbl, self.hole2copper_entry,
         self.hole2copper_units, self.h2c) = self._rule_row(
            self.holes_grid, 16, _("Hole to Copper clearance"),
            _("This checks if the minimum distance between a hole\n"
              "and copper that is not its own pad is met."),
            _("Minimum acceptable distance between the edge of a hole and foreign copper."))

        # Buttons
        self.run_button = FCButton(_("Run Rules Check"), bold=True)
        self.run_button.setIcon(QtGui.QIcon(self.app.resource_location + '/rules32.png'))
        self.run_button.setToolTip(
            _("Run the enabled rules on the selected objects\n"
              "and create a document with the report.")
        )
        self.layout.addWidget(self.run_button)

        # Results table
        self.cancel_button = FCButton(_("Cancel"))
        self.cancel_button.setToolTip(_("Stop the running check and discard its results."))
        self.cancel_button.setEnabled(False)
        self.export_button = FCButton(_("Export CSV"))
        self.export_button.setToolTip(_("Save the violations of the last check as a CSV file."))
        self.export_button.setEnabled(False)
        buttons_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[1, 1])
        buttons_grid.addWidget(self.cancel_button, 0, 0)
        buttons_grid.addWidget(self.export_button, 0, 1)
        self.layout.addLayout(buttons_grid)

        self.summary_label = FCLabel(self.summary_text(0, 0))
        self.layout.addWidget(self.summary_label)

        self.results_table = FCTable()
        self.results_table.setColumnCount(6)
        self.results_table.setHorizontalHeaderLabels([
            _("Rule"), _("Layer"), 'X (%s)' % self.units, 'Y (%s)' % self.units, '%s (%s)' % (_("Value"), self.units),
            '%s (%s)' % (_("Limit"), self.units)])
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.results_table.setSortingEnabled(True)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setMinimumHeight(180)
        self.layout.addWidget(self.results_table)

        GLay.set_common_column_size(
            [self.copper_grid, self.solder_grid, self.silk_grid, self.exc_grid, self.top_grid, self.bottom_grid,
             self.outline_grid, self.holes_grid], 0)

        self.layout.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

        # #############################################################################################################
        # SIGNALS: each Select All checkbox follows its children
        # #############################################################################################################
        self._bulk_change = False
        self.select_groups = [
            (self.all_gerber_obj_cb,
             [self.copper_t_cb, self.sm_t_cb, self.ss_t_cb, self.copper_b_cb, self.sm_b_cb, self.ss_b_cb,
              self.out_cb]),
            (self.all_exc_obj_cb, [self.e1_cb, self.e2_cb]),
            (self.all_copper_rules_cb,
             [self.trace_size_cb, self.clearance_copper2copper_cb, self.clearance_copper2ol_cb,
              self.ring_integrity_cb, self.iso_cb]),
            (self.all_silk_rules_cb,
             [self.clearance_silk2silk_cb, self.clearance_silk2sm_cb, self.clearance_silk2ol_cb]),
            (self.all_solder_rules_cb, [self.clearance_sm2sm_cb]),
            (self.all_holes_rules_cb,
             [self.clearance_d2d_cb, self.drill_size_cb, self.slot_size_cb, self.hole2outline_cb,
              self.hole2copper_cb]),
        ]
        for all_cb, children in self.select_groups:
            all_cb.clicked.connect(lambda checked, g=children: self.set_group(g, checked))
            for child in children:
                child.stateChanged.connect(self.sync_select_all)

        # #################################### FINSIHED GUI ###########################
        # #############################################################################

    def _object_row(self, grid, row, title, obj_type, root_row, tooltip):
        combo = FCComboBox()
        combo.setModel(self.app.collection)
        combo.setRootModelIndex(self.app.collection.index(root_row, 0, QtCore.QModelIndex()))
        combo.is_last = True
        combo.obj_type = obj_type

        label = FCLabel('%s:' % title)
        label.setToolTip(tooltip)
        cb = FCCheckBox()

        grid.addWidget(label, row, 0)
        grid.addWidget(combo, row, 1)
        grid.addWidget(cb, row, 2)
        return combo, cb

    def _rule_row(self, grid, row, title, title_tip, value_tip):
        cb = FCCheckBox('%s:' % title)
        cb.setToolTip(title_tip)
        grid.addWidget(cb, row, 0, 1, 3)

        label = FCLabel('%s:' % _("Value"))
        label.setToolTip(value_tip)

        entry = FCDoubleSpinner(callback=self.confirmation_message)
        entry.set_range(0.0000, 10000.0000)
        entry.set_precision(self.decimals)
        entry.setSingleStep(0.1)
        entry.setToolTip(value_tip)

        units = FCLabel(self.units)

        grid.addWidget(label, row + 2, 0)
        grid.addWidget(entry, row + 2, 1)
        grid.addWidget(units, row + 2, 2)

        section = OptionalInputSection(cb, [label, entry, units])
        return cb, label, entry, units, section

    @staticmethod
    def summary_text(violations, rules):
        return _("%d violations in %d rules") % (violations, rules)

    def fill_results(self, results):
        """One row per violation; a rule that could not run or had nothing to check gets one row with the status."""
        table = self.results_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        total = 0
        failed_rules = 0
        for res in results:
            if not isinstance(res, dict):
                continue
            status = results_status(res)
            limit = res.get('limit') or 0.0
            if status == 'FAILED':
                failed_rules += 1
                for vio in res['violations']:
                    total += 1
                    self._add_row(res['rule'], vio['layer'], vio['x'], vio['y'], vio['value'], limit)
            elif status in ('ERROR', 'NOTHING TO CHECK'):
                label = {'ERROR': _("ERROR"), 'NOTHING TO CHECK': _("NOTHING TO CHECK")}[status]
                self._add_row(res['rule'], label, None, None, None, limit)
        table.setSortingEnabled(True)
        self.summary_label.setText(self.summary_text(total, failed_rules))

    def _add_row(self, rule, layer, x, y, value, limit):
        table = self.results_table
        row = table.rowCount()
        table.insertRow(row)
        rule_item = QtWidgets.QTableWidgetItem(str(rule))
        if x is not None:
            rule_item.setData(ROLE_LOCATION, (float(x), float(y), float(limit)))
        table.setItem(row, 0, rule_item)
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(layer)))
        for col, num in ((2, x), (3, y), (4, value), (5, limit)):
            if num is None:
                table.setItem(row, col, QtWidgets.QTableWidgetItem(''))
            else:
                table.setItem(row, col, NumItem(num, self.decimals))

    def set_group(self, children, checked):
        self._bulk_change = True
        try:
            for cb in children:
                cb.setChecked(bool(checked))
        finally:
            self._bulk_change = False
        self.sync_select_all()

    def sync_select_all(self, *args):
        # a Select All checkbox is checked only when all of its children are checked
        if self._bulk_change:
            return
        for all_cb, children in self.select_groups:
            all_checked = all(cb.isChecked() for cb in children)
            if all_cb.isChecked() != all_checked:
                all_cb.blockSignals(True)
                all_cb.setChecked(all_checked)
                all_cb.blockSignals(False)

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
