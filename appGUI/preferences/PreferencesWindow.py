# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# License:  MIT Licence                                    #
# ##########################################################
"""
The Preferences window: a window of moderate size over the main window. On the left a tree of sections and pages, on
the right one page at a time. Each page shows 1 existing preferences group (an OptionsGroupUI); the groups stay the
attributes of the section forms of MainGUI (``ui.general_pref_form.general_app_group`` and so on), so the
``defaults_form_fields`` of the PreferencesUIManager and every other reference to them keep working.
"""

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QSettings

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def _sections():
    """
    The tree of the window: (section name, [(section form attribute of MainGUI, group attribute, page name)]).
    A page name None takes the title of the group. The pages of the Plugins section are sorted by name.
    """
    plugins = [
        ('plugin_pref_form', 'tools_drill_group', _("Drilling")),
        ('plugin_pref_form', 'tools_mill_group', _("Milling")),
        ('plugin_pref_form', 'tools_cutout_group', _("Cutout")),
        ('plugin_pref_form', 'tools_film_group', _("Film")),
        ('plugin_pref_form', 'tools_panelize_group', _("Panelize")),
        ('plugin_pref_form', 'tools_calculators_group', _("Calculators")),
        ('plugin_pref_form', 'tools_transform_group', _("Transform")),
        ('plugin_pref_form', 'tools_solderpaste_group', _("SolderPaste")),
        ('plugin_pref_form', 'tools_markers_group', _("Markers")),
        ('plugin_pref_form', 'tools_sub_group', _("Subtract")),
        ('plugin_pref_form', 'tools_compare_group', _("Compare")),
        ('plugin_pref_form', 'tools_follow_group', _("Follow")),
        ('plugin_eng_pref_form', 'tools_iso_group', _("Isolation")),
        ('plugin_eng_pref_form', 'tools_ncc_group', _("NCC")),
        ('plugin_eng_pref_form', 'tools_paint_group', _("Paint")),
        ('plugin_eng_pref_form', 'tools_photocarve_group', _("Photo Carve")),
        ('plugin_eng_pref_form', 'tools_2sided_group', _("2-Sided")),
        ('plugin_eng_pref_form', 'tools_level_group', _("Levelling")),
        ('plugin_eng_pref_form', 'tools_grbl_group', _("GCode sender")),
        ('plugin2_pref_form', 'tools2_checkrules_group', _("Check Rules")),
        ('plugin2_pref_form', 'tools2_optimal_group', _("Optimal")),
        ('plugin2_pref_form', 'tools2_qrcode_group', _("QRCode")),
        ('plugin2_pref_form', 'tools2_cfill_group', _("Copper Thieving")),
        ('plugin2_pref_form', 'tools2_fiducials_group', _("Fiducials")),
        ('plugin2_pref_form', 'tools2_edrills_group', _("Extract Drills")),
        ('plugin2_pref_form', 'tools2_punch_group', _("Punch Gerber")),
        ('plugin2_pref_form', 'tools2_invert_group', _("Invert Gerber")),
        ('plugin2_pref_form', 'tools2_etch_group', _("Etch Compensation")),
    ]
    plugins.sort(key=lambda page: page[2].casefold())

    def object_pages(form, prefix):
        return [(form, '%s_%s_group' % (prefix, name), None) for name in ('gen', 'opt', 'exp', 'adv_opt', 'editor')]

    return [
        (_("Application"), [('general_pref_form', name, None) for name in (
            'general_app_group', 'general_gui_group', 'general_app_set_group')]),
        (_("Gerber"), object_pages('gerber_pref_form', 'gerber')),
        (_("Excellon"), object_pages('excellon_pref_form', 'excellon')),
        (_("Geometry"), object_pages('geo_pref_form', 'geometry')),
        (_("CNC Job"), [('cncjob_pref_form', name, None) for name in (
            'cncjob_gen_group', 'cncjob_opt_group', 'cncjob_adv_opt_group', 'cncjob_editor_group',
            'cncjob_pp_group')]),
        (_("Plugins"), plugins),
        (_("Utilities"), [('util_pref_form', name, None) for name in (
            'fa_excellon_group', 'fa_gcode_group', 'fa_gerber_group', 'fa_flat_group', 'kw_group')]),
    ]


class _RowDelegate(QtWidgets.QStyledItemDelegate):
    """The rows of the tree get some air."""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QtCore.QSize(size.width(), size.height() + 6)


