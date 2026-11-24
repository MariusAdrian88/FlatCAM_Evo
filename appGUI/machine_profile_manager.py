# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""The Machine Profiles manager: a tab in the Plot Area that lists, edits, duplicates and deletes the profiles."""

import builtins
import gettext
from dataclasses import replace

from PyQt6 import QtCore, QtWidgets

from appCommon.machine_profile.facts import active_profile_of, design_to_machine
from appCommon.machine_profile.profile import AXIS_NAMES, CORNERS, MachineProfile, delete_profile, list_profiles, save_profile
from appCommon.machine_profile.records import ViolationKind
from appGUI.GUIElements import FCButton, FCCheckBox, FCComboBox, FCDoubleSpinner, FCEntry, FCFileSaveDialog, FCLabel
from appGUI.machine_profile_ui import ExclusionDrawer, apply_machine_profile_locks, profiles_folder, \
    refresh_profile_selector

if '_' not in builtins.__dict__:
    _ = gettext.gettext

STRATEGIES = ('forbid', 'around', 'over')

_LIMITS = (('feed_xy_max', 'Max XY feed'), ('feed_z_max', 'Max Z feed'), ('rapid_max', 'Max rapid feed'),
           ('rpm_min', 'Min spindle speed'), ('rpm_max', 'Max spindle speed'), ('cut_depth_max', 'Max cut depth'))
_ECUT_FIELDS = (('axis_velocity_max', 'Max speed [mm/min]'), ('axis_accel_max', 'Max acceleration [mm/s2]'),
                ('soft_limit_min', 'Soft limit min [mm]'), ('soft_limit_max', 'Soft limit max [mm]'))
_STATIONS = (('toolchange', 'Tool change', 3), ('end', 'End position', 3), ('probe_plate', 'Probe plate', 2))
_KIND_LABELS = {
    ViolationKind.OUT_OF_BED: 'Out of bed', ViolationKind.EXCLUSION: 'Exclusion zone', ViolationKind.FEED: 'Feed',
    ViolationKind.RAPID: 'Rapid feed', ViolationKind.RPM: 'Spindle speed', ViolationKind.LASER_POWER: 'Laser power',
    ViolationKind.CUT_DEPTH: 'Cut depth', ViolationKind.Z_TRAVEL: 'Z travel', ViolationKind.UNVERIFIED: 'Unverified',
}


def _spin(low=-100000.0, high=100000.0):
    spin = FCDoubleSpinner()
    spin.lineEdit().setValidator(None)      # the default validator allows no minus sign and 2 decimals
    spin.setDecimals(4)
    spin.setRange(low, high)
    return spin


