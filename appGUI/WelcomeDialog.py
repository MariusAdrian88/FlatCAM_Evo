"""Startup entry points into the existing project and file workflows."""
import builtins
import gettext
from html import escape
import os

from PyQt6 import QtCore, QtGui, QtWidgets

import appTranslation as fcTranslate
from appGUI.AboutContent import PROGRAMMERS, TRANSLATORS, REPOSITORY, WIKI, license_text, attributions_text
from appGUI.GUIElements import add_case_variants
from appGUI.ImportDialog import KEY_ROLE, import_entries

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


CATEGORY_ROLE = KEY_ROLE + 1


def welcome_entries(app):
    imports = {entry['key']: dict(entry) for title, entries in import_entries(app) for entry in entries}

    def entry(key, title, ext, creates, text, icon):
        return {'key': key, 'title': title, 'ext': ext, 'creates': creates, 'text': text,
                'icon': app.resource_location + '/' + icon}

    project = entry('project', _('FlatCAM Evo Project'), '.FlatPrj', _('Project'),
                    _('Open a saved project or start an empty, untitled project.'), 'project16.png')
    geometry = [entry('geometry', _('Geometry'), '', _('Geometry object'),
                      _('Start with a blank Geometry object in an untitled project.'), 'geometry16.png')]
    for kind in ('dxf', 'svg'):
        vector = imports[kind + '_geometry']
        vector.update(key=kind, title=kind.upper(),
                      text=_('Import vector artwork as Geometry for machining, or as Gerber for PCB processing.'))
        if kind == 'dxf':
            vector['icon'] = app.resource_location + '/dxf64.png'
        geometry.append(vector)
    geometry.extend(imports[key] for key in ('hpgl2', 'image', 'pdf') if key in imports)
    pcb = [
        entry('gerber', _('Gerber'),
              '.gbr .ger .gtl .gbl .gts .gbs .gtp .gbp .gto .gbo .gm1 .gml .gm3 .gko .cmp .sol '
              '.stc .sts .plc .pls .crc .crs .tsm .bsm .ly2 .ly15 .dim .mil .grb .top .bot .smt '
              '.smb .sst .ssb .spt .spb .pho .gdo .art .gbd .outline .gdl .gme', _('Gerber object'),
              _('Open PCB artwork, or create a blank Gerber object.'), 'flatcam_icon16.png'),
        entry('excellon', _('Excellon'), '.drl .txt .xln .drd .tap .exc .ncd .xnc', _('Excellon object'),
              _('Open drilling data, or create a blank Excellon object.'), 'drill16.png'),
        dict(imports['odb'], title=_('ODB++'), icon=app.resource_location + '/odb64.png'),
        entry('script', _('Script'), '.FlatScript .tcl', _('Script object'),
              _('Open a script for editing without executing it, or create a blank script.'), 'script_new24.png'),
        entry('document', _('Doc'), '.FlatDoc', _('Document object'),
              _('Open a FlatCAM Evo document, or create a blank document.'), 'notes16_1.png'),
    ]
    boards = [entry for entry in imports.values() if entry['key'] in (
        'pcbdoc', 'cspcbdoc', 'proteus', 'kicad_pcb', 'sprint_layout', 'eagle_brd', 'allegro_brd', 'diptrace', 'pads')]
    return [(_('Project'), [project]), (_('Geometry'), geometry),
            (_('PCB Manufacturing files'), pcb), (_('PCB Boards'), boards)]


def recent_files(app, key):
    """Read the existing histories, including their legacy kind names. Never reinterpret HPGL as DXF."""
    if key is None:
        return []
    history = getattr(app, 'recent_projects' if key == 'project' else 'recent', [])
    if not isinstance(history, (list, tuple)):
        return []
    result = []
    for record in history:
        if not isinstance(record, dict):
            continue
        filename, kind = record.get('filename'), record.get('kind')
        if not isinstance(filename, str) or not filename or not isinstance(kind, str):
            continue
        kind = kind.lower()
        kind = {'sprint_lay6': 'sprint_layout', 'hpgl': 'hpgl2'}.get(kind, kind)
        if kind == 'geometry':
            kind = {'.plt': 'hpgl2', '.svg': 'svg', '.dxf': 'dxf'}.get(os.path.splitext(filename)[1].lower())
        if kind == key and filename not in result:
            result.append(filename)
            if len(result) == 5:
                break
    return result


