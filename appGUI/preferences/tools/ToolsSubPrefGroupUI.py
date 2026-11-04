
from appGUI.GUIElements import FCCheckBox, FCLabel, FCFrame, GLay, RadioSet, FCDoubleSpinner, FCEntry
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

class ToolsSubPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(ToolsSubPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Subtract Plugin")))
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # PARAMETERS Frame
        # #############################################################################################################
        self.sublabel = FCLabel('%s' % _("Parameters"), bold=True)
        self.sublabel.setToolTip(
            _("A tool to substract one Gerber or Geometry object\n"
              "from another of the same type.")
        )
        self.layout.addWidget(self.sublabel)

        param_frame = FCFrame()
        self.layout.addWidget(param_frame)

        param_grid = GLay(v_spacing=5, h_spacing=3)
        param_frame.setLayout(param_grid)

        self.op_label = FCLabel('%s:' % _("Operation"))
        self.op_label.setToolTip(_("The operation that the plugin starts with."))
        self.op_radio = RadioSet([
            {'label': _("Subtract"), 'value': 'subtract'},
            {'label': _("Intersect"), 'value': 'intersect'},
            {'label': _("XOR"), 'value': 'xor'},
        ], compact=True)
        param_grid.addWidget(self.op_label, 0, 0)
        param_grid.addWidget(self.op_radio, 0, 1, 1, 2)

        self.margin_label = FCLabel('%s:' % _("Margin"))
        self.margin_label.setToolTip(_("Grow the subtractor area by this distance before it is used."))
        self.margin_entry = FCDoubleSpinner()
        self.margin_entry.set_range(0.0, 10000.0)
        self.margin_entry.set_precision(self.decimals)
        self.margin_entry.setSingleStep(0.1)
        self.margin_units_label = FCLabel(str(app.app_units).lower())
        param_grid.addWidget(self.margin_label, 1, 0)
        param_grid.addWidget(self.margin_entry, 1, 1)
        param_grid.addWidget(self.margin_units_label, 1, 2)

        self.close_paths_cb = FCCheckBox(_("Close paths"))
        self.close_paths_cb.setToolTip(_("Checking this will close the paths cut by the subtractor object."))
        param_grid.addWidget(self.close_paths_cb, 2, 0, 1, 3)

        self.suffix_label = FCLabel('%s:' % _("Name suffix"))
        self.suffix_label.setToolTip(_("Added to the name of the target to make the default name of the result."))
        self.suffix_entry = FCEntry()
        param_grid.addWidget(self.suffix_label, 3, 0)
        param_grid.addWidget(self.suffix_entry, 3, 1, 1, 2)

        self.keep_target_cb = FCCheckBox(_("Keep target"))
        self.keep_target_cb.setToolTip(
            _("Unchecked: the result replaces the target (the target is deleted and the result gets its name).")
        )
        param_grid.addWidget(self.keep_target_cb, 4, 0, 1, 3)

        self.delete_subs_cb = FCCheckBox(_("Delete subtractors"))
        self.delete_subs_cb.setToolTip(
            _("When checked will delete the subtractor objects\n"
              "after a successful operation.")
        )
        param_grid.addWidget(self.delete_subs_cb, 5, 0, 1, 3)
        self.layout.addStretch()
