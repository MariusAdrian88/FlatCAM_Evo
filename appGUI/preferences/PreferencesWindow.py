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


def _plain(text):
    """The text of a label without its HTML."""
    if text and Qt.mightBeRichText(text):
        text = QtGui.QTextDocumentFragment.fromHtml(text).toPlainText()
    return (text or '').strip()


def _is_label(widget):
    """A widget that names a setting: a label or a check box with a text."""
    return isinstance(widget, (QtWidgets.QLabel, QtWidgets.QCheckBox)) and bool(_plain(widget.text()))


def _layout_widgets(item):
    """The widgets of a layout item (a widget, or the widgets of a layout in a grid cell)."""
    if item is None:
        return []
    if item.widget() is not None:
        return [item.widget()]
    layout = item.layout()
    if layout is None:
        return []
    widgets = []
    for index in range(layout.count()):
        widgets += _layout_widgets(layout.itemAt(index))
    return widgets


def setting_rows(group):
    """
    The settings of a preferences group: [(label, [field widgets of its row])]. A label is a QLabel or a QCheckBox
    with a text; its fields are the other widgets of its grid row. A label outside a grid (a subheading) has no field.
    """
    rows = []
    taken = set()
    for grid in group.findChildren(QtWidgets.QGridLayout):
        for row in range(grid.rowCount()):
            cells = []
            for column in range(grid.columnCount()):
                for widget in _layout_widgets(grid.itemAtPosition(row, column)):
                    if widget not in cells:
                        cells.append(widget)
            label = next((w for w in cells if _is_label(w)), None)
            if label is None:
                continue
            fields = []
            for widget in cells:
                if widget is label:
                    continue
                if isinstance(widget, QtWidgets.QCheckBox) and _is_label(widget):
                    rows.append((widget, []))       # a 2nd check box of the row is a setting of its own
                    taken.add(widget)
                elif not isinstance(widget, QtWidgets.QLabel):
                    fields.append(widget)
            rows.append((label, fields))
            taken.add(label)
    for widget in group.findChildren(QtWidgets.QWidget):
        if widget not in taken and _is_label(widget):
            rows.append((widget, []))
    return rows


def help_html(title, tooltip):
    """
    The text of the help strip: the name of the setting and its tooltip, nothing else. The line breaks of a tooltip
    that only wrap a sentence become spaces; the ones that start a list item or follow a full stop stay.
    """
    if Qt.mightBeRichText(tooltip):
        body = tooltip
    else:
        lines = [line.strip() for line in tooltip.strip().splitlines()]
        body = ''
        for index, line in enumerate(lines):
            if index:
                previous = lines[index - 1]
                new_line = (not line or not previous or previous[-1] in '.:;!?' or
                            line[0] in "-*'\"(\u2022" or line[0].isdigit())
                body += '<br>' if new_line else ' '
            body += _escape(line)
    if not title:
        return body
    return '<b>%s</b> &nbsp;\u00b7&nbsp; %s' % (_escape(title), body)


def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# the alpha slider of a pair of colours '#RRGGBBAA': (section form, group, slider) by the key of the fill colour
ALPHA_SLIDERS = {
    'gerber_plot_fill': ('gerber_pref_form', 'gerber_gen_group', 'gerber_alpha_entry'),
    'gerber_plot_line': ('gerber_pref_form', 'gerber_gen_group', 'gerber_alpha_entry'),
    'excellon_plot_fill': ('excellon_pref_form', 'excellon_gen_group', 'excellon_alpha_entry'),
    'excellon_plot_line': ('excellon_pref_form', 'excellon_gen_group', 'excellon_alpha_entry'),
    'cncjob_travel_fill': ('cncjob_pref_form', 'cncjob_gen_group', 'cncjob_alpha_entry'),
    'cncjob_travel_line': ('cncjob_pref_form', 'cncjob_gen_group', 'cncjob_alpha_entry'),
    'global_sel_fill': ('general_pref_form', 'general_gui_group', 'left_right_alpha_entry'),
    'global_sel_line': ('general_pref_form', 'general_gui_group', 'left_right_alpha_entry'),
    'global_alt_sel_fill': ('general_pref_form', 'general_gui_group', 'right_left_alpha_entry'),
    'global_alt_sel_line': ('general_pref_form', 'general_gui_group', 'right_left_alpha_entry'),
}

