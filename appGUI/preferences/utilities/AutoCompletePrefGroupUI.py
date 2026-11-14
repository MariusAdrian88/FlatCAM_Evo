from appGUI.preferences.utilities.ListPrefGroupUI import ListPrefGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AutoCompletePrefGroupUI(ListPrefGroupUI):
    def __init__(self, app, parent=None):
        super().__init__(
            app, str(_("Autocompleter Keywords")),
            list_label=_("Keywords list"),
            list_tip=_("List of keywords used by\n"
                       "the autocompleter in FlatCAM.\n"
                       "The autocompleter is installed\n"
                       "in the Code Editor and for the Tcl Shell."),
            entry_label=_("Keyword"),
            entry_tip=_("A keyword to be added or deleted to the list."),
            add_text=_("Add keyword"), add_tip=_("Add a keyword to the list"),
            del_text=_("Delete keyword"), del_tip=_("Delete a keyword from the list"),
            restore_tip=_("Restore the autocompleter keywords list to the default state."),
            del_all_tip=_("Delete all autocompleter keywords from the list."),
            association=False, parent=parent)

        self.kw_list_text = self.list_text
        self.kw_entry = self.entry
