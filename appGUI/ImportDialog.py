from PyQt6 import QtCore, QtGui, QtWidgets

# App Translation
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


KEY_ROLE = QtCore.Qt.ItemDataRole.UserRole


def import_entries(app):
    """
    The imports that the Import dialog shows, in groups. Each import calls the same handler as its File -> Import
    menu entry, so the handler opens its own file dialog.

    :return:    a list of (group title, list of entries); an entry is a dict with the keys 'key', 'title',
                'icon', 'ext', 'creates', 'text' and 'run'
    """
    res = app.resource_location
    handlers = app.f_handlers

    vector = [
        {'key': 'svg_geometry', 'title': _('SVG as Geometry'), 'icon': res + '/svg16.png', 'ext': '.svg',
         'creates': _('Geometry object'),
         'text': _('Reads the paths, the shapes and the text of a Scalable Vector Graphics file. '
                   'Use it for artwork that you mill, cut or engrave. You can select more than one file.'),
         'run': lambda: handlers.on_file_import_svg('geometry')},
        {'key': 'svg_gerber', 'title': _('SVG as Gerber'), 'icon': res + '/svg16.png', 'ext': '.svg',
         'creates': _('Gerber object'),
         'text': _('Reads the paths, the shapes and the text of a Scalable Vector Graphics file. '
                   'Use it for copper, solder mask or silkscreen artwork that the isolation and the copper '
                   'clearing plugins process. You can select more than one file.'),
         'run': lambda: handlers.on_file_import_svg('gerber')},
        {'key': 'dxf_geometry', 'title': _('DXF as Geometry'), 'icon': res + '/dxf16.png', 'ext': '.dxf',
         'creates': _('Geometry object'),
         'text': _('Reads the lines, the arcs, the circles, the polylines, the splines and the blocks of an '
                   'AutoCAD drawing. Use it for mechanical outlines and for artwork that you mill, cut or engrave.'),
         'run': lambda: handlers.on_file_import_dxf('geometry')},
        {'key': 'dxf_gerber', 'title': _('DXF as Gerber'), 'icon': res + '/dxf16.png', 'ext': '.dxf',
         'creates': _('Gerber object'),
         'text': _('Reads the lines, the arcs, the circles, the polylines, the splines and the blocks of an '
                   'AutoCAD drawing. Use it for copper artwork that the isolation and the copper clearing '
                   'plugins process.'),
         'run': lambda: handlers.on_file_import_dxf('gerber')},
        {'key': 'hpgl2', 'title': _('HPGL2 as Geometry'), 'icon': res + '/dxf16.png', 'ext': '.plt',
         'creates': _('Geometry object'),
         'text': _('Reads the pen moves of an HPGL/2 plot file, the format of pen plotters and vinyl cutters.'),
         'run': lambda: handlers.on_file_open_hpgl2()},
    ]
    if getattr(app, 'pdf_tool', None) is not None:
        vector.append(
            {'key': 'pdf', 'title': _('PDF'), 'icon': res + '/pdf32.png', 'ext': '.pdf',
             'creates': _('Gerber and Excellon objects'),
             'text': _('Reads the vector artwork of a PDF file, for example a PCB print from an EDA program. '
                       'Each layer becomes a Gerber object. The drill holes become an Excellon object.'),
             'run': lambda: app.pdf_tool.run(toggle=True)})

    raster = []
    if getattr(app, 'image_tool', None) is not None:
        raster.append(
            {'key': 'image', 'title': _('Image'), 'icon': res + '/image32.png', 'ext': '.bmp .png .jpg .jpeg',
             'creates': _('Geometry or Gerber object'),
             'text': _('Opens the Image Import plugin in the Plugins tab. The plugin converts a raster image '
                       'to polygons, by the black and white mask or by the trace of the outlines. '
                       'Load the image and set the options in the plugin.'),
             'run': lambda: app.image_tool.run(toggle=True)})

    board_text = _('A dialog lets you select the layers and the drills to import. '
                   'Each selected layer becomes a Gerber object. The drills become Excellon objects.')
    boards = [
        {'key': 'pcbdoc', 'title': _('Altium PCB'), 'ext': '.PcbDoc',
         'text': _('Reads a native Altium Designer board.'),
         'run': lambda: handlers.on_file_import_pcbdoc()},
        {'key': 'proteus', 'title': _('Proteus PCB'), 'ext': '.pdsprj',
         'text': _('Reads a native Proteus 8 or 9 project. If the project has more than one board, '
                   'the import reads the boards one after the other.'),
         'run': lambda: handlers.on_file_import_proteus()},
        {'key': 'kicad_pcb', 'title': _('KiCad PCB'), 'ext': '.kicad_pcb',
         'text': _('Reads a native KiCad board.'),
         'run': lambda: handlers.on_file_import_kicad_pcb()},
        {'key': 'sprint_layout', 'title': _('Sprint Layout 6'), 'ext': '.lay6',
         'text': _('Reads a native Sprint Layout 6 board.'),
         'run': lambda: handlers.on_file_import_sprint_layout()},
        {'key': 'eagle_brd', 'title': _('Eagle Board'), 'ext': '.brd',
         'text': _('Reads a native Eagle board, the XML format of Eagle 6 and later.'),
         'run': lambda: handlers.on_file_import_eagle_brd()},
        {'key': 'allegro_brd', 'title': _('OrCAD/Allegro Board'), 'ext': '.brd',
         'text': _('Reads a native OrCAD or Allegro board, the binary format.'),
         'run': lambda: handlers.on_file_import_allegro_brd()},
        {'key': 'diptrace', 'title': _('DipTrace PCB'), 'ext': '.dip',
         'text': _('Reads supported native DipTrace boards, including multilayer designs. '
                   'Imports saved copper, pads, vias, drilling, outlines, mask, paste and documentation layers. '
                   'Unsupported records and unavailable artwork are reported during import.'),
         'run': lambda: handlers.on_file_import_diptrace()},
    ]
    for entry in boards:
        entry['icon'] = res + '/import.png'
        entry['creates'] = _('Gerber and Excellon objects')
        entry['text'] = '%s %s' % (entry['text'], board_text)

    groups = [(_('Vector Files'), vector), (_('Raster Images'), raster), (_('PCB Boards'), boards)]
    return [(title, entries) for title, entries in groups if entries]


