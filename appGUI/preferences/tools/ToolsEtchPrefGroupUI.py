
from appGUI.GUIElements import FCLabel, FCFrame, GLay, RadioSet, FCDoubleSpinner, FCEntry, FCComboBox
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class EtchantCombo(FCComboBox):
    """Combo that stores the etchant key ('cucl2', 'fecl3', 'alkaline') instead of the shown text."""

    def get_value(self):
        return str(self.currentData() or 'cucl2')

    def set_value(self, val):
        idx = self.findData(str(val))
        self.setCurrentIndex(idx if idx != -1 else 0)


class ToolsEtchPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(ToolsEtchPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Etch Compensation")))
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # PARAMETERS Frame
        # #############################################################################################################
        self.param_label = FCLabel('%s' % _("Parameters"), bold=True)
        self.param_label.setToolTip(
            _("Grow the copper of a Gerber object to compensate\n"
              "the lateral undercut of the etching.")
        )
        self.layout.addWidget(self.param_label)

        param_frame = FCFrame()
        self.layout.addWidget(param_frame)

        param_grid = GLay(v_spacing=5, h_spacing=3)
        param_frame.setLayout(param_grid)

        self.thickness_label = FCLabel('%s:' % _("Copper thickness"))
        self.thickness_label.setToolTip(_("The thickness of the copper layer, in microns."))
        self.thickness_entry = FCDoubleSpinner()
        self.thickness_entry.set_range(0, 10000)
        self.thickness_entry.set_precision(self.decimals)
        self.thickness_entry.setSingleStep(1)
        self.thickness_units_label = FCLabel('%s' % _("um"))
        param_grid.addWidget(self.thickness_label, 0, 0)
        param_grid.addWidget(self.thickness_entry, 0, 1)
        param_grid.addWidget(self.thickness_units_label, 0, 2)

        self.ratio_label = FCLabel('%s:' % _("Ratio"))
        self.ratio_label.setToolTip(
            _("How the lateral offset is computed:\n"
              "- Etch Factor: from the etch factor and the copper thickness.\n"
              "- Etchants list: from the etch factor of a known etchant.\n"
              "- Manual offset: a fixed offset.")
        )
        self.ratio_radio = RadioSet([
            {'label': _("Etch Factor"), 'value': 'factor'},
            {'label': _("Etchants list"), 'value': 'etch_list'},
            {'label': _("Manual offset"), 'value': 'manual'},
        ], compact=True)
        param_grid.addWidget(self.ratio_label, 1, 0)
        param_grid.addWidget(self.ratio_radio, 1, 1, 1, 2)

        self.factor_label = FCLabel('%s:' % _("Etch factor"))
        self.factor_label.setToolTip(
            _("The etch factor: depth etch / lateral etch.\n"
              "The lateral offset is the copper thickness divided by this value.")
        )
        self.factor_entry = FCDoubleSpinner()
        self.factor_entry.set_range(0.01, 100)
        self.factor_entry.set_precision(self.decimals)
        self.factor_entry.setSingleStep(0.1)
        param_grid.addWidget(self.factor_label, 2, 0)
        param_grid.addWidget(self.factor_entry, 2, 1, 1, 2)

        self.etchant_label = FCLabel('%s:' % _("Etchant"))
        self.etchant_label.setToolTip(_("The etchant used. Its typical etch factor gives the lateral offset."))
        self.etchant_combo = EtchantCombo()
        self.etchant_combo.addItem(_("CuCl2"), 'cucl2')
        self.etchant_combo.addItem(_("FeCl3"), 'fecl3')
        self.etchant_combo.addItem(_("Alkaline baths"), 'alkaline')
        param_grid.addWidget(self.etchant_label, 3, 0)
        param_grid.addWidget(self.etchant_combo, 3, 1, 1, 2)

        self.offset_label = FCLabel('%s:' % _("Offset"))
        self.offset_label.setToolTip(
            _("The manual lateral offset, in microns.\n"
              "A negative value shrinks the copper.")
        )
        self.offset_entry = FCDoubleSpinner()
        self.offset_entry.set_range(-10000, 10000)
        self.offset_entry.set_precision(self.decimals)
        self.offset_entry.setSingleStep(1)
        self.offset_units_label = FCLabel('%s' % _("um"))
        param_grid.addWidget(self.offset_label, 4, 0)
        param_grid.addWidget(self.offset_entry, 4, 1)
        param_grid.addWidget(self.offset_units_label, 4, 2)

        self.suffix_label = FCLabel('%s:' % _("Name suffix"))
        self.suffix_label.setToolTip(_("Added to the name of the source to make the default name of the result."))
        self.suffix_entry = FCEntry()
        param_grid.addWidget(self.suffix_label, 5, 0)
        param_grid.addWidget(self.suffix_entry, 5, 1, 1, 2)

        self.layout.addStretch()