class PreferencesPage:
    """1 page of the window: 1 preferences group. The scroll area that shows it is built on the first display."""

    # the group gets at least this width; a wider page has an empty margin instead of stretched fields
    max_width = 640

    def __init__(self, section, name, group, item):
        self.section = section
        self.name = name
        self.group = group
        self.item = item
        self.widget = None      # the scroll area, built by build()

    def build(self):
        """Move the group from its section form into a scroll area of its own. Returns the scroll area."""
        if self.widget is not None:
            return self.widget
        group = self.group
        old_parent = group.parentWidget()
        # the section forms and the groups keep their layout in an attribute named 'layout'
        old_layout = QtWidgets.QWidget.layout(old_parent) if old_parent is not None else None
        if old_layout is not None:
            old_layout.removeWidget(group)

        content = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(content)
        lay.setContentsMargins(4, 4, 4, 4)
        group.setTitle('')      # the page title above shows the name
        group.setFlat(True)
        group.setMaximumWidth(max(self.max_width, group.minimumSizeHint().width()))
        lay.addWidget(group, 1)
        lay.addStretch(0)

        self.widget = QtWidgets.QScrollArea()
        self.widget.setWidgetResizable(True)
        self.widget.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.widget.setWidget(content)
        group.show()
        return self.widget


