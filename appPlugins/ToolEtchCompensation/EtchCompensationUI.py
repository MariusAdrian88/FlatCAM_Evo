# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 2/14/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCEntry, FCCheckBox, \
    RadioSet, FCDoubleSpinner, NumericalEvalEntry
from appPlugins.ToolEtchCompensation.EtchCompensationGen import ETCHANTS

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def etchant_label(key):
    """The text of an etchant in the combo: the name and its etch factor."""
    name = _("Alkaline baths") if key == 'alkaline' else ETCHANTS[key]['label']
    return "%s (F = %s)" % (name, ETCHANTS[key]['factor'])


class EtchUI:

    pluginName = _("Etch Compensation")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.tools_box.addWidget(title_label)

        # Source
        self.gerber_label = FCLabel('%s' % _("Source"), color='darkorange', bold=True)
        self.gerber_label.setToolTip(_("Gerber object that will be compensated."))
        self.tools_box.addWidget(self.gerber_label)
        source_frame = FCFrame()
        self.tools_box.addWidget(source_frame)
        grid_s = GLay(v_spacing=5, h_spacing=3)
        source_frame.setLayout(grid_s)

        self.gerber_combo = FCComboBox()
        self.gerber_combo.setModel(self.app.collection)
        self.gerber_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.gerber_combo.is_last = True
        self.gerber_combo.obj_type = "Gerber"
        grid_s.addWidget(self.gerber_combo, 0, 0, 1, 2)

        self.multi_cb = FCCheckBox(_("Use all selected Gerber objects"))
        self.multi_cb.setToolTip(
            _("When checked, every selected Gerber object is compensated, each into its own new object.\n"
              "When unchecked, only the object chosen above is compensated.")
        )
        grid_s.addWidget(self.multi_cb, 2, 0, 1, 2)

        # Utilities
        self.util_label = FCLabel('%s' % _("Utilities"), color='red', bold=True)
        self.util_label.setToolTip('%s.' % _("Conversion utilities"))
        self.tools_box.addWidget(self.util_label)
        util_frame = FCFrame()
        self.tools_box.addWidget(util_frame)
        grid0 = GLay(v_spacing=5, h_spacing=3)
        util_frame.setLayout(grid0)

        self.oz_um_label = FCLabel('%s:' % _('Oz to Microns'))
        self.oz_um_label.setToolTip(
            _("Will convert from oz thickness to microns [um].\n"
              "Can use formulas with operators: /, *, +, -, %, .\n"
              "The real numbers use the dot decimals separator.")
        )
        grid0.addWidget(self.oz_um_label, 0, 0, 1, 2)
        hlay_1 = QtWidgets.QHBoxLayout()
        self.oz_entry = NumericalEvalEntry(border_color='#0069A9')
        self.oz_entry.setPlaceholderText(_("Oz value"))
        self.oz_to_um_entry = FCEntry()
        self.oz_to_um_entry.setPlaceholderText(_("Microns value"))
        self.oz_to_um_entry.setReadOnly(True)
        hlay_1.addWidget(self.oz_entry)
        hlay_1.addWidget(FCLabel(" "))
        hlay_1.addWidget(self.oz_to_um_entry)
        grid0.addLayout(hlay_1, 2, 0, 1, 2)

        self.mils_um_label = FCLabel('%s:' % _('Mils to Microns'))
        self.mils_um_label.setToolTip(
            _("Will convert from mils to microns [um].\n"
              "Can use formulas with operators: /, *, +, -, %, .\n"
              "The real numbers use the dot decimals separator.")
        )
        grid0.addWidget(self.mils_um_label, 4, 0, 1, 2)
        hlay_2 = QtWidgets.QHBoxLayout()
        self.mils_entry = NumericalEvalEntry(border_color='#0069A9')
        self.mils_entry.setPlaceholderText(_("Mils value"))
        self.mils_to_um_entry = FCEntry()
        self.mils_to_um_entry.setPlaceholderText(_("Microns value"))
        self.mils_to_um_entry.setReadOnly(True)
        hlay_2.addWidget(self.mils_entry)
        hlay_2.addWidget(FCLabel(" "))
        hlay_2.addWidget(self.mils_to_um_entry)
        grid0.addLayout(hlay_2, 6, 0, 1, 2)

        # Parameters
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.param_label.setToolTip(_("Parameters used for this tool."))
        self.tools_box.addWidget(self.param_label)
        self.gp_frame = FCFrame()
        self.tools_box.addWidget(self.gp_frame)
        grid1 = GLay(v_spacing=5, h_spacing=3)
        self.gp_frame.setLayout(grid1)

        self.thick_label = FCLabel('%s:' % _('Copper Thickness'))
        self.thick_label.setToolTip(_("The thickness of the copper foil.\nIn microns [um]."))
        self.thick_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.thick_entry.set_precision(self.decimals)
        self.thick_entry.set_range(0.0000, 10000.0000)
        grid1.addWidget(self.thick_label, 0, 0)
        grid1.addWidget(self.thick_entry, 0, 1)
        grid1.addWidget(FCLabel(_("um")), 0, 2)

        self.ratio_label = FCLabel('%s:' % _("Ratio"))
        self.ratio_label.setToolTip(
            _("How the lateral offset is found:\n"
              "- Etch Factor -> offset = thickness / etch factor, with a factor entered by the user\n"
              "- Etchants list -> offset = thickness / etch factor of the chosen etchant\n"
              "- Manual offset -> the offset is entered directly")
        )
        self.ratio_radio = RadioSet([
            {'label': _('Etch Factor'), 'value': 'factor'},
            {'label': _('Etchants list'), 'value': 'etch_list'},
            {'label': _('Manual offset'), 'value': 'manual'}
        ], orientation='vertical', compact=True)
        grid1.addWidget(self.ratio_label, 2, 0, 1, 3)
        grid1.addWidget(self.ratio_radio, 4, 0, 1, 3)
        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 6, 0, 1, 3)

        self.etchants_label = FCLabel('%s:' % _('Etchants'))
        self.etchants_label.setToolTip(
            _("A list of etchants. F is the etch factor of the etchant: depth etch / lateral etch.")
        )
        self.etchants_combo = FCComboBox()
        for key in ETCHANTS:
            self.etchants_combo.addItem(etchant_label(key), key)
        grid1.addWidget(self.etchants_label, 8, 0)
        grid1.addWidget(self.etchants_combo, 8, 1, 1, 2)

        self.factor_label = FCLabel('%s:' % _('Etch Factor'))
        self.factor_label.setToolTip(
            _("The etch factor F: the ratio between depth etch and lateral etch.\n"
              "The lateral offset is the copper thickness divided by F.\n"
              "Must be greater than zero. Accepts real numbers and formulas using the operators: /,*,+,-,%")
        )
        self.factor_entry = NumericalEvalEntry(border_color='#0069A9')
        self.factor_entry.setPlaceholderText(_("Real number or formula"))
        grid1.addWidget(self.factor_label, 10, 0)
        grid1.addWidget(self.factor_entry, 10, 1, 1, 2)

        self.offset_label = FCLabel('%s:' % _('Offset'))
        self.offset_label.setToolTip(
            _("Value with which to increase (positive) or decrease (negative) the copper features.\n"
              "In microns [um].")
        )
        self.offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.offset_entry.set_precision(self.decimals)
        self.offset_entry.set_range(-10000.0000, 10000.0000)
        self.offset_units_label = FCLabel(_("um"))
        grid1.addWidget(self.offset_label, 12, 0)
        grid1.addWidget(self.offset_entry, 12, 1)
        grid1.addWidget(self.offset_units_label, 12, 2)

        self.lateral_label = FCLabel('%s:' % _("Lateral offset"))
        self.lateral_label.setToolTip(
            _("The offset that Compensate will apply to the copper, in microns and in application units.")
        )
        self.lateral_value = FCEntry()
        self.lateral_value.setReadOnly(True)
        grid1.addWidget(self.lateral_label, 14, 0)
        grid1.addWidget(self.lateral_value, 14, 1, 1, 2)

        self.etchants_label.hide()
        self.etchants_combo.hide()
        self.factor_label.hide()
        self.factor_entry.hide()
        self.offset_label.hide()
        self.offset_entry.hide()
        self.offset_units_label.hide()

        self.preview_button = FCButton(_('Preview'), bold=True)
        self.preview_button.setToolTip(
            _("Will draw the outline of the compensated copper of the source object on the canvas.\n"
              "Nothing is created.")
        )
        self.tools_box.addWidget(self.preview_button)

        self.compensate_btn = FCButton(_('Compensate'), bold=True)
        self.compensate_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/etch_32.png'))
        self.compensate_btn.setToolTip(
            _("Will grow the copper features by the lateral offset to compensate the lateral etch.\n"
              "A negative manual offset shrinks the copper.\nThe source object is not changed.")
        )
        self.tools_box.addWidget(self.compensate_btn)
        self.tools_box.addStretch(1)

        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(_("Will reset the tool parameters."))
        self.layout.addWidget(self.reset_button)

    def on_ratio_change(self, val):
        is_factor, is_list, is_manual = val == 'factor', val == 'etch_list', val == 'manual'
        self.etchants_label.setVisible(is_list)
        self.etchants_combo.setVisible(is_list)
        self.factor_label.setVisible(is_factor)
        self.factor_entry.setVisible(is_factor)
        self.offset_label.setVisible(is_manual)
        self.offset_entry.setVisible(is_manual)
        self.offset_units_label.setVisible(is_manual)

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
