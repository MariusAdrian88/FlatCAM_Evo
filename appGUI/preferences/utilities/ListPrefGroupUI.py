from PyQt6 import QtWidgets, QtGui
from PyQt6.QtCore import QSettings

from appGUI.GUIElements import FCButton, FCTextArea, FCEntry, FCLabel, FCFrame, GLay
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def assoc_buttons():
    """The 'Apply Association' and 'Remove Association' buttons of a file associations group."""
    apply_btn = FCButton(_("Apply Association"))
    unreg_btn = FCButton(_("Remove Association"))
    unreg_btn.setToolTip(_("Remove the file associations written by 'Apply Association'.\n"
                           "Associations that now belong to another application are kept."))
    return apply_btn, unreg_btn


class ListPrefGroupUI(OptionsGroupUI):
    """
    A preferences group that edits a list of words: the extensions of a file association or the keywords of the
    autocompleter. The list is in a frame with the entry and the buttons that change it; the content stays at the top
    of the page.
    """

    def __init__(self, app, title, list_label, list_tip, entry_label, entry_tip, add_text, add_tip, del_text,
                 del_tip, restore_tip, del_all_tip, association=True, parent=None):
        super().__init__(self, parent=parent)

        self.setTitle(title)
        self.decimals = app.decimals
        self.options = app.options

        self.list_label = FCLabel(list_label, bold=True)
        self.list_label.setToolTip(list_tip)
        self.layout.addWidget(self.list_label)

        list_frame = FCFrame()
        self.layout.addWidget(list_frame)

        self.grid = GLay(v_spacing=5, h_spacing=3)
        list_frame.setLayout(self.grid)

        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        tb_fsize = q_settings.value('textbox_font_size', 10, type=int)
        font = QtGui.QFont()
        font.setPointSize(tb_fsize)

        self.list_text = FCTextArea()
        self.list_text.setReadOnly(True)
        self.list_text.setFont(font)
        # the list has a fixed height; it does not take the free height of the page
        self.list_text.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.list_text.setFixedHeight(self.list_text.fontMetrics().lineSpacing() * 6 + 12)
        self.grid.addWidget(self.list_text, 0, 0, 1, 2)

        self.entry_label = FCLabel('%s:' % entry_label)
        self.entry_label.setToolTip(entry_tip)
        self.entry = FCEntry()
        self.grid.addWidget(self.entry_label, 2, 0)
        self.grid.addWidget(self.entry, 2, 1)

        self.add_btn = FCButton(add_text)
        self.add_btn.setToolTip(add_tip)
        self.del_btn = FCButton(del_text)
        self.del_btn.setToolTip(del_tip)

        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.add_btn)
        hlay.addWidget(self.del_btn)
        self.grid.addLayout(hlay, 4, 0, 1, 2)

        self.restore_btn = FCButton(_("Restore"))
        self.restore_btn.setToolTip(restore_tip)
        self.del_all_btn = FCButton(_("Delete All"))
        self.del_all_btn.setToolTip(del_all_tip)

        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.restore_btn)
        hlay.addWidget(self.del_all_btn)
        self.grid.addLayout(hlay, 6, 0, 1, 2)

        self.apply_btn = self.unreg_btn = None
        if association:
            self.apply_btn, self.unreg_btn = assoc_buttons()
            self.apply_btn.setToolTip(_("Apply the file associations between\n"
                                        "FlatCAM and the files with above extensions.\n"
                                        "On Windows they are active after the next logon."))

            separator = QtWidgets.QFrame()
            separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            separator.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
            self.grid.addWidget(separator, 8, 0, 1, 2)

            hlay = QtWidgets.QHBoxLayout()
            hlay.addWidget(self.apply_btn)
            hlay.addWidget(self.unreg_btn)
            self.grid.addLayout(hlay, 10, 0, 1, 2)

        self.grid.setColumnStretch(1, 1)
        self.layout.addStretch(1)


class ExtListPrefGroupUI(ListPrefGroupUI):
    """The extensions list of the file associations of one file type."""

    def __init__(self, app, title, parent=None):
        super().__init__(
            app, title,
            list_label=_("Extensions list"),
            list_tip=_("List of file extensions to be\n"
                       "associated with FlatCAM."),
            entry_label=_("Extension"),
            entry_tip=_("A file extension to be added or deleted to the list."),
            add_text=_("Add Extension"), add_tip=_("Add a file extension to the list"),
            del_text=_("Delete Extension"), del_tip=_("Delete a file extension from the list"),
            restore_tip=_("Restore the extension list to the default state."),
            del_all_tip=_("Delete all extensions from the list."),
            parent=parent)
        self.ext_entry = self.entry