class PreferencesWindow(QtWidgets.QWidget):
    """The Preferences window. It is built by AppUIActions.on_preferences() the first time the Preferences open."""

    settings_key = 'preferences_window_size'
    default_size = QtCore.QSize(1000, 700)

    def __init__(self, app, parent=None):
        # a Dialog window stays over the main window; a QWidget (not a QDialog) so that Enter in a field does not
        # press a button of the page
        super().__init__(parent if parent is not None else app.ui, Qt.WindowType.Dialog)
        self.app = app
        self.ui = app.ui
        self.manager = app.preferencesUiManager
        self._quiet_close = False

        self.setObjectName('preferences_window')
        self.setWindowTitle(_("Preferences"))
        self.setWindowIcon(QtGui.QIcon(app.resource_location + '/settings18.png'))
        self.setMinimumSize(640, 420)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        # ------------------------------------------ the list of pages ---------------------------------------------
        left = QtWidgets.QWidget()
        left_lay = QtWidgets.QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegate(_RowDelegate(self.tree))
        left_lay.addWidget(self.tree)
        self.splitter.addWidget(left)

        # ------------------------------------------ the page ------------------------------------------------------
        right = QtWidgets.QWidget()
        right_lay = QtWidgets.QVBoxLayout(right)
        right_lay.setContentsMargins(8, 0, 0, 0)

        self.path_label = QtWidgets.QLabel()
        self.path_label.setEnabled(False)       # the disabled text colour: a quiet line in both themes
        right_lay.addWidget(self.path_label)

        self.title_label = QtWidgets.QLabel()
        title_font = self.title_label.font()
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.4)
        self.title_label.setFont(title_font)
        right_lay.addWidget(self.title_label)

        self.stack = QtWidgets.QStackedWidget()
        right_lay.addWidget(self.stack, 1)
        self.splitter.addWidget(right)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 760])

        # ------------------------------------------ the buttons ---------------------------------------------------
        self.bottom_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(self.bottom_layout)
        self._build_buttons()

        # ------------------------------------------ the pages -----------------------------------------------------
        self.pages = []
        bold = self.tree.font()
        bold.setBold(True)
        for section, entries in _sections():
            section_item = QtWidgets.QTreeWidgetItem([section, ''])
            section_item.setFont(0, bold)
            section_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(section_item)
            for form_name, group_name, name in entries:
                group = getattr(getattr(self.ui, form_name, None), group_name, None)
                if group is None:
                    continue
                page = PreferencesPage(section, name or group.title(), group, None)
                page.item = QtWidgets.QTreeWidgetItem([page.name, ''])
                page.item.setData(0, Qt.ItemDataRole.UserRole, len(self.pages))
                section_item.addChild(page.item)
                self.pages.append(page)

        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)

        if self.pages:
            self.show_page(self.pages[0])

    def _build_buttons(self):
        res = self.app.resource_location

        self.restore_defaults_button = QtWidgets.QPushButton(
            QtGui.QIcon(res + '/restore32.png'), _("Restore Defaults"))
        self.restore_defaults_button.setToolTip(
            _("Restore the entire set of default values\n"
              "to the initial values loaded after first launch."))
        self.restore_defaults_button.clicked.connect(self.manager.on_restore_defaults_preferences)

        self.open_folder_button = QtWidgets.QPushButton(QtGui.QIcon(res + '/pref.png'), _("Open Pref Folder"))
        self.open_folder_button.setToolTip(_("Open the folder where FlatCAM save the preferences files."))
        self.open_folder_button.clicked.connect(self.ui.on_preferences_open_folder)

        self.clear_gui_button = QtWidgets.QPushButton(QtGui.QIcon(res + '/trash32.png'), _("Clear GUI Settings"))
        self.clear_gui_button.setToolTip(
            _("Clear the GUI settings for FlatCAM,\n"
              "such as: layout, gui state, style etc."))
        self.clear_gui_button.clicked.connect(lambda: self.ui.on_gui_clear())

        for button in (self.restore_defaults_button, self.open_folder_button, self.clear_gui_button):
            self.bottom_layout.addWidget(button)
        self.bottom_layout.addStretch(1)

        self.cancel_button = QtWidgets.QPushButton(_("Cancel"))
        self.cancel_button.setToolTip(_("Will not save the changes and will close the preferences window."))
        self.cancel_button.clicked.connect(self.manager.on_pref_close_button)

        self.apply_button = QtWidgets.QPushButton(QtGui.QIcon(res + '/apply32.png'), _("Apply"))
        self.apply_button.setToolTip(_("Apply the current preferences without saving to a file."))
        self.apply_button.clicked.connect(lambda: self.manager.on_save_button(save_to_file=False))

        self.save_button = QtWidgets.QPushButton(QtGui.QIcon(res + '/save_as.png'), _("Save"))
        self.save_button.setToolTip(
            _("Save the current settings in the 'current_defaults' file\n"
              "which is the file storing the working default preferences."))
        self.save_button.clicked.connect(lambda: self.manager.on_save_button(save_to_file=True))

        for button in (self.cancel_button, self.apply_button, self.save_button):
            button.setMinimumWidth(100)
            self.bottom_layout.addWidget(button)

    # ------------------------------------------------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------------------------------------------------
    def groups(self):
        """All the preferences groups of the window, built or not."""
        return [page.group for page in self.pages]

    def page_of(self, widget):
        """The page that holds the widget (a field of defaults_form_fields, a label ...), or None."""
        for page in self.pages:
            if widget is page.group or page.group.isAncestorOf(widget):
                return page
        return None

    def current_page(self):
        item = self.tree.currentItem()
        index = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        return self.pages[index] if index is not None else None

    def show_page(self, page):
        """Show the page and select it in the tree."""
        widget = page.build()
        if self.stack.indexOf(widget) < 0:
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)
        self.path_label.setText('%s  ›  %s' % (page.section, page.name))
        self.title_label.setText(page.name)
        if self.tree.currentItem() is not page.item:
            self.tree.setCurrentItem(page.item)
        page.item.parent().setExpanded(True)

    def _on_current_item_changed(self, item, _previous):
        if item is None:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is not None:
            self.show_page(self.pages[index])

    @staticmethod
    def _on_item_clicked(item, _column):
        if item.data(0, Qt.ItemDataRole.UserRole) is None:     # a section: open or close it
            item.setExpanded(not item.isExpanded())

    # ------------------------------------------------------------------------------------------------------------
    # edit tracking
    # ------------------------------------------------------------------------------------------------------------
    edit_signals = ('textEdited', 'modificationChanged', 'toggled', 'valueChanged', 'currentIndexChanged')

    def set_edit_tracking(self, slot, enabled):
        """Connect (or disconnect) the edit signals of every widget of the groups to the slot."""
        for group in self.groups():
            for widget in group.findChildren(QtWidgets.QWidget):
                for name in self.edit_signals:
                    signal = getattr(widget, name, None)
                    if signal is None or not hasattr(signal, 'connect'):
                        continue
                    try:
                        signal.disconnect(slot)
                    except (TypeError, AttributeError):
                        pass
                    if enabled:
                        signal.connect(slot)

    def set_edited(self, edited):
        """Show on the Apply button that the form holds edits that are not applied."""
        name = '/apply_red32.png' if edited else '/apply32.png'
        self.apply_button.setIcon(QtGui.QIcon(self.app.resource_location + name))

    # ------------------------------------------------------------------------------------------------------------
    # open and close
    # ------------------------------------------------------------------------------------------------------------
    def open_window(self):
        """Show the window centred over the main window, with the size it had the last time."""
        if not self.isVisible():
            settings = QSettings("Open Source", "FlatCAM_EVO")
            size = settings.value(self.settings_key, None)
            try:
                size = QtCore.QSize(int(size[0]), int(size[1]))
            except (TypeError, ValueError, IndexError):
                size = self.default_size
            screen = self.ui.screen() if self.ui is not None else None
            if screen is not None:
                available = screen.availableGeometry().size()
                size = size.boundedTo(available - QtCore.QSize(40, 60))
            self.resize(size.expandedTo(self.minimumSize()))
            center = self.ui.frameGeometry().center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event):
        # the keys that no field of the page uses come here: Ctrl+S applies the preferences (as it did in the
        # Preferences tab), Esc closes the window as its close button does
        if event.key() == Qt.Key.Key_S and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.manager.on_save_button(save_to_file=False)
            event.accept()
        elif event.key() == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def close_quietly(self):
        """Close without the question about unsaved changes (after Save or Cancel)."""
        self._quiet_close = True
        try:
            self.close()
        finally:
            self._quiet_close = False

    def closeEvent(self, event):
        if not self._quiet_close:
            # the close button of the window, Esc or the Preferences icon of the status bar
            self.manager.on_close_preferences_tab(parent=self.ui)
        settings = QSettings("Open Source", "FlatCAM_EVO")
        settings.setValue(self.settings_key, (self.width(), self.height()))
        del settings
        event.accept()