class RecentFileDelegate(QtWidgets.QStyledItemDelegate):
    row_height = 50

    def __init__(self, parent, line_color):
        super().__init__(parent)
        self.line_color = QtGui.QColor(line_color)

    def sizeHint(self, option, index):
        return QtCore.QSize(100, self.row_height)

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        filename = index.data(KEY_ROLE)
        selected = bool(opt.state & QtWidgets.QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if selected or opt.state & QtWidgets.QStyle.StateFlag.State_MouseOver:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(opt.palette.highlight())
            painter.drawRoundedRect(QtCore.QRectF(opt.rect.adjusted(0, 0, -1, -1)), 5, 5)
        painter.fillRect(QtCore.QRect(opt.rect.left(), opt.rect.bottom(), opt.rect.width(), 1), self.line_color)
        rect = opt.rect.adjusted(8, 8, -32, -8)
        font = QtGui.QFont(opt.font)
        font.setPixelSize(13)
        font.setWeight(QtGui.QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(opt.palette.color(QtGui.QPalette.ColorRole.HighlightedText if selected
                                        else QtGui.QPalette.ColorRole.Text))
        metrics = QtGui.QFontMetrics(font)
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(os.path.basename(filename), QtCore.Qt.TextElideMode.ElideMiddle, rect.width()))
        font.setPixelSize(11)
        font.setWeight(QtGui.QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(opt.palette.color(QtGui.QPalette.ColorRole.PlaceholderText))
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignBottom | QtCore.Qt.AlignmentFlag.AlignLeft,
                         QtGui.QFontMetrics(font).elidedText(filename, QtCore.Qt.TextElideMode.ElideMiddle, rect.width()))
        font.setPixelSize(14)
        painter.setFont(font)
        painter.drawText(opt.rect.adjusted(0, 0, -8, 0),
                         QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter, '↗')
        if selected and opt.state & QtWidgets.QStyle.StateFlag.State_HasFocus:
            painter.setPen(opt.palette.color(QtGui.QPalette.ColorRole.HighlightedText))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(opt.rect.adjusted(1, 1, -2, -2)), 5, 5)
        painter.restore()


