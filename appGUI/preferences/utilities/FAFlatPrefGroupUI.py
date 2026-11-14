from PyQt6 import QtWidgets

from appGUI.GUIElements import FCLabel, FCFrame, GLay
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from appGUI.preferences.utilities.ListPrefGroupUI import assoc_buttons

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class FAFlatPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):
        super(FAFlatPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("File Associations")))
        self.decimals = app.decimals
        self.options = app.options

        self.files_label = FCLabel(_("Extensions"), bold=True)
        self.files_label.setToolTip(_("The FlatCAM project and configuration file extensions."))
        self.layout.addWidget(self.files_label)

        files_frame = FCFrame()
        self.layout.addWidget(files_frame)

        grid = GLay(v_spacing=5, h_spacing=3)
        files_frame.setLayout(grid)

        self.ext_value = FCLabel('.flatprj, .flatconfig')
        grid.addWidget(self.ext_value, 0, 0, 1, 2)

        self.reg_btn, self.unreg_btn = assoc_buttons()
        self.reg_btn.setToolTip(_("Apply the file associations between\n"
                                  "FlatCAM and the project and configuration files.\n"
                                  "On Windows they are active after the next logon."))

        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.reg_btn)
        hlay.addWidget(self.unreg_btn)
        grid.addLayout(hlay, 2, 0, 1, 2)

        grid.setColumnStretch(1, 1)
        self.layout.addStretch(1)
