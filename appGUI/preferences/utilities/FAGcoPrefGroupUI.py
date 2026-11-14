from appGUI.preferences.utilities.ListPrefGroupUI import ExtListPrefGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class FAGcoPrefGroupUI(ExtListPrefGroupUI):
    def __init__(self, app, parent=None):
        super().__init__(app, str(_("GCode File associations")), parent=parent)

        self.gco_list_text = self.list_text
        self.gco_list_btn = self.apply_btn
        self.gco_unreg_btn = self.unreg_btn