class WelcomeDialog(QtWidgets.QDialog):
    decision = QtCore.pyqtSignal()

    def __init__(self, app, parent=None):
        # An owner hidden during startup would suppress this window's taskbar entry.
        super().__init__(None, QtCore.Qt.WindowType.Window | QtCore.Qt.WindowType.FramelessWindowHint)
        self.app = app
        self.setWindowTitle(_('Welcome to FlatCAM Evo'))
        self.setWindowIcon(getattr(app.ui, 'app_icon', QtGui.QIcon(app.resource_location + '/app256.png')))
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setObjectName('welcome_dialog')
        self.apply_theme()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QtWidgets.QFrame()
        self.header.setObjectName('welcome_header')
        self.header_layout = QtWidgets.QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(38, 32, 38, 26)
        self.header_layout.setSpacing(26)
        self.logo_label = QtWidgets.QLabel()
        self.logo_label.setFixedSize(86, 86)
        self.header_layout.addWidget(self.logo_label)
        brand_widget = QtWidgets.QWidget()
        self.brand_layout = brand = QtWidgets.QVBoxLayout(brand_widget)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(0)
        self.brand_label = QtWidgets.QLabel('FlatCAM Evo')
        self.brand_label.setObjectName('welcome_title')
        font = self.brand_label.font()
        font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, -1.3)
        self.brand_label.setFont(font)
        brand.addWidget(self.brand_label)
        brand.addSpacing(9)
        self.version_label = QtWidgets.QLabel('%s · %s' % (app.version, app.version_date))
        self.version_label.setObjectName('welcome_version')
        brand.addWidget(self.version_label)
        brand.addSpacing(12)
        description = QtWidgets.QLabel(_('CAM tools for PCB manufacturing, machining and more.'))
        description.setObjectName('welcome_tagline')
        description.setWordWrap(True)
        brand.addWidget(description)
        brand.addSpacing(11)
        self.close_button = QtWidgets.QPushButton(self.header)
        self.close_button.setObjectName('welcome_close')
        self.close_button.setText('×')
        self.close_button.setFixedSize(32, 32)
        self.close_button.setFlat(True)
        self.close_button.setAccessibleName(_('Close'))
        self.close_button.setToolTip(_('Continue to workspace'))
        self.close_button.clicked.connect(self.reject)
        links = QtWidgets.QLabel('<a style="color:%s;text-decoration:none" href="%s">GitHub ↗</a>'
                                '&nbsp;&nbsp;&nbsp;&nbsp;'
                                '<a style="color:%s;text-decoration:none" href="%s">Wiki ↗</a>' % (
                                    self.colors['accent'], REPOSITORY, self.colors['accent'], WIKI))
        links.setObjectName('welcome_links')
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        brand.addWidget(links)
        self.header_layout.addWidget(brand_widget, 1)
        for widget in (self.header, brand_widget, self.logo_label, self.brand_label, self.version_label, description):
            widget.installEventFilter(self)
        layout.addWidget(self.header)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName('welcome_tabs')
        layout.addWidget(self.tabs, 1)
        application = QtWidgets.QWidget()
        self.tabs.addTab(application, _('Application'))
        content = QtWidgets.QHBoxLayout(application)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.navigation = QtWidgets.QFrame()
        self.navigation.setObjectName('welcome_navigation')
        self.navigation.setFixedWidth(285)
        left = QtWidgets.QVBoxLayout(self.navigation)
        left.setContentsMargins(16, 17, 16, 12)
        left.setSpacing(8)
        self.filter_entry = QtWidgets.QLineEdit()
        self.filter_entry.setPlaceholderText(_('Search file types…'))
        self.filter_entry.setAccessibleName(_('Search file types'))
        self.filter_entry.setClearButtonEnabled(True)
        left.addWidget(self.filter_entry)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setMouseTracking(True)
        self.tree.setIconSize(QtCore.QSize(18, 18))
        self.tree.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        self.tree.setAccessibleName(_('File types'))
        left.addWidget(self.tree)
        content.addWidget(self.navigation)
        details = QtWidgets.QWidget()
        details.setObjectName('welcome_details')
        self.details_layout = right = QtWidgets.QVBoxLayout(details)
        right.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        right.setContentsMargins(32, 29, 32, 22)
        right.setSpacing(0)
        eyebrow = QtWidgets.QLabel(_('Start something').upper())
        eyebrow.setObjectName('welcome_eyebrow')
        font = eyebrow.font()
        font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 1.8)
        eyebrow.setFont(font)
        right.addWidget(eyebrow)
        right.addSpacing(9)
        self.title_label = QtWidgets.QLabel()
        self.title_label.setObjectName('welcome_type')
        right.addWidget(self.title_label)
        right.addSpacing(8)
        self.details_label = QtWidgets.QLabel()
        self.details_label.setObjectName('welcome_note')
        self.details_label.setWordWrap(True)
        self.details_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.details_label.setMinimumHeight(43)
        self.details_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        right.addWidget(self.details_label)
        right.addSpacing(12)
        badges = QtWidgets.QHBoxLayout()
        badges.setSpacing(8)
        self.ext_label = QtWidgets.QLabel()
        self.ext_label.setObjectName('welcome_badge')
        self.ext_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Preferred)
        badges.addWidget(self.ext_label)
        self.creates_label = QtWidgets.QLabel()
        self.creates_label.setObjectName('welcome_badge')
        self.creates_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Preferred)
        self.creates_label.setWordWrap(True)
        badges.addWidget(self.creates_label)
        badges.addStretch()
        right.addLayout(badges)
        self.object_type = QtWidgets.QComboBox()
        self.object_type.setAccessibleName(_('Import as'))
        self.object_type.addItem(_('Geometry object'), 'geometry')
        self.object_type.addItem(_('Gerber object'), 'gerber')
        right.addWidget(self.object_type)
        right.addSpacing(25)
        recent_head = QtWidgets.QHBoxLayout()
        self.recent_heading = heading = QtWidgets.QLabel(_('Recent files'))
        heading.setObjectName('welcome_history_heading')
        recent_head.addWidget(heading)
        recent_head.addStretch()
        self.recent_hint = hint = QtWidgets.QLabel(_('Last 5 · selected type'))
        hint.setObjectName('welcome_history_hint')
        recent_head.addWidget(hint)
        right.addLayout(recent_head)
        right.addSpacing(8)
        self.recent_list = QtWidgets.QListWidget()
        self.recent_list.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.recent_list.setAccessibleName(_('Recent files'))
        self.recent_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setMouseTracking(True)
        self.recent_list.setMinimumHeight(0)
        self.recent_list.setItemDelegate(RecentFileDelegate(self.recent_list, self.colors['line']))
        right.addWidget(self.recent_list)
        self.empty_label = QtWidgets.QLabel(_('No recent files of this type.'))
        self.empty_label.setObjectName('welcome_empty')
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        right.addWidget(self.empty_label, 1)
        right.addStretch()
        self.detail_scroll = detail_scroll = QtWidgets.QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        detail_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail_scroll.setWidget(details)
        content.addWidget(detail_scroll, 1)

        self.contributors = self.text_tab(_('Contributors'))
        credits = {'Juan Pablo Caram': [(_('FlatCAM Author'), '')],
                   'Marius Stanciu': [(_('FlatCAM Evo Author/Maintainer'), '<marius_adrian@yahoo.com>')]}
        for name in PROGRAMMERS:
            credits.setdefault(name, []).append((_('Developer'), ''))
        for translation in TRANSLATORS:
            for name, email in translation['authors']:
                credits.setdefault(name, []).append(('%s · %s' % (_('Translator'), translation['language']), email))
        rows = []
        for name, roles in credits.items():
            detail = ' · '.join(escape(role) for role, email in roles)
            emails = ' '.join(dict.fromkeys(escape(email) for role, email in roles if email))
            rows.append('<p><b>%s</b><br><span style="color: %s">%s %s</span></p>' % (
                escape(name), self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText).name(), detail, emails))
        self.contributors.setHtml(''.join(rows))
        self.license = self.text_tab(_('License'))
        self.license.setHtml('<h3>%s</h3><p><a href="https://opensource.org/licenses/MIT">MIT</a></p><p>%s</p>' % (
            escape(_('Licensed under the MIT license')), escape(license_text()).replace('\n', '<br>')))
        self.about = self.text_tab(_('About'))
        self.about.setHtml('<h2>FlatCAM Evo</h2><p>%s</p><p><a href="%s">GitHub</a> · '
                           '<a href="%s">Wiki</a></p><br>%s' % (
                               escape(_('CAM tools for PCB manufacturing, machining and more.')),
                               REPOSITORY, WIKI, attributions_text()))

        footer_frame = QtWidgets.QFrame()
        footer_frame.setObjectName('welcome_footer')
        self.footer_layout = footer = QtWidgets.QHBoxLayout(footer_frame)
        footer.setContentsMargins(26, 20, 26, 20)
        footer.setSpacing(10)
        self.next_time = QtWidgets.QCheckBox(_('Show this screen next time'))
        self.next_time.setObjectName('welcome_next_time')
        self.next_time.setChecked(bool(app.options.get('global_welcome', app.defaults.get('global_welcome', True))))
        self.next_time.toggled.connect(self.save_preference)
        footer.addWidget(self.next_time)
        footer.addStretch()
        self.continue_button = QtWidgets.QPushButton(_('Continue'))
        self.continue_button.setObjectName('welcome_continue')
        self.browse_button = QtWidgets.QPushButton(_('Browse…'))
        self.browse_button.setObjectName('browse')
        self.new_button = QtWidgets.QPushButton(_('New'))
        for button in (self.continue_button, self.browse_button, self.new_button):
            button.setAutoDefault(True)
            footer.addWidget(button)
        layout.addWidget(footer_frame)
        self.continue_button.clicked.connect(self.reject)
        self.browse_button.clicked.connect(self.browse)
        self.new_button.clicked.connect(self.new)
        self.entries = {}
        category_texts = (
            _('A FlatCAM Evo project keeps your objects and their settings together. '
              'Select FlatCAM Evo Project to reopen saved work or start an empty project.'),
            _('Import drawings, vector artwork and images for machining, cutting or engraving, '
              'or create a blank Geometry object. Select a file type to see its import options and recent files.'),
            _('Work with Gerber artwork and Excellon drilling files, or add scripts and documents to your project. '
              'Select a file type to open an existing file or create a supported blank object.'),
            _('Import a native PCB design from a supported board editor. Choose its format, '
              'then select the layers and drilling to bring into FlatCAM Evo.'),
        )
        for (title, entries), description in zip(welcome_entries(app), category_texts):
            group = QtWidgets.QTreeWidgetItem(self.tree, [title.upper()])
            group.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            group.setData(0, CATEGORY_ROLE, {'title': title, 'text': description})
            font = group.font(0)
            font.setBold(True)
            font.setPixelSize(10)
            font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 1.2)
            group.setFont(0, font)
            group.setForeground(0, QtGui.QColor(self.colors['muted']))
            group.setSizeHint(0, QtCore.QSize(1, 38))
            for entry in entries:
                item = QtWidgets.QTreeWidgetItem(group, [entry['title']])
                item.setData(0, KEY_ROLE, entry['key'])
                icon_path = entry['icon']
                if self.dark:
                    dark_path = os.path.join(os.path.dirname(icon_path), 'dark_resources', os.path.basename(icon_path))
                    if os.path.isfile(dark_path):
                        icon_path = dark_path
                icon = QtGui.QIcon(icon_path)
                pixmap = icon.pixmap(QtCore.QSize(18, 18), self.devicePixelRatioF())
                if not pixmap.isNull():
                    painter = QtGui.QPainter(pixmap)
                    painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
                    painter.fillRect(pixmap.rect(), QtGui.QColor(self.colors['accent']))
                    painter.end()
                    icon = QtGui.QIcon(pixmap)
                item.setIcon(0, icon)
                item.setToolTip(0, '%s (%s)' % (entry['title'], entry['ext']))
                item.setSizeHint(0, QtCore.QSize(1, 34))
                self.entries[entry['key']] = entry
            group.setExpanded(True)
        self.tree.currentItemChanged.connect(self.selection_changed)
        self.filter_entry.textChanged.connect(self.apply_filter)
        self.object_type.currentIndexChanged.connect(self.selection_changed)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.recent_list.itemClicked.connect(self.open_recent)
        self.recent_list.itemActivated.connect(self.open_recent)
        self.recent_list.currentItemChanged.connect(self.scroll_recent_into_view)
        self.select('project')
        order = (self.close_button, links, self.tabs.tabBar(), self.filter_entry, self.tree, self.object_type,
                 self.recent_list, self.next_time, self.continue_button, self.browse_button, self.new_button)
        for first, second in zip(order, order[1:]):
            self.setTabOrder(first, second)
        self.filter_entry.setFocus()
        self._compact = None

    def apply_theme(self):
        window = self.app.ui
        window.ensurePolished()
        option = QtWidgets.QStyleOption()
        option.initFrom(window)
        surface = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        surface.fill(window.palette().color(QtGui.QPalette.ColorRole.Window))
        option.rect = surface.rect()
        painter = QtGui.QPainter(surface)
        # Render only the main window's background: QSS can override Window, and Base belongs to inputs.
        window.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, option, painter, window)
        painter.end()
        background = surface.pixelColor(16, 16)
        dark = background.lightness() < 128
        self.dark = dark
        self.colors = c = (
            dict(text='#e5edf5', muted='#a0adbf', line='#313e4f',
                 accent='#c6ad70', primary='#b39a55', primary_text='#211b10') if dark else
            dict(text='#253247', muted='#738093', line='#e5eaf0',
                 accent='#79612b', primary='#b39a55', primary_text='#211b10'))
        c['bg'] = background.name()
        c['panel'] = background.darker(112 if dark else 103).name()
        gold = QtGui.QColor(c['primary'])
        mix = .18 if dark else .14
        tinted = [round(base * (1 - mix) + ink * mix)
                  for base, ink in zip(background.getRgb()[:3], gold.getRgb()[:3])]
        c['tint'] = QtGui.QColor(*tinted).name()
        palette = QtGui.QPalette(self.palette())
        for role, color in ((QtGui.QPalette.ColorRole.Window, c['bg']),
                            (QtGui.QPalette.ColorRole.Base, c['bg']),
                            (QtGui.QPalette.ColorRole.WindowText, c['text']),
                            (QtGui.QPalette.ColorRole.Text, c['text']),
                            (QtGui.QPalette.ColorRole.ButtonText, c['text']),
                            (QtGui.QPalette.ColorRole.PlaceholderText, c['muted']),
                            (QtGui.QPalette.ColorRole.Mid, c['line']),
                            (QtGui.QPalette.ColorRole.Highlight, c['tint']),
                            (QtGui.QPalette.ColorRole.HighlightedText, c['accent']),
                            (QtGui.QPalette.ColorRole.Link, c['accent'])):
            palette.setColor(role, QtGui.QColor(color))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QDialog#welcome_dialog {{ background: {c['bg']}; border: none; }}
            QDialog#welcome_dialog QWidget {{
                background: transparent; color: {c['text']}; border: none;
                font-family: 'Segoe UI'; font-size: 14px; font-weight: 400;
            }}
            QDialog#welcome_dialog QLabel {{ padding: 0; margin: 0; }}
            QDialog#welcome_dialog #welcome_header {{
                border-bottom: 1px solid {c['line']};
            }}
            QDialog#welcome_dialog #welcome_title {{ font-size: 36px; font-weight: 650; }}
            QDialog#welcome_dialog #welcome_version {{ font-size: 12px; color: {c['muted']}; }}
            QDialog#welcome_dialog #welcome_tagline {{ font-size: 14px; color: {c['muted']}; }}
            QDialog#welcome_dialog #welcome_links {{ font-size: 12px; }}
            QDialog#welcome_dialog QTabWidget::pane {{ border: none; border-top: 1px solid {c['line']}; }}
            QDialog#welcome_dialog QTabWidget::tab-bar {{ left: 32px; }}
            QDialog#welcome_dialog QTabBar::tab {{
                padding: 16px 4px 13px; margin-right: 18px; font-size: 13px;
                color: {c['muted']}; background: {c['bg']}; border-bottom: 3px solid transparent;
            }}
            QDialog#welcome_dialog QTabBar::tab:selected {{
                color: {c['accent']}; font-weight: 600; border-bottom-color: {c['accent']};
            }}
            QDialog#welcome_dialog QTabBar::tab:focus {{ background: {c['tint']}; }}
            QDialog#welcome_dialog #welcome_navigation {{
                background: {c['panel']}; border-right: 1px solid {c['line']};
            }}
            QDialog#welcome_dialog QLineEdit, QDialog#welcome_dialog QComboBox {{
                padding: 10px 12px; border: 1px solid {c['line']}; border-radius: 7px;
                background: {c['bg']}; selection-background-color: {c['tint']};
                selection-color: {c['accent']};
            }}
            QDialog#welcome_dialog QLineEdit:focus, QDialog#welcome_dialog QComboBox:focus {{
                border-color: {c['accent']};
            }}
            QDialog#welcome_dialog QComboBox {{ font-size: 12px; padding: 6px 10px; }}
            QDialog#welcome_dialog QComboBox::drop-down {{ width: 20px; border: none; }}
            QDialog#welcome_dialog QListWidget {{
                selection-background-color: {c['tint']}; selection-color: {c['accent']};
            }}
            QDialog#welcome_dialog QComboBox QAbstractItemView {{
                background: {c['bg']}; color: {c['text']}; border: 1px solid {c['line']};
                selection-background-color: {c['tint']}; selection-color: {c['accent']};
            }}
            QDialog#welcome_dialog QTreeWidget, QDialog#welcome_dialog QTreeWidget:focus {{
                font-size: 13px; border: none; outline: none; padding: 0; margin: 0;
            }}
            QDialog#welcome_dialog QTreeWidget::item {{ padding: 8px 11px; border-radius: 7px; }}
            QDialog#welcome_dialog QTreeWidget::item:hover {{ background: {c['line']}; }}
            QDialog#welcome_dialog QTreeWidget::item:selected {{
                background: {c['tint']}; color: {c['accent']};
            }}
            QDialog#welcome_dialog QTreeWidget::item:selected:focus {{ border: 1px solid {c['accent']}; }}
            QDialog#welcome_dialog QScrollArea, QDialog#welcome_dialog QListWidget,
            QDialog#welcome_dialog QListWidget:focus {{ border: none; outline: none; padding: 0; margin: 0; }}
            QDialog#welcome_dialog #welcome_eyebrow {{ font-size: 10px; font-weight: 700; color: {c['accent']}; }}
            QDialog#welcome_dialog #welcome_type {{ font-size: 25px; font-weight: 600; }}
            QDialog#welcome_dialog #welcome_note, QDialog#welcome_dialog #welcome_empty {{
                font-size: 13px; color: {c['muted']};
            }}
            QDialog#welcome_dialog #welcome_badge {{
                font-size: 11px; color: {c['muted']}; border: 1px solid {c['line']};
                border-radius: 5px; padding: 4px 8px;
            }}
            QDialog#welcome_dialog #welcome_history_heading {{ font-size: 13px; font-weight: 600; }}
            QDialog#welcome_dialog #welcome_history_hint {{ font-size: 11px; color: {c['muted']}; }}
            QDialog#welcome_dialog #welcome_information {{ color: {c['muted']}; font-size: 13px; }}
            QDialog#welcome_dialog #welcome_footer {{
                border-top: 1px solid {c['line']};
            }}
            QDialog#welcome_dialog #welcome_next_time {{ font-size: 12px; color: {c['muted']}; spacing: 7px; }}
            QDialog#welcome_dialog QCheckBox::indicator {{ width: 15px; height: 15px; }}
            QDialog#welcome_dialog QPushButton {{
                background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 7px;
                padding: 9px 19px; font-size: 13px;
            }}
            QDialog#welcome_dialog QPushButton:hover {{ background: {c['tint']}; }}
            QDialog#welcome_dialog QPushButton:focus {{ border-color: {c['accent']}; }}
            QDialog#welcome_dialog QPushButton:pressed {{ background: {c['line']}; }}
            QDialog#welcome_dialog #welcome_continue {{ border-color: transparent; color: {c['muted']}; }}
            QDialog#welcome_dialog #welcome_continue:focus {{ border-color: {c['accent']}; }}
            QDialog#welcome_dialog #browse {{
                background: {c['primary']}; border-color: {c['primary']};
                color: {c['primary_text']}; font-weight: 600;
            }}
            QDialog#welcome_dialog #browse:hover {{ border-color: {c['text']}; }}
            QDialog#welcome_dialog #browse:focus {{
                border: 2px solid {c['accent']}; padding: 8px 18px;
            }}
            QDialog#welcome_dialog QPushButton:disabled, QDialog#welcome_dialog #browse:disabled {{
                background: {c['panel']}; color: {c['muted']}; border-color: {c['line']};
            }}
            QDialog#welcome_dialog #welcome_close {{
                font-size: 24px; color: {c['muted']}; padding: 0; border: 1px solid transparent;
            }}
            QDialog#welcome_dialog #welcome_close:focus {{ border-color: {c['accent']}; }}
            QDialog#welcome_dialog QScrollBar:vertical {{ width: 8px; margin: 0; background: transparent; }}
            QDialog#welcome_dialog QScrollBar::handle:vertical {{
                background: {c['line']}; border-radius: 4px; min-height: 24px;
            }}
            QDialog#welcome_dialog QScrollBar::add-line:vertical,
            QDialog#welcome_dialog QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def text_tab(self, title):
        browser = QtWidgets.QTextBrowser()
        browser.setObjectName('welcome_information')
        browser.document().setDocumentMargin(35)
        browser.document().setDefaultStyleSheet('a { color: %s; }' % self.colors['accent'])
        browser.setOpenExternalLinks(True)
        self.tabs.addTab(browser, title)
        return browser

    def items(self):
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            for child in range(group.childCount()):
                yield group.child(child)

    def visible_keys(self):
        return [item.data(0, KEY_ROLE) for item in self.items() if not item.isHidden()]

    def select(self, key):
        for item in self.items():
            if item.data(0, KEY_ROLE) == key:
                self.tree.setCurrentItem(item)
                return

    def selected_key(self):
        item = self.tree.currentItem()
        return item.data(0, KEY_ROLE) if item is not None and not item.isHidden() else None

    def apply_filter(self, text):
        words = text.lower().split()
        for item in self.items():
            entry = self.entries[item.data(0, KEY_ROLE)]
            text = ' '.join((entry['title'], entry['ext'], entry['creates'], item.parent().text(0))).lower()
            item.setHidden(not all(word in text for word in words))
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            group.setHidden(all(group.child(child).isHidden() for child in range(group.childCount())))
        current = self.tree.currentItem()
        if current is None or current.isHidden():
            keys = self.visible_keys()
            if keys:
                self.select(keys[0])
            else:
                self.tree.setCurrentItem(None)
        self.selection_changed()

    def selection_changed(self, *args):
        key = self.selected_key()
        entry = self.entries.get(key)
        current = self.tree.currentItem()
        category = current.data(0, CATEGORY_ROLE) if current is not None and not current.isHidden() else None
        selection = entry or category
        self.browse_button.setEnabled(entry is not None and key != 'geometry')
        self.new_button.setEnabled(key in ('project', 'geometry', 'gerber', 'excellon', 'script', 'document'))
        empty_title = _('Select a category or file type.') if self.visible_keys() else _('No file type matches the search.')
        self.title_label.setText(selection['title'] if selection else empty_title)
        self.details_label.setText(selection['text'] if selection else '')
        for widget in (self.ext_label, self.creates_label, self.recent_heading, self.recent_hint):
            widget.setVisible(entry is not None)
        extensions = entry['ext'].split() if entry else []
        self.ext_label.setText(' '.join(extensions[:5]) + (' …' if len(extensions) > 5 else '') if extensions
                               else _('New object only') if entry else '')
        self.ext_label.setToolTip(entry['ext'] if entry else '')
        self.ext_label.setAccessibleDescription(entry['ext'] if entry else '')
        self.creates_label.setText(self.object_type.currentText() if key in ('svg', 'dxf') else
                                   entry['creates'] if entry else '')
        self.object_type.setVisible(key in ('svg', 'dxf'))
        self.recent_list.clear()
        for filename in recent_files(self.app, key):
            item = QtWidgets.QListWidgetItem(os.path.basename(filename))
            item.setData(KEY_ROLE, filename)
            item.setToolTip(filename)
            item.setSizeHint(QtCore.QSize(100, RecentFileDelegate.row_height))
            self.recent_list.addItem(item)
        self.empty_label.setVisible(entry is not None and self.recent_list.count() == 0)
        self.recent_list.setVisible(self.recent_list.count() > 0)
        # The details area owns scrolling; the file list must not introduce a second viewport.
        self.recent_list.setFixedHeight(self.recent_list.count() * RecentFileDelegate.row_height)
        self.tab_changed()

    def scroll_recent_into_view(self, item, previous=None):
        if item is not None:
            center = self.recent_list.viewport().mapTo(
                self.detail_scroll.widget(), self.recent_list.visualItemRect(item).center())
            self.detail_scroll.ensureVisible(center.x(), center.y(), 0, RecentFileDelegate.row_height // 2)

    def tab_changed(self, *args):
        application = self.tabs.currentIndex() == 0
        self.browse_button.setVisible(application)
        self.new_button.setVisible(application)
        self.browse_button.setDefault(application and self.browse_button.isEnabled())
        self.continue_button.setDefault(not application or not self.browse_button.isEnabled())

    def save_preference(self, enabled):
        self.app.defaults['global_welcome'] = enabled
        self.app.options['global_welcome'] = enabled
        group = getattr(getattr(self.app.ui, 'general_pref_form', None), 'general_app_group', None)
        if group is not None:
            group.welcome_cb.set_value(enabled)
        if self.app.preferencesUiManager.save_defaults(silent=True) is not False:
            self.app.defaults.current_defaults['global_welcome'] = enabled

    def browse(self):
        entry = self.entries.get(self.selected_key())
        if entry is None or not entry['ext']:
            return
        patterns = ' '.join('*' + ext for ext in entry['ext'].split())
        filename, chosen = QtWidgets.QFileDialog.getOpenFileName(
            parent=self, caption=entry['title'], directory=self.app.get_last_folder() or '',
            filter=add_case_variants('%s (%s);;%s (*.*)' % (entry['title'], patterns, _('All Files'))))
        if filename and self.isVisible():
            self.open_file(filename)

    def open_recent(self, item):
        if self.isVisible() and item is not None:
            self.open_file(item.data(KEY_ROLE))

    def open_file(self, filename):
        if not isinstance(filename, str) or not os.path.isfile(filename):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('File no longer available.'))
            return
        key = self.selected_key()
        if key is None or key == 'geometry':
            return
        handlers = self.app.f_handlers
        self.decision.emit()
        self.hide()
        try:
            if key == 'project':
                result = handlers.open_project(filename)
            elif key in ('svg', 'dxf'):
                self.app.worker_task.emit({'fcn': getattr(handlers, 'import_' + key),
                                           'params': [filename, self.object_type.currentData()]})
                result = None
            elif key == 'image':
                self.app.image_tool.run(toggle=False)
                self.app.image_tool.on_load_image(filename=filename)
                result = None
            elif key == 'pdf':
                result = handlers.import_pdf(filename)
            elif key in ('gerber', 'excellon', 'script', 'document', 'hpgl2'):
                self.app.worker_task.emit({'fcn': getattr(handlers, 'open_' + key), 'params': [filename]})
                result = None
            else:
                result = getattr(handlers, 'on_file_import_' + key)(filename)
        except Exception as error:
            self.app.log.error('WelcomeDialog.open_file() --> %s', error)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Failed to open file'), str(error)))
            self.show()
            return
        if result == 'fail':
            self.show()
        else:
            self.accept()

    def new(self):
        key = self.selected_key()
        if not self.new_button.isEnabled():
            return
        # A separate project preserves any existing unsaved work, as File > New Project does.
        if self.app.projects.new() is None:
            return
        self.decision.emit()
        if key != 'project':
            if key == 'script':
                result = self.app.f_handlers.on_file_new_script()
            else:
                result = getattr(self.app.app_obj, 'new_' + key + '_object')()
            if result == 'fail':
                return
        self.accept()

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.MouseButtonPress and event.button() == QtCore.Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return True
        return super().eventFilter(obj, event)

    def fit_layout(self, size):
        compact, short = size.width() < 730, size.height() < 550
        if self._compact != (compact, short):
            self._compact = (compact, short)
            self.navigation.setFixedWidth(210 if compact else 285)
            side = 22 if compact else 38
            self.header_layout.setContentsMargins(side, 6 if short else 26 if compact else 32,
                                                  side, 6 if short else 26)
            for index, gap in ((1, 9), (3, 12), (5, 11)):
                self.brand_layout.itemAt(index).spacerItem().changeSize(0, 3 if short else gap)
            self.brand_layout.invalidate()
            self.header_layout.setSpacing(16 if compact else 26)
            self.details_layout.setContentsMargins(*(16, 22, 16, 22) if compact else (32, 29, 32, 22))
            self.footer_layout.setContentsMargins(26, 12 if short else 20, 26, 12 if short else 20)
            self.brand_label.setStyleSheet('font-size: 29px;' if compact else '')
            logo_size = 65 if compact else 86
            self.logo_label.setFixedSize(logo_size, logo_size)
            self.logo_label.setPixmap(QtGui.QIcon(self.app.resource_location + '/app256.png').pixmap(
                QtCore.QSize(logo_size, logo_size), self.devicePixelRatioF()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_layout(event.size())
        self.close_button.move(self.width() - 18 - self.close_button.width(), 12)

    def fit_to_screen(self):
        if not self.isVisible():
            return
        screen = self.app.ui.screen()
        area = screen.availableGeometry()
        self.setMinimumSize(0, 0)
        frame = self.frameGeometry().size() - self.size()
        size = QtCore.QSize(min(1000, area.width() - frame.width() - 32),
                            min(720, area.height() - frame.height() - 32))
        self.fit_layout(size)
        self.layout().invalidate()
        self.layout().activate()
        self.setMinimumSize(self.layout().minimumSize())
        self.resize(size)
        self.move(area.center() - QtCore.QRect(QtCore.QPoint(), self.frameGeometry().size()).center())

    def showEvent(self, event):
        super().showEvent(event)
        if os.name == 'nt' and QtWidgets.QApplication.platformName() == 'windows':
            import ctypes
            from ctypes import wintypes
            # Let DWM round the opaque window, without a mask or a native caption.
            preference = ctypes.c_int(2)  # DWMWCP_ROUND; unsupported Windows versions keep square corners.
            set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            set_attribute(wintypes.HWND(int(self.winId())), 33,
                          ctypes.byref(preference), ctypes.sizeof(preference))
        self.fit_to_screen()
        # Native show/polish can retain the old minimum until the compact layout's font changes settle.
        QtCore.QTimer.singleShot(0, self.fit_to_screen)
