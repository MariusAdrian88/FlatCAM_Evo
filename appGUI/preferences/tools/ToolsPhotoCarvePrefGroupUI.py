from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from appPlugins.ToolPhotoCarve.PhotoCarveUI import add_parameter_fields

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolsPhotoCarvePrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):
        super().__init__(self, parent=parent)
        self.setTitle(_("Photo Carve Plugin"))
        self.decimals = app.decimals
        self.options = app.options
        add_parameter_fields(self, self.layout, self.decimals)
        self.layout.addStretch()