class ImportDialog(QtWidgets.QDialog):
    """
    The Import dialog of the Project menu. The left side lists the imports in groups, the right side shows the
    details of the selected import. Import closes the dialog; the caller then runs the selected import.
    """

    def __init__(self, app, parent=None, last_key=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle(_("Import"))
        self.setWindowIcon(QtGui.QIcon(app.resource_location + '/import.png'))
        self.setMinimumSize(640, 400)
        self.resize(760, 460)

        self.entries = {}

        # Left side: the filter and the list of the imports
        left = QtWidgets.QWidget()
        left_lay = QtWidgets.QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        self.filter_entry = QtWidgets.QLineEdit()
        self.filter_entry.setPlaceholderText(_("Search"))
        self.filter_entry.setClearButtonEnabled(True)
        left_lay.addWidget(self.filter_entry)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QtCore.QSize(16, 16))
        left_lay.addWidget(self.tree)

        # Right side: the details of the selected import
        right = QtWidgets.QWidget()
        right_lay = QtWidgets.QVBoxLayout(right)
        right_lay.setContentsMargins(12, 0, 0, 0)

        head_lay = QtWidgets.QHBoxLayout()
        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(32, 32)
        head_lay.addWidget(self.icon_label)
        self.title_label = QtWidgets.QLabel()
        title_font = self.title_label.font()
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.3)
        self.title_label.setFont(title_font)
        head_lay.addWidget(self.title_label, stretch=1)
        right_lay.addLayout(head_lay)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.ext_label = QtWidgets.QLabel()
        self.creates_label = QtWidgets.QLabel()
        form.addRow('%s:' % _("File type"), self.ext_label)
        form.addRow('%s:' % _("Creates"), self.creates_label)
        right_lay.addLayout(form)
        right_lay.addSpacing(8)

        self.details_label = QtWidgets.QLabel()
        self.details_label.setWordWrap(True)
        self.details_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.details_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        right_lay.addWidget(self.details_label)
        right_lay.addStretch(1)

        self.hint_label = QtWidgets.QLabel(_("Click Import to select the file."))
        self.hint_label.setEnabled(False)
        right_lay.addWidget(self.hint_label)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([260, 500])

        self.buttons = QtWidgets.QDialogButtonBox()
        self.import_button = self.buttons.addButton(_("Import..."), QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_button.setDefault(True)
        self.buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.splitter, stretch=1)
        layout.addWidget(self.buttons)

        self.build_tree()

        self.tree.currentItemChanged.connect(self.on_current_changed)
        self.tree.itemActivated.connect(self.on_item_activated)
        self.filter_entry.textChanged.connect(self.apply_filter)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        if last_key not in self.entries:
            last_key = next(iter(self.entries), None)
        self.select(last_key)
        self.on_current_changed(self.tree.currentItem())
        self.tree.setFocus()

    def build_tree(self):
        for title, entries in import_entries(self.app):
            group = QtWidgets.QTreeWidgetItem(self.tree, [title])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            # a group is a header: it cannot be selected
            group.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            for entry in entries:
                item = QtWidgets.QTreeWidgetItem(group, [entry['title']])
                item.setIcon(0, QtGui.QIcon(entry['icon']))
                item.setToolTip(0, '%s (%s)' % (entry['title'], entry['ext']))
                item.setData(0, KEY_ROLE, entry['key'])
                self.entries[entry['key']] = entry
            group.setExpanded(True)

    def items(self):
        for g_idx in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g_idx)
            for c_idx in range(group.childCount()):
                yield group.child(c_idx)

    def keys(self):
        return list(self.entries)

    def visible_keys(self):
        return [item.data(0, KEY_ROLE) for item in self.items() if not item.isHidden()]

    def select(self, key):
        for item in self.items():
            if item.data(0, KEY_ROLE) == key:
                self.tree.setCurrentItem(item)
                return

    def selected_key(self):
        item = self.tree.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(0, KEY_ROLE)

    def apply_filter(self, text):
        words = text.lower().split()
        for g_idx in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g_idx)
            shown = 0
            for c_idx in range(group.childCount()):
                item = group.child(c_idx)
                entry = self.entries[item.data(0, KEY_ROLE)]
                haystack = ' '.join((entry['title'], entry['ext'], entry['creates'], group.text(0))).lower()
                match = all(word in haystack for word in words)
                item.setHidden(not match)
                shown += match
            group.setHidden(shown == 0)

        if self.selected_key() is None:
            visible = self.visible_keys()
            if visible:
                self.select(visible[0])
            else:
                self.tree.setCurrentItem(None)
        self.on_current_changed(self.tree.currentItem())

    def on_current_changed(self, current, previous=None):
        key = self.selected_key() if current is not None else None
        entry = self.entries.get(key)
        self.import_button.setEnabled(entry is not None)
        self.hint_label.setVisible(entry is not None)
        if entry is None:
            self.icon_label.clear()
            if self.visible_keys():
                self.title_label.setText(_("Select an import in the list."))
            else:
                self.title_label.setText(_("No import matches the search."))
            self.ext_label.clear()
            self.creates_label.clear()
            self.details_label.clear()
            return
        self.icon_label.setPixmap(QtGui.QIcon(entry['icon']).pixmap(32, 32))
        self.title_label.setText(entry['title'])
        self.ext_label.setText(entry['ext'])
        self.creates_label.setText(entry['creates'])
        self.details_label.setText(entry['text'])

    def on_item_activated(self, item, column=0):
        if item.data(0, KEY_ROLE) is not None:
            self.accept()

    def accept(self):
        if self.selected_key() is None:
            return
        super().accept()

    def run_selected(self):
        entry = self.entries.get(self.selected_key())
        if entry is not None:
            entry['run']()
