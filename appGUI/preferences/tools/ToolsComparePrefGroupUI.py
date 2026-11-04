from appGUI.GUIElements import FCLabel, FCFrame, GLay, FCDoubleSpinner
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolsComparePrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(ToolsComparePrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Compare Plugin")))
        self.decimals = app.decimals
        self.options = app.options

        self.param_label = FCLabel('%s' % _("Parameters"), bold=True)
        self.param_label.setToolTip(_("Compare two objects of the same type and show what was removed or added."))
        self.layout.addWidget(self.param_label)

        param_frame = FCFrame()
        self.layout.addWidget(param_frame)
        param_grid = GLay(v_spacing=5, h_spacing=3)
        param_frame.setLayout(param_grid)

        self.tolerance_label = FCLabel('%s:' % _("Tolerance"))
        self.tolerance_label.setToolTip(
            _("Geometry within this distance of the other object is never reported as changed.\n"
              "Geometry farther than about 1.011 times this distance is always reported as changed."))
        self.tolerance_entry = FCDoubleSpinner()
        self.tolerance_entry.set_range(0.0, 10000.0)
        self.tolerance_entry.set_precision(self.decimals)
        self.tolerance_entry.setSingleStep(0.001)
        self.tolerance_units_label = FCLabel(str(app.app_units).lower())
        param_grid.addWidget(self.tolerance_label, 0, 0)
        param_grid.addWidget(self.tolerance_entry, 0, 1)
        param_grid.addWidget(self.tolerance_units_label, 0, 2)

        self.layout.addStretch()