class MachineProfilesTab(QtWidgets.QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.profile = None         # the profile of the form; None: a new one that is not saved yet
        self.setObjectName("machine_profiles_tab")

        root = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        left.addWidget(self.list)
        for text, slot in ((_("New"), self.on_new), (_("Duplicate"), self.on_duplicate),
                           (_("Delete"), self.on_delete), (_("Save"), self.on_save)):
            button = FCButton(text)
            button.clicked.connect(slot)
            left.addWidget(button)
        root.addLayout(left, 1)

        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 3)
        self._build_general()
        self._build_bed()
        self._build_stations()
        self._build_limits()
        self._build_ecut()
        self._build_exclusions()
        self._build_calibration()
        self._build_check()
        self.exclusions = []        # the areas of the form: {name, polygon (M, mm), strategy, over_z}
        self.drawer = None

        self.list.currentItemChanged.connect(self.on_select)
        self.refresh_list()
        self.on_new()

    # ---- the pages ----
    def _page(self, title):
        page = QtWidgets.QWidget()
        form = QtWidgets.QGridLayout(page)
        form.setRowStretch(99, 1)
        self.tabs.addTab(page, title)
        return form

    def _build_general(self):
        form = self._page(_("General"))
        self.name_entry = FCEntry()
        form.addWidget(FCLabel(_("Name")), 0, 0)
        form.addWidget(self.name_entry, 0, 1)
        form.addWidget(FCLabel(_("Units")), 1, 0)
        form.addWidget(FCLabel("mm"), 1, 1)

    def _build_bed(self):
        form = self._page(_("Bed"))
        self.bed_x = _spin(0.0)
        self.bed_y = _spin(0.0)
        self.origin_corner = FCComboBox()
        self.origin_corner.addItems(list(CORNERS))
        self.z_travel = _spin(0.0)
        self.work_offset = [_spin() for _i in range(3)]
        rows = ((_("Bed X"), [self.bed_x]), (_("Bed Y"), [self.bed_y]), (_("Origin corner"), [self.origin_corner]),
                (_("Z travel"), [self.z_travel]), (_("Work offset X, Y, Z"), self.work_offset))
        for row, (text, widgets) in enumerate(rows):
            form.addWidget(FCLabel(text), row, 0)
            for col, widget in enumerate(widgets):
                form.addWidget(widget, row, 1 + col)

    def _build_stations(self):
        form = self._page(_("Fixed stations"))
        self.station = {}
        for row, (field, text, count) in enumerate(_STATIONS):
            spins = [_spin() for _i in range(count)]
            unset = FCCheckBox(_("unset"))
            unset.toggled.connect(lambda checked, spins=spins: [s.setEnabled(not checked) for s in spins])
            self.station[field] = (spins, unset)
            form.addWidget(FCLabel(text), row, 0)
            for col, spin in enumerate(spins):
                form.addWidget(spin, row, 1 + col)
            form.addWidget(unset, row, 4)

    def _build_limits(self):
        form = self._page(_("Limits"))
        self.limit = {}
        for row, (field, text) in enumerate(_LIMITS):
            spin = _spin(0.0)
            unset = FCCheckBox(_("unset"))
            unset.toggled.connect(lambda checked, spin=spin: spin.setEnabled(not checked))
            self.limit[field] = (spin, unset)
            form.addWidget(FCLabel(_(text)), row, 0)
            form.addWidget(spin, row, 1)
            form.addWidget(unset, row, 2)

    def _build_ecut(self):
        form = self._page(_("E-Cut"))
        self.ecut = {}
        form.addWidget(FCLabel(_("Per axis limits of the E-Cut. Machine frame. Apply them in the E-Cut machine "
                                 "settings of the G-code Sender.")), 0, 0, 1, 5)
        for col, name in enumerate(AXIS_NAMES):
            form.addWidget(FCLabel(name), 1, 1 + col * 2)
        for row, (field, text) in enumerate(_ECUT_FIELDS, 2):
            form.addWidget(FCLabel(_(text)), row, 0)
            for col, axis in enumerate(AXIS_NAMES):
                spin = _spin()
                unset = FCCheckBox(_("unset"))
                unset.toggled.connect(lambda checked, spin=spin: spin.setEnabled(not checked))
                self.ecut[(field, axis)] = (spin, unset)
                form.addWidget(spin, row, 1 + col * 2)
                form.addWidget(unset, row, 2 + col * 2)

    def _build_exclusions(self):
        form = self._page(_("Exclusions"))
        self.excl_table = QtWidgets.QTableWidget(0, 4)
        self.excl_table.setHorizontalHeaderLabels([_("Name"), _("Strategy"), _("Over Z"), _("Vertices")])
        self.excl_table.horizontalHeader().setStretchLastSection(True)
        self.excl_table.itemChanged.connect(self.on_excl_name_changed)
        form.addWidget(self.excl_table, 0, 0, 1, 3)
        for col, (text, slot) in enumerate(((_("Add rectangle"), self.on_add_rectangle),
                                            (_("Draw on canvas"), self.on_draw_area),
                                            (_("Delete"), self.on_delete_area))):
            button = FCButton(text)
            button.clicked.connect(slot)
            form.addWidget(button, 1, col)
        form.setRowStretch(99, 0)
        form.setRowStretch(0, 1)

    def refresh_exclusions(self):
        self.excl_table.blockSignals(True)
        self.excl_table.setRowCount(len(self.exclusions))
        for row, area in enumerate(self.exclusions):
            self.excl_table.setItem(row, 0, QtWidgets.QTableWidgetItem(area.get('name', '')))
            combo = FCComboBox()
            combo.addItems(list(STRATEGIES))
            combo.set_value(area.get('strategy', 'forbid'))
            spin = _spin(0.0)
            spin.setValue(area.get('over_z') or 0.0)
            spin.setEnabled(combo.get_value() == 'over')
            combo.currentTextChanged.connect(lambda text, a=area, s=spin: self.on_strategy_changed(a, s, text))
            spin.valueChanged.connect(lambda value, a=area: self.on_over_z_changed(a, value))
            self.excl_table.setCellWidget(row, 1, combo)
            self.excl_table.setCellWidget(row, 2, spin)
            vertices = QtWidgets.QTableWidgetItem(str(len(area.get('polygon', []))))
            vertices.setFlags(vertices.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.excl_table.setItem(row, 3, vertices)
        self.excl_table.blockSignals(False)

    def on_excl_name_changed(self, item):
        if item.column() == 0 and item.row() < len(self.exclusions):
            self.exclusions[item.row()]['name'] = item.text()

    def on_strategy_changed(self, area, spin, strategy):
        area['strategy'] = strategy
        spin.setEnabled(strategy == 'over')
        area['over_z'] = spin.value() if strategy == 'over' else None

    def on_over_z_changed(self, area, value):
        if area.get('strategy') == 'over':
            area['over_z'] = value

    def add_area(self, polygon_mm):
        self.exclusions.append({'name': '%s %d' % (_("Area"), len(self.exclusions) + 1), 'polygon': polygon_mm,
                                'strategy': 'forbid', 'over_z': None})
        self.refresh_exclusions()

    def on_add_rectangle(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(_("Add rectangle"))
        grid = QtWidgets.QGridLayout(dialog)
        spins = []
        for row, text in enumerate(("x0", "y0", "x1", "y1")):
            spin = _spin()
            spins.append(spin)
            grid.addWidget(FCLabel('%s [mm]' % text), row, 0)
            grid.addWidget(spin, row, 1)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                         QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        grid.addWidget(box, 4, 0, 1, 2)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        x0, y0, x1, y1 = (spin.value() for spin in spins)
        self.add_area([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    def on_draw_area(self):
        if self.app.__dict__.get('plotcanvas') is None:
            return
        self.drawer = ExclusionDrawer(self.app, self.on_area_drawn)
        self.drawer.start()

    def on_area_drawn(self, points):
        offset = tuple(spin.value() for spin in self.work_offset)
        self.add_area(design_to_machine(points, offset, self.app.app_units))

    def on_delete_area(self):
        row = self.excl_table.currentRow()
        if 0 <= row < len(self.exclusions):
            del self.exclusions[row]
            self.refresh_exclusions()

    # ---- the calibration wizard: Pattern, Measure, Result, Check cut ----
    def _build_calibration(self):
        form = self._page(_("Calibration"))
        self.cal_side = _spin(1.0)
        self.cal_side.setValue(100.0)
        self.cal_depth = _spin()
        self.cal_depth.setValue(-0.1)
        self.cal_feed = _spin(0.0)
        self.cal_feed.setValue(120.0)
        self.cal_tooldia = _spin(0.0)
        self.cal_tooldia.setValue(1.0)
        self.cal_travel = _spin()
        self.cal_travel.setValue(2.0)
        self.cal_pp = FCComboBox()
        self.cal_pp.addItems(sorted(self.app.__dict__.get('preprocessors') or {}))
        rows = ((_("1. Pattern: side L [mm]"), self.cal_side), (_("Cut depth [mm]"), self.cal_depth),
                (_("Travel Z [mm]"), self.cal_travel), (_("Feed [mm/min]"), self.cal_feed),
                (_("Tool diameter [mm]"), self.cal_tooldia), (_("Preprocessor"), self.cal_pp))
        for row, (text, widget) in enumerate(rows):
            form.addWidget(FCLabel(text), row, 0)
            form.addWidget(widget, row, 1)
        make = FCButton(_("Make G-code"))
        make.clicked.connect(self.on_cal_make)
        form.addWidget(make, len(rows), 0, 1, 2)

        row = len(rows) + 1
        form.addWidget(FCLabel(_("2. Measure the cut square [mm]. d1 is the diagonal from the zero corner "
                                 "to the opposite corner, d2 the other one.")), row, 0, 1, 2)
        self.cal_measure = {}
        for i, (key, text) in enumerate((('a', _("a (X side)")), ('b', _("b (Y side)")),
                                         ('d1', _("d1 (zero corner diagonal)")), ('d2', _("d2 (other diagonal)")))):
            spin = _spin(0.0)
            self.cal_measure[key] = spin
            form.addWidget(FCLabel(text), row + 1 + i, 0)
            form.addWidget(spin, row + 1 + i, 1)
        row += 5
        solve = FCButton(_("3. Result"))
        solve.clicked.connect(self.on_cal_solve)
        form.addWidget(solve, row, 0, 1, 2)
        self.cal_result = FCLabel('')
        self.cal_result.setWordWrap(True)
        form.addWidget(self.cal_result, row + 1, 0, 1, 2)
        self.cal_solution = None
        apply_btn = FCButton(_("Apply"))
        apply_btn.clicked.connect(self.on_cal_apply)
        check = FCButton(_("4. Check cut"))
        check.clicked.connect(self.on_cal_check_cut)
        form.addWidget(apply_btn, row + 2, 0)
        form.addWidget(check, row + 2, 1)

    def _cal_inform(self, kind, text):
        self.app.inform.emit('[%s] %s' % (kind, text))

    def _cal_pattern(self):
        from appCommon.machine_profile.calibrate import pattern_gcode
        text = pattern_gcode(self.cal_side.value(), self.cal_depth.value(), self.cal_travel.value(),
                             self.cal_feed.value(), 4)
        return '(TOOL DIAMETER: %s mm)\n%s' % (self.cal_tooldia.value(), text)

    def _cal_save_text(self, caption, text):
        filename = FCFileSaveDialog.get_saved_filename(caption=caption, ext_filter="G-Code Files (*.nc *.gcode);;"
                                                       "All Files (*.*)")[0]
        if not filename:
            self._cal_inform('WARNING_NOTCL', _("Cancelled."))
            return
        try:
            with open(filename, 'w') as f:
                f.write(text)
        except OSError as error:
            self._cal_inform('ERROR_NOTCL', '%s: %s' % (_("Could not save the file"), error))
            return
        self._cal_inform('success', '%s: %s' % (_("Saved to"), filename))

    def on_cal_make(self):
        """The square outline, saved with the purpose CALIBRATION: no correction at all."""
        if self.profile is None:
            self._cal_inform('WARNING_NOTCL', _("Save the machine profile first."))
            return
        gate = self._cal_gate_pattern()
        if not gate.ok:
            self._cal_inform('ERROR_NOTCL', gate.error)
            return
        # the profile "None" or a missing profile: no header, the text as it is
        self._cal_save_text(_("Save the calibration pattern"), gate.text if gate.text is not None
                            else self._cal_pattern())

    def _cal_gate_pattern(self):
        from appCommon.machine_profile.writer import calibration_file_text
        folder = self.app.options.get('global_machine_profiles_folder') or ''
        return calibration_file_text(self.profile.name, folder, '' if folder else self.app.data_path,
                                     self._cal_pattern(), self.cal_pp.get_value() or None, 'mm')

    def on_cal_solve(self):
        from appCommon.machine_profile.calibrate import CalibrationError, solve_calibration
        self.cal_solution = None
        self.cal_result.setStyleSheet('')
        values = {key: spin.value() for key, spin in self.cal_measure.items()}
        try:
            solution = solve_calibration(self.cal_side.value(), values['a'], values['b'], values['d1'],
                                         values['d2'])
        except CalibrationError as error:
            self.cal_result.setStyleSheet('color: #d03030')
            self.cal_result.set_value(str(error))
            return
        self.cal_solution = solution
        self.cal_result.set_value('sx %.5f, sy %.5f, %s %.4f deg, %s %.4f mm' % (
            solution['sx'], solution['sy'], _("skew"), solution['epsilon_deg'], _("residual"), solution['residual']))

    def on_cal_apply(self):
        """Store the calibration (matrix, date, residual, enabled) in the profile and save it."""
        if self.cal_solution is None or self.profile is None:
            self._cal_inform('WARNING_NOTCL', _("Save the machine profile and solve the measurements first."))
            return
        solution = self.cal_solution
        calibration = {'matrix': solution['matrix'], 'date': solution['date'], 'residual': solution['residual'],
                       'enabled': True}
        profile = replace(self.profile, calibration=calibration)
        try:
            save_profile(profile, profiles_folder(self.app))
        except (ValueError, OSError) as error:
            self._cal_inform('ERROR_NOTCL', '%s: %s' % (_("The machine profile is not saved"), error))
            return
        self.profile = profile
        active = active_profile_of(self.app)
        if active is not None and active.name == profile.name:
            apply_machine_profile_locks(self.app)
        self._cal_inform('success', '%s: %s' % (_("Calibration applied"), profile.name))

    def on_cal_check_cut(self):
        """The same square as a file output (corrected by the calibration) for a second cut."""
        from appCommon.machine_profile.writer import gate_file_text
        if self.profile is None or not (self.profile.calibration or {}).get('enabled'):
            self._cal_inform('WARNING_NOTCL', _("Apply a calibration first."))
            return
        folder = self.app.options.get('global_machine_profiles_folder') or ''
        gate = gate_file_text(self.profile.name, folder, '' if folder else self.app.data_path, self._cal_pattern(),
                              self.cal_pp.get_value() or None, 'mm', correct=True, uncorrected_source=True)
        if not gate.ok:
            self._cal_inform('ERROR_NOTCL', gate.error)
            return
        self._cal_save_text(_("Save the check cut"), gate.text if gate.text is not None else self._cal_pattern())

    def _build_check(self):
        form = self._page(_("Check"))
        self.rule = {}
        for row, kind in enumerate(ViolationKind):
            combo = FCComboBox()
            combo.addItems(['warn', 'reject'])
            self.rule[kind.value] = combo
            form.addWidget(FCLabel(_(_KIND_LABELS[kind])), row, 0)
            form.addWidget(combo, row, 1)

    # ---- the list ----
    def refresh_list(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for profile in list_profiles(profiles_folder(self.app)):
            self.list.addItem(profile.name)
        self.list.blockSignals(False)
        if select is not None:
            for row in range(self.list.count()):
                if self.list.item(row).text() == select:
                    self.list.setCurrentRow(row)
        refresh_profile_selector(self.app)

    def on_select(self, item, _previous=None):
        if item is None:
            return
        for profile in list_profiles(profiles_folder(self.app)):
            if profile.name == item.text():
                self.profile = profile
                self.to_form(profile)
                return

    def on_new(self):
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self.profile = None
        self.to_form(MachineProfile())

    def on_duplicate(self):
        if self.profile is None:
            return
        self.profile = None
        self.name_entry.set_value(self.name_entry.get_value() + ' ' + _("copy"))
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)

    def on_delete(self):
        item = self.list.currentItem()
        if item is None:
            return
        name = item.text()
        try:
            delete_profile(name, profiles_folder(self.app))
        except OSError as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not delete the machine profile"), error))
            return
        if self.app.options.get('global_machine_profile') == name:
            self.app.options['global_machine_profile'] = 'None'
            self.app.defaults['global_machine_profile'] = 'None'
            apply_machine_profile_locks(self.app)
        self.refresh_list()
        self.on_new()

    def on_save(self):
        try:
            profile = self.from_form()
            save_profile(profile, profiles_folder(self.app))
        except (ValueError, OSError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The machine profile is not saved"), error))
            return
        # a rename leaves the old file: the profile of the old name is removed
        if self.profile is not None and self.profile.name != profile.name:
            try:
                delete_profile(self.profile.name, profiles_folder(self.app))
            except OSError:
                pass
        self.profile = profile
        self.refresh_list(select=profile.name)
        active = active_profile_of(self.app)
        if active is not None and active.name == profile.name:
            apply_machine_profile_locks(self.app)
        self.app.inform.emit('[success] %s: %s' % (_("Machine profile saved"), profile.name))

    # ---- form <-> profile ----
    def to_form(self, profile):
        self.name_entry.set_value(profile.name)
        self.bed_x.setValue(profile.bed_x)
        self.bed_y.setValue(profile.bed_y)
        self.origin_corner.set_value(profile.origin_corner)
        self.z_travel.setValue(profile.z_travel)
        for spin, value in zip(self.work_offset, profile.work_offset):
            spin.setValue(value)
        for field, (spins, unset) in self.station.items():
            value = getattr(profile, field)
            unset.setChecked(value is None)
            for spin, number in zip(spins, value or [0.0] * len(spins)):
                spin.setValue(number)
            for spin in spins:
                spin.setEnabled(value is not None)
        for field, (spin, unset) in self.limit.items():
            value = getattr(profile, field)
            unset.setChecked(value is None)
            spin.setValue(value or 0.0)
            spin.setEnabled(value is not None)
        for (field, axis), (spin, unset) in self.ecut.items():
            value = (getattr(profile, field) or {}).get(axis)
            unset.setChecked(value is None)
            spin.setValue(value or 0.0)
            spin.setEnabled(value is not None)
        for kind, combo in self.rule.items():
            combo.set_value(profile.rule_modes.get(kind, 'warn'))
        if profile.preprocessor_mill and self.cal_pp.findText(profile.preprocessor_mill) >= 0:
            self.cal_pp.set_value(profile.preprocessor_mill)
        self.cal_solution = None
        self.cal_result.set_value('')
        self.exclusions = [dict(area, polygon=list(area.get('polygon', []))) for area in profile.exclusions]
        self.refresh_exclusions()

    def from_form(self):
        """The profile of the form: the fields that the form does not edit keep the values of the loaded profile."""
        base = self.profile if self.profile is not None else MachineProfile()
        values = {
            'name': self.name_entry.get_value().strip(),
            'bed_x': self.bed_x.value(), 'bed_y': self.bed_y.value(),
            'origin_corner': self.origin_corner.get_value(), 'z_travel': self.z_travel.value(),
            'work_offset': tuple(spin.value() for spin in self.work_offset),
            'rule_modes': {kind: combo.get_value() for kind, combo in self.rule.items()},
            'exclusions': [dict(area, polygon=list(area['polygon'])) for area in self.exclusions],
        }
        for field, (spins, unset) in self.station.items():
            values[field] = None if unset.isChecked() else tuple(spin.value() for spin in spins)
        for field, (spin, unset) in self.limit.items():
            values[field] = None if unset.isChecked() else spin.value()
        for field, _text in _ECUT_FIELDS:
            axes = {axis: self.ecut[(field, axis)][0].value() for axis in AXIS_NAMES
                    if not self.ecut[(field, axis)][1].isChecked()}
            values[field] = axes or None
        return replace(base, **values)