# the colour of the marks of a setting that differs from its factory value
MARK_COLOR = QtGui.QColor(224, 138, 0)
MARK = '\u25cf'


def _tolerance(field):
    """Two numbers closer than this show the same text in the field."""
    if isinstance(field, QtWidgets.QDoubleSpinBox):
        return 0.5 * 10 ** -field.decimals() + 1e-12
    return 1e-9


def _same(a, b, tol=1e-9):
    """The values of a field are the same: numbers within the tolerance, colours in any case, element by element."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, str) and isinstance(b, str) and a.startswith('#') and b.startswith('#'):
        return a.casefold() == b.casefold()
    return a == b


class _SignalsBlocked:
    """Block the signals of a widget and of its children (a field built of several widgets) for a with block."""

    def __init__(self, widget):
        self.objects = [widget] + widget.findChildren(QtWidgets.QWidget)
        self.states = []

    def __enter__(self):
        self.states = [obj.blockSignals(True) for obj in self.objects]

    def __exit__(self, *args):
        for obj, state in zip(self.objects, self.states):
            try:
                obj.blockSignals(state)
            except RuntimeError:
                pass        # a part that the field replaced


class _PageContent(QtWidgets.QWidget):
    """The content of a page: the group, and in the leading margin the marks of the changed settings."""

    gutter = 24

    def __init__(self):
        super().__init__()
        self.bars = []
        self.buttons = []

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.bars:
            painter = QtGui.QPainter(self)
            for bar in self.bars:
                painter.fillRect(bar, MARK_COLOR)
            painter.end()


class _RowDelegate(QtWidgets.QStyledItemDelegate):
    """The rows of the tree get some air."""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QtCore.QSize(size.width(), size.height() + 6)


class PreferencesPage:
    """1 page of the window: 1 preferences group. The scroll area that shows it is built on the first display."""

    # the group gets at least this width; a wider page has an empty margin instead of stretched fields
    max_width = 640

    def __init__(self, window, section, name, group, item):
        self.window = window
        self.section = section
        self.name = name
        self.group = group
        self.item = item
        self.widget = None      # the scroll area, built by build()
        self.content = None     # the _PageContent in the scroll area
        self._rows = None
        self.matches = 0        # the number of settings that match the search
        self.keys = []          # the option keys of defaults_form_fields on this page
        self.changed = []       # the keys whose value in the form differs from the factory value

    def rows(self):
        """The settings of the page: [(label, [fields], the text that the search looks in)]."""
        if self._rows is None:
            self._rows = []
            for label, fields in setting_rows(self.group):
                texts = [_plain(label.text()), label.toolTip()] + [field.toolTip() for field in fields]
                self._rows.append((label, fields, '\n'.join(t for t in texts if t).casefold()))
        return self._rows

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

        content = _PageContent()
        lay = QtWidgets.QHBoxLayout(content)
        # the leading margin holds the marks of the changed settings; the margins do not follow the direction
        if content.isRightToLeft():
            lay.setContentsMargins(4, 4, content.gutter, 4)
        else:
            lay.setContentsMargins(content.gutter, 4, 4, 4)
        group.setTitle('')      # the page title above shows the name
        group.setFlat(True)
        group.setMaximumWidth(max(self.max_width, group.minimumSizeHint().width()))
        lay.addWidget(group, 1)
        lay.addStretch(0)

        for widget in [group] + group.findChildren(QtWidgets.QWidget):
            widget.installEventFilter(self.window)

        self.widget = QtWidgets.QScrollArea()
        self.widget.setWidgetResizable(True)
        self.widget.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.widget.setWidget(content)
        self.content = content
        content.installEventFilter(self.window)
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

        self.search_entry = QtWidgets.QLineEdit()
        self.search_entry.setPlaceholderText(_("Search settings"))
        self.search_entry.setClearButtonEnabled(True)
        left_lay.addWidget(self.search_entry)
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(lambda: self.search(self.search_entry.text()))
        self.search_entry.textChanged.connect(lambda: self._search_timer.start())
        self._highlighted = {}      # label -> its style sheet before the highlight
        self._query = ''

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

        # the help strip: the tooltip of the setting under the mouse or with the focus
        self.help_label = QtWidgets.QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setTextFormat(Qt.TextFormat.RichText)
        self.help_label.setAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
        self.help_label.setContentsMargins(8, 6, 8, 6)
        self.help_area = QtWidgets.QScrollArea()
        self.help_area.setWidgetResizable(True)
        self.help_area.setWidget(self.help_label)
        self.help_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.help_area.setFixedHeight(self.fontMetrics().lineSpacing() * 4 + 16)
        right_lay.addWidget(self.help_area)
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
                page = PreferencesPage(self, section, name or group.title(), group, None)
                page.item = QtWidgets.QTreeWidgetItem([page.name, ''])
                page.item.setData(0, Qt.ItemDataRole.UserRole, len(self.pages))
                section_item.addChild(page.item)
                self.pages.append(page)

        # the option keys by page, and the factory value of each as its field shows it (probe_factory_values())
        self.fields = self.manager.defaults_form_fields
        for key, field in self.fields.items():
            page = self.page_of(field)
            if page is not None:
                page.keys.append(key)
        self._factory_shown = {}
        self.unmarked_keys = []     # the keys that get no mark: their field cannot show the factory value reliably
        self._markers_timer = QtCore.QTimer(self)
        self._markers_timer.setSingleShot(True)
        self._markers_timer.setInterval(80)
        self._markers_timer.timeout.connect(self.refresh_markers)
        for field in self.fields.values():
            for widget in [field] + field.findChildren(QtWidgets.QWidget):
                for name in self.edit_signals + ('textChanged', 'stateChanged'):
                    signal = getattr(widget, name, None)
                    if signal is not None and hasattr(signal, 'connect'):
                        # not start() itself: QTimer.start(msec) would take the value of the signal as the interval
                        signal.connect(self.schedule_markers)

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

        self.reset_page_button = QtWidgets.QPushButton(_("Reset page to defaults"))
        self.reset_page_button.clicked.connect(lambda: self.reset_page())

        for button in (self.reset_page_button, self.restore_defaults_button, self.open_folder_button,
                       self.clear_gui_button):
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
        if self.stack.currentWidget() is not widget:
            self.help_label.setText('')         # the help of the page that was shown
        self.stack.setCurrentWidget(widget)
        self.schedule_markers()
        self.path_label.setText('%s  \u203a  %s' % (page.section, page.name))
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

    def label_of(self, field):
        """The label that names a field (the field itself for a check box), or None."""
        page = self.page_of(field)
        if page is None:
            return None
        for label, fields, _text in page.rows():
            if field is label or field in fields:
                return label
        return None

    # ------------------------------------------------------------------------------------------------------------
    # help strip
    # ------------------------------------------------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if isinstance(obj, _PageContent):
            if event.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.LayoutRequest):
                self.schedule_markers()
            return False
        if event.type() in (QtCore.QEvent.Type.Enter, QtCore.QEvent.Type.FocusIn) and isinstance(
                obj, QtWidgets.QWidget):
            self.show_help_for(obj)
        return False

    def show_help_for(self, widget):
        """Show the name and the tooltip of the setting of the widget (a label, a field or a part of a field)."""
        page = self.page_of(widget)
        if page is None:
            return
        names = {}
        for label, fields, _text in page.rows():
            names[label] = (label, fields)
            for field in fields:
                names[field] = (label, fields)
        while widget is not None and widget is not page.group:
            if widget in names:
                label, fields = names[widget]
                tips = [widget.toolTip(), label.toolTip()] + [field.toolTip() for field in fields]
                tip = next((t for t in tips if t), '')
                title = _plain(label.text()).rstrip(':').strip()
                self.help_label.setText(help_html(title, tip) if tip else '')
                return
            if widget.toolTip():
                text = widget.text() if hasattr(widget, 'text') and callable(widget.text) else ''
                title = _plain(text).rstrip(':').strip() if isinstance(text, str) else ''
                self.help_label.setText(help_html(title, widget.toolTip()))
                return
            widget = widget.parentWidget()

    # ------------------------------------------------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------------------------------------------------
    highlight_style = '\nQLabel, QCheckBox { background-color: rgba(255, 190, 0, 110); }'

    def search(self, text):
        """
        Show only the pages with a setting whose label or tooltip holds the text (case-insensitive), with the number
        of the matches, highlight the matching labels and show the first page with a match. An empty text shows all.
        """
        self._search_timer.stop()
        if self.search_entry.text() != text:
            self.search_entry.blockSignals(True)
            self.search_entry.setText(text)
            self.search_entry.blockSignals(False)
        query = text.strip().casefold()
        self._query = query

        for label, style in self._highlighted.items():
            label.setStyleSheet(style)
        self._highlighted = {}

        first = None
        for page in self.pages:
            if query:
                matches = [label for label, _fields, hay in page.rows() if query in hay]
                for label in matches:
                    self._highlighted[label] = label.styleSheet()
                    label.setStyleSheet(label.styleSheet() + self.highlight_style)
                page.matches = len(matches)
            else:
                page.matches = 0
            page.item.setHidden(bool(query) and not page.matches)
            self._update_item(page)
            if first is None and page.matches:
                first = page

        for index in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(index)
            children = [section.child(i) for i in range(section.childCount())]
            section.setHidden(all(child.isHidden() for child in children))
            current = self.current_page()
            section.setExpanded(bool(query) or (current is not None and current.item in children))

        if first is not None:
            self.show_page(first)

    def _update_item(self, page):
        """The second column of a page in the tree: the number of matches of the search and the changed mark."""
        parts = [str(page.matches)] if self._query else []
        if page.changed:
            parts.append(MARK)
        page.item.setText(1, ' '.join(parts))
        if page.changed:
            page.item.setForeground(1, MARK_COLOR)
        else:
            page.item.setData(1, Qt.ItemDataRole.ForegroundRole, None)
        section = page.item.parent()
        changed = any(p.changed for p in self.pages if p.item.parent() is section)
        section.setText(1, MARK if changed else '')
        section.setForeground(1, MARK_COLOR)

    # ------------------------------------------------------------------------------------------------------------
    # changed settings
    # ------------------------------------------------------------------------------------------------------------
    def probe_factory_values(self):
        """
        Find the factory value of each key as its field shows it: write the factory value through the same path as
        the form (PreferencesUIManager.defaults_write_form_field(): units, colours with alpha, tuples), read the field,
        and put the value of the form back, with the signals blocked. A field that does not give its value back the
        same is left without a mark (unmarked_keys).
        """
        factory = self.app.defaults.factory_defaults
        self._factory_shown = {}
        self.unmarked_keys = []
        for key, field in self.fields.items():
            ok = False
            if key in factory:
                with _SignalsBlocked(field):
                    try:
                        current = field.get_value()
                        self.manager.defaults_write_form_field(key, defaults_dict=factory)
                        shown = field.get_value()
                        field.set_value(current)
                        ok = _same(field.get_value(), current, _tolerance(field))
                    except Exception as err:
                        self.app.log.debug("PreferencesWindow.probe_factory_values() %s --> %s" % (key, str(err)))
            if ok:
                self._factory_shown[key] = shown
            else:
                self.unmarked_keys.append(key)

    def schedule_markers(self, *_args):
        """Refresh the marks soon: an edit gives several signals, the marks are drawn once after them."""
        if not self._markers_timer.isActive():
            self._markers_timer.start()

    def changed_keys(self, page):
        """The keys of the page whose value in the form differs from the factory value."""
        changed = []
        for key in page.keys:
            if key not in self._factory_shown:
                continue
            field = self.fields[key]
            try:
                if not _same(field.get_value(), self._factory_shown[key], _tolerance(field)):
                    changed.append(key)
            except Exception:
                pass
        return changed

    def marker_rows(self, page):
        """[(key, rectangle of the row in the page content)] of the changed settings that are visible."""
        rows = []
        content = page.content
        if content is None:
            return rows
        for key in page.changed:
            field = self.fields[key]
            label = self.label_of(field)
            widgets = [w for w in (label, field) if w is not None and w.isVisibleTo(content)]
            if not widgets:
                continue
            tops = [w.mapTo(content, QtCore.QPoint(0, 0)).y() for w in widgets]
            bottoms = [top + w.height() for top, w in zip(tops, widgets)]
            rows.append((key, QtCore.QRect(0, min(tops), content.width(), max(bottoms) - min(tops))))
        return rows

    def refresh_markers(self):
        """Mark the changed settings: the pages in the tree, and the rows of the page that is shown."""
        self._markers_timer.stop()
        for page in self.pages:
            page.changed = self.changed_keys(page)
            self._update_item(page)
        page = self.current_page()
        if page is None or page.content is None:
            return
        content = page.content
        style = content.style()
        bars = []
        rows = self.marker_rows(page)
        while len(content.buttons) < len(rows):
            button = QtWidgets.QToolButton(content)
            button.setAutoRaise(True)
            button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
            button.setIconSize(QtCore.QSize(14, 14))
            button.setToolTip(_("Reset"))
            button.clicked.connect(lambda _checked=False, b=button: self.reset_key(b.property('option_key')))
            content.buttons.append(button)
        direction = content.layoutDirection()
        for index, button in enumerate(content.buttons):
            if index >= len(rows):
                button.hide()
                continue
            key, rect = rows[index]
            bar = QtCore.QRect(content.gutter - 6, rect.top() + 2, 3, max(rect.height() - 4, 4))
            box = QtCore.QRect(1, rect.center().y() - 9, 18, 18)
            bars.append(style.visualRect(direction, content.rect(), bar))
            button.setGeometry(style.visualRect(direction, content.rect(), box))
            button.setProperty('option_key', key)
            button.show()
            button.raise_()
        content.bars = bars
        content.update()

    def reset_key(self, key):
        """Put the factory value of 1 setting into the form (saved by Save or Apply, as an edit)."""
        self._write_factory([key])

    def reset_page(self, page=None):
        """Put the factory values of the settings of the page (the page shown) into the form."""
        page = page or self.current_page()
        if page is not None:
            self._write_factory(page.keys)

    def _write_factory(self, keys):
        factory = self.app.defaults.factory_defaults
        for key in keys:
            if key in factory:
                self.manager.defaults_write_form_field(key, defaults_dict=factory)
        for key in keys:
            self._show_alpha(key)
        self.manager.on_preferences_edited()
        self.refresh_markers()

    def _show_alpha(self, key):
        """An alpha slider shows the alpha of the colour of the key (the slider itself is not an option)."""
        names = ALPHA_SLIDERS.get(key)
        slider = getattr(getattr(getattr(self.ui, names[0], None), names[1], None), names[2], None) if names else None
        value = self.fields[key].get_value() if key in self.fields else None
        if slider is None or not isinstance(value, str) or len(value) not in (7, 9):
            return
        alpha = int(value[7:9], 16) if len(value) == 9 else 255
        for part in (slider.spinner, slider.slider):
            with _SignalsBlocked(part):
                part.setValue(alpha)

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
            self.probe_factory_values()
            self.schedule_markers()
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
            if self.search_entry.hasFocus() and self.search_entry.text():
                self.search('')
            else:
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
