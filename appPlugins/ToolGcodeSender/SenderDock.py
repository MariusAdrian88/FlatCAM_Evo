# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import math
import re

from PyQt6 import QtWidgets, QtCore, QtGui

from appEditors.text_editor.code_edit import CodeEdit
from appPlugins.ToolGcodeSender import SenderKeys
from appPlugins.Common.grbl.grbl_messages import setting_text
from appPlugins.ToolGcodeSender.SenderCameraUI import CurrentPageTabWidget

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class CommandHistory(QtCore.QObject):
    """Up and Down in a command entry walk the sent commands; 'items' is the list that the options store."""

    def __init__(self, entry, items, limit=100):
        # a child of the entry: a rebuilt UI deletes the old entry and its history with it
        super().__init__(entry if isinstance(entry, QtCore.QObject) else None)
        self.entry = entry
        self.items = items
        self.limit = limit
        self._pos = len(items)
        self._draft = ''
        entry.installEventFilter(self)

    def add(self, command):
        """Keep a sent command unless it repeats the last one."""
        if not self.items or self.items[-1] != command:
            self.items.append(command)
            del self.items[:-self.limit]
        self._pos = len(self.items)

    def eventFilter(self, obj, event):
        if obj is not self.entry or event.type() != QtCore.QEvent.Type.KeyPress:
            return False
        key = event.key()
        if key not in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down) or not self.items:
            return False
        if self._pos >= len(self.items):
            self._draft = self.entry.text()
        step = -1 if key == QtCore.Qt.Key.Key_Up else 1
        self._pos = max(0, min(len(self.items), self._pos + step))
        self.entry.setText(self.items[self._pos] if self._pos < len(self.items) else self._draft)
        return True


class GrblConfiguration(QtWidgets.QWidget):
    """Controller-confirmed values and explicit pending edits; editing never sends commands."""

    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self.tool = tool
        self.values = {}
        self.identity = None
        self.epoch = 0
        layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel(_('Not read. Refresh while the controller is idle.'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([_('Identifier'), _('Description'), _('Confirmed'),
                                             _('Pending'), _('Result')])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton(_('Refresh'))
        self.apply_button = QtWidgets.QPushButton(_('Apply changes'))
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.apply_button)
        self.save_button = QtWidgets.QPushButton(_('Save to file'))
        self.load_button = QtWidgets.QPushButton(_('Load from file'))
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.load_button)
        layout.addLayout(buttons)
        note = QtWidgets.QLabel(_(
            'Firmware validates supported values and read-only settings. Some settings require reset; '
            'no reset is sent automatically. Consult the controller documentation.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.refresh_button.clicked.connect(lambda: self.tool._configuration_request(self))
        self.apply_button.clicked.connect(self.apply_changes)
        self.save_button.clicked.connect(self._save_clicked)
        self.load_button.clicked.connect(self._load_clicked)
        self.table.cellChanged.connect(self._edited)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.update_controls)
        self._timer.start()

    def invalidate(self):
        self.epoch += 1
        self.identity = None
        self.values = {}
        self.table.setRowCount(0)
        self.status.setText(_('Connection/reset changed. Previous values and edits discarded. Refresh required.'))

    def update_controls(self):
        supported = self.tool._configuration_supported()
        busy = self.tool.__dict__.get('_grbl_op') is not None
        recovery = self.tool.__dict__.get('_grbl_recovery') is not None
        self.refresh_button.setEnabled(supported and not busy)
        self.apply_button.setEnabled(supported and not busy and not recovery and self.identity is not None)
        self.save_button.setEnabled(bool(self.values))
        self.load_button.setEnabled(bool(self.values))
        self.table.setToolTip(_('Cached values; reads and writes require an idle controller.') if busy else '')

    def _edited(self, row, column):
        if column != 3:
            return
        pending, confirmed = self.table.item(row, 3), self.table.item(row, 2)
        if pending is not None and confirmed is not None:
            self.table.item(row, 4).setText(_('Modified (not sent)') if pending.text() != confirmed.text() else '')

    def apply_changes(self):
        changes = {self.table.item(row, 0).text()[1:]: self.table.item(row, 3).text()
                   for row in range(self.table.rowCount())
                   if self.table.item(row, 3).text() != self.table.item(row, 2).text()}
        if changes:
            self.tool._configuration_request(self, changes)

    def _save_clicked(self):
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self, _('Save settings'), '', _('Text (*.txt)'))
        if path:
            self.save_to_file(path)

    def _load_clicked(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, _('Load settings'), '', _('Text (*.txt)'))
        if path:
            self.load_from_file(path)

    def save_to_file(self, path):
        """Write the confirmed values as '$n=v' lines."""
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                for number, value in sorted(self.values.items(), key=lambda item: int(item[0])):
                    handle.write('$%s=%s\n' % (number, value))
        except (OSError, ValueError) as err:
            QtWidgets.QMessageBox.warning(self, _('Save settings'), '%s\n%s' % (_('The file could not be written.'), err))

    def load_from_file(self, path):
        """Fill the Pending column of the rows that the file names; nothing is sent before Apply."""
        try:
            with open(path, encoding='utf-8') as handle:
                loaded = self.parse_settings(handle.read())
        except (OSError, UnicodeDecodeError, ValueError) as err:
            QtWidgets.QMessageBox.warning(self, _('Load settings'), '%s\n%s' % (_('The file could not be read.'), err))
            return
        rows = {self.table.item(row, 0).text()[1:]: row for row in range(self.table.rowCount())}
        skipped = [number for number in loaded if number not in rows]
        for number, value in loaded.items():
            if number in rows:
                self.table.item(rows[number], 3).setText(value)
        if skipped:
            self.status.setText(_('Not in the controller, skipped:') + ' $' + ', $'.join(skipped))

    @staticmethod
    def parse_settings(text):
        values = {}
        for line in text.splitlines():
            match = re.fullmatch(r'\s*\$(\d+)=(.*)', line)
            if match:
                value = match[2].strip()
                # Older GRBL includes a description after numeric values. String values stay intact.
                comment = re.fullmatch(r'([+\-]?(?:\d+(?:\.\d*)?|\.\d+))\s+\((.*)\)', value)
                values[match[1]] = comment[1] if comment else value
        return values

    def show_result(self, values, changes, results, identity, read_status):
        pending = {self.table.item(row, 0).text()[1:]: self.table.item(row, 3).text()
                   for row in range(self.table.rowCount())}
        missing = set(changes) - set(values) if read_status == 'ok' else set()
        if read_status == 'ok':
            self.values = {**{number: self.values[number] for number in missing}, **values}
            self.identity = None if missing else identity
        else:
            self.identity = None  # do not retry an acknowledged write without an explicit refresh
        blocker = QtCore.QSignalBlocker(self.table)
        self.table.setRowCount(len(self.values))
        for row, (number, value) in enumerate(sorted(self.values.items(), key=lambda item: int(item[0]))):
            edited = pending.get(number, value)
            status, reply = results.get(number, ('', ''))
            outcome = 'ok' if status == 'ok' else (status + ': ' + reply if status else '')
            if number in missing:
                outcome += ' · ' + _('Setting not reported by readback; previous value shown')
            elif number in changes and outcome == 'ok' and read_status == 'ok':
                requested = changes[number]
                same = requested == value
                if not same:
                    try:
                        same = math.isfinite(float(value)) and float(value) == float(requested)
                    except ValueError:
                        pass
                outcome = _('Confirmed by readback') if same else _('Acknowledged; readback differs')
                if same and edited == requested:
                    edited = value
            elif number in changes and not outcome:
                outcome = _('Not sent')
            if number in changes and read_status != 'ok':
                outcome += ' · ' + _('Readback failed; previous value shown')
            if status == 'ok' and re.search(r'reset|reboot', reply, re.IGNORECASE):
                outcome += ' · ' + reply
            description, units = setting_text(number)
            if description and units:
                description += ' (' + units + ')'
            for column, text in enumerate(('$' + number, description or _('Firmware setting'),
                                            value, edited, outcome)):
                item = QtWidgets.QTableWidgetItem(text)
                if column != 3:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        del blocker
        self.status.setText(_('Readback incomplete. Refresh required.') if missing else
                            _('Readback complete. Values are cached until Refresh.') if read_status == 'ok'
                            else _('Read failed. Values are unverified; Refresh before applying again.') + ' ' + read_status)
        self.update_controls()


class _TabStrip(QtWidgets.QWidget):
    """The tab buttons of a QTabWidget (its own tab bar is hidden), on 1 row, or on 2 rows when they do not fit.
    Wide: natural widths. Narrow: the best split into 2 rows. Narrower: both rows elide and shrink down to a minimum
    (3 characters); minimumSizeHint() is that minimum, so the dock cannot get narrower and nothing clips."""
    SPACING = 2
    PADDING = 16

    def __init__(self, tabs, parent=None):
        super().__init__(parent)
        self.tabs = tabs
        self._buttons = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._style = ''
        policy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        tabs.currentChanged.connect(self._sync_current)

    # ---- the widths
    def _row_height(self):
        return self.fontMetrics().height() + 10

    def _row_width(self, widths):
        return sum(widths) + self.SPACING * (len(widths) - 1) if widths else 0

    def _shown(self):
        return [(i, b) for i, b in enumerate(self._buttons) if self.tabs.isTabVisible(i)]

    def _natural(self, text):
        return self.fontMetrics().horizontalAdvance(text) + self.PADDING

    def _minimum(self, text):
        fm = self.fontMetrics()
        short = text if len(text) <= 3 else text[:3] + '…'
        return min(self._natural(text), fm.horizontalAdvance(short) + self.PADDING)

    def _best_split(self, widths):
        """(widest row, k) for the split with the first k buttons on row 1 that makes the widest row smallest."""
        return min((max(self._row_width(widths[:k]), self._row_width(widths[k:])), k)
                   for k in range(1, len(widths)))

    def _fit(self, nat, mn, width):
        """The widths of 1 row inside `width`: natural, or shrunk equally (never below the minimum)."""
        if self._row_width(nat) <= width:
            return list(nat)
        low, high = 0, max(nat)
        while low < high:
            mid = (low + high + 1) // 2
            if self._row_width([max(m, min(n, mid)) for n, m in zip(nat, mn)]) <= width:
                low = mid
            else:
                high = mid - 1
        return [max(m, min(n, low)) for n, m in zip(nat, mn)]

    def _plan(self, width):
        """[(button, x, row, width)] for the shown buttons."""
        shown = self._shown()
        if not shown:
            return []
        nat = [self._natural(b.fullText) for _i, b in shown]
        mn = [self._minimum(b.fullText) for _i, b in shown]
        n = len(shown)
        if n == 1 or self._row_width(nat) <= width:
            split = n
        else:
            wide, k = self._best_split(nat)
            split = k if wide <= width else self._best_split(mn)[1]
        out = []
        for row, (lo, hi) in enumerate(((0, split), (split, n))):
            if lo >= hi:
                continue
            widths = self._fit(nat[lo:hi], mn[lo:hi], width)
            x = 0
            for (_i, button), w in zip(shown[lo:hi], widths):
                out.append((button, x, row, w))
                x += w + self.SPACING
        return out

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        plan = self._plan(width)
        rows = 1 + max((row for _b, _x, row, _w in plan), default=0)
        return rows * self._row_height() + (rows - 1) * self.SPACING

    def sizeHint(self):
        shown = self._shown()
        return QtCore.QSize(self._row_width([self._natural(b.fullText) for _i, b in shown]), self._row_height())

    def minimumSizeHint(self):
        mn = [self._minimum(b.fullText) for _i, b in self._shown()]
        width = self._best_split(mn)[0] if len(mn) > 1 else self._row_width(mn)
        return QtCore.QSize(width, self._row_height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange:
            self._apply_style()

    def _reflow(self):
        row_height = self._row_height()
        plan = self._plan(self.width())
        for button, x, row, w in plan:
            fm = button.fontMetrics()
            text = button.fullText
            if self._natural(text) > w:
                text = fm.elidedText(text, QtCore.Qt.TextElideMode.ElideRight, max(w - self.PADDING, 0))
            button.setText(text.replace('&', '&&'))
            button.setToolTip(button.fullText if text != button.fullText else '')
            button.setGeometry(x, row * (row_height + self.SPACING), w, row_height)
            button.show()

    def _apply_style(self):
        pal = self.palette()
        Role = QtGui.QPalette.ColorRole
        style = ('QToolButton {border: 1px solid %s; border-radius: 0px; padding: 3px 7px; background: transparent;}'
                 'QToolButton:checked {background: %s; border-bottom: 2px solid %s;}'
                 'QToolButton:hover:!checked {background: %s;}'
                 % (pal.color(Role.Mid).name(), pal.color(Role.Window).name(), pal.color(Role.Highlight).name(),
                    pal.color(Role.Midlight).name()))
        if style != self._style:
            self._style = style
            self.setStyleSheet(style)

    # ---- the sync with the tab widget
    def rebuild(self):
        """After a page was added, replaced or removed."""
        for button in self._buttons:
            self._group.removeButton(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self._buttons = []
        self._apply_style()
        for i in range(self.tabs.count()):
            button = QtWidgets.QToolButton(self)
            button.fullText = self.tabs.tabText(i)
            button.setCheckable(True)
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._group.addButton(button)
            button.clicked.connect(lambda _checked=False, index=i: self.tabs.setCurrentIndex(index))
            self._buttons.append(button)
        self.sync()

    def sync(self):
        """After a tab was shown, hidden, enabled or disabled."""
        for i, button in enumerate(self._buttons):
            button.setVisible(self.tabs.isTabVisible(i))
            button.setEnabled(self.tabs.isTabEnabled(i))
        self._sync_current()
        self.updateGeometry()
        self._reflow()

    def _sync_current(self, *_args):
        current = self.tabs.currentIndex()
        for i, button in enumerate(self._buttons):
            button.setChecked(i == current)


class GrblConsole(QtWidgets.QWidget):
    """The prepared program, GRBL acknowledgements, the command console, the camera and the overrides, under the fixed header and jog block."""

    program_line_selected = QtCore.pyqtSignal(object)
    page_changed = QtCore.pyqtSignal(str)
    program_applied = QtCore.pyqtSignal(str)
    program_reverted = QtCore.pyqtSignal()
    TEXT_MIN_HEIGHT = 150   # a short dock scrolls instead of squashing the Program and Console text
    _program_source = None  # the list of the last set_program(): the same list is not set twice
    PROGRAM_BLOCK = 20000   # lines per GUI call when the program text loads
    _program_loaded = 0     # lines already in the document
    _program_generation = 0  # a new set_program stops the running load chain

    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self.tool = tool
        self._selected_lines = set()
        self._selection_anchor = None
        self._progress_selections = []
        # the whole dock content (header, jog block, pages) scrolls as one under a short dock
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(self.scroll_area)
        self._content = QtWidgets.QWidget()
        self.scroll_area.setWidget(self._content)
        lay = QtWidgets.QVBoxLayout(self._content)
        lay.setContentsMargins(0, 0, 0, 0)
        self._pages = {}
        self._header_widget = None
        self._header = QtWidgets.QWidget()
        self._header_lay = QtWidgets.QVBoxLayout(self._header)
        self._header_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._header)
        # the fixed jog block between the header and the pages: a title row with a collapse arrow, and the body
        self._jog_widget = None
        self._jog_block = QtWidgets.QWidget()
        self._jog_block.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
        jog_block_lay = QtWidgets.QVBoxLayout(self._jog_block)
        jog_block_lay.setContentsMargins(0, 0, 0, 0)
        jog_block_lay.setSpacing(0)
        jog_title_row = QtWidgets.QHBoxLayout()
        jog_title_row.setContentsMargins(0, 0, 0, 0)
        self.jog_collapse_button = QtWidgets.QToolButton()
        self.jog_collapse_button.setCheckable(True)
        self.jog_collapse_button.setAutoRaise(True)
        self.jog_collapse_button.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self.jog_collapse_button.setToolTip(_("Collapse or expand the jog controls. Tab does the same."))
        jog_title_row.addWidget(self.jog_collapse_button)
        jog_title_row.addWidget(QtWidgets.QLabel(_("Jog")), 1)
        jog_block_lay.addLayout(jog_title_row)
        self._jog_body = QtWidgets.QWidget()
        self._jog_body.setObjectName('jog_block_body')
        self._jog_body_lay = QtWidgets.QVBoxLayout(self._jog_body)
        self._jog_body_lay.setContentsMargins(0, 0, 0, 0)
        jog_block_lay.addWidget(self._jog_body)
        lay.addWidget(self._jog_block)
        self.tabs = CurrentPageTabWidget()
        self.tabs.tabBar().hide()
        self._tab_strip = _TabStrip(self.tabs)
        lay.addWidget(self._tab_strip)
        lay.addWidget(self.tabs, 1)
        program_tab = QtWidgets.QWidget()
        program_lay = QtWidgets.QVBoxLayout(program_tab)
        program_lay.setContentsMargins(0, 0, 0, 0)
        program_header = QtWidgets.QHBoxLayout()
        self.program_status = QtWidgets.QLabel()
        self.program_status.setToolTip(_(
            "GRBL acknowledged this line. Buffered moves may still be executing."))
        self.follow_cb = QtWidgets.QCheckBox(_("Follow"))
        self.follow_cb.setChecked(True)
        program_header.addWidget(self.program_status, 1)
        program_header.addWidget(self.follow_cb)
        program_lay.addLayout(program_header)
        edit_row = QtWidgets.QHBoxLayout()
        self.edit_button = QtWidgets.QPushButton(_("Edit"))
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip(_("Edit the program before Start. The G-code object is not changed."))
        self.apply_button = QtWidgets.QPushButton(_("Apply"))
        self.apply_button.setToolTip(_("Use the edited text for the next Start."))
        self.revert_button = QtWidgets.QPushButton(_("Revert"))
        self.revert_button.setToolTip(_("Go back to the G-code of the object."))
        self.edit_status = QtWidgets.QLabel()
        for widget in (self.edit_button, self.apply_button, self.revert_button):
            edit_row.addWidget(widget)
        edit_row.addWidget(self.edit_status, 1)
        program_lay.addLayout(edit_row)
        self.program_view = CodeEdit(theme=tool.app.options.get('global_theme', 'default'))
        self.program_area = self.program_view
        self.program_view.setReadOnly(True)
        self.program_view.setMinimumHeight(self.TEXT_MIN_HEIGHT)
        self.program_view.setUndoRedoEnabled(False)
        self.program_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.program_view.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        # Browsing must not replace the stream's line highlight with the text cursor's highlight.
        self.program_view.cursorPositionChanged.disconnect(self.program_view.highlight)
        # the program view keeps its read-only keys, context menu and line clicks with a local filter (always on);
        # the application event filter (Tab jog shortcut, outside clicks) is on only while it is needed
        self.program_view.installEventFilter(self)
        self.program_view.viewport().installEventFilter(self)
        self._app_filter_on = False
        program_lay.addWidget(self.program_area)
        self.set_page('program', _("Program"), program_tab)

        console_tab = QtWidgets.QWidget()
        console_lay = QtWidgets.QVBoxLayout(console_tab)
        console_lay.setContentsMargins(0, 0, 0, 0)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(self.TEXT_MIN_HEIGHT)
        self.log_view.setMaximumBlockCount(5000)
        console_lay.addWidget(self.log_view)
        self._console_lay = console_lay
        self._command_row = None
        self.set_page('console', _("Console"), console_tab)
        self.follow_cb.toggled.connect(self._follow_program)
        self.edit_button.toggled.connect(self._edit_toggled)
        self.apply_button.clicked.connect(self._apply_clicked)
        self.revert_button.clicked.connect(self._revert_clicked)
        self.program_applied.connect(lambda text: self.tool.on_program_applied(text))
        self.program_reverted.connect(lambda: self.tool.on_program_reverted())
        self.update_edit_controls()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.jog_collapse_button.toggled.connect(self._jog_collapse_toggled)
        self.program_line_selected.connect(lambda index: self.tool.on_program_line_selected(index))
        self.set_program([])
        self.sync_event_filter()

    def minimumSizeHint(self):
        """The scroll area alone would let the dock get narrower than its content (the tab strip)."""
        hint = super().minimumSizeHint()
        bar = self.scroll_area.verticalScrollBar()
        width = self._content.minimumSizeHint().width() + (bar.sizeHint().width() if bar.isVisible() else 0)
        return QtCore.QSize(max(hint.width(), width), hint.height())

    def set_tool(self, tool):
        """A new project makes a new Sender plugin; the dock keeps this console: it then works for the new plugin."""
        self.tool = tool
        self.sync_event_filter()

    def sync_event_filter(self):
        """The application-wide event filter is installed only while the console is shown or its Sender plugin is
        active (the Tab jog shortcut works with the dock hidden). Otherwise every Qt event of the app would pay a
        Python call for it."""
        qt_app = QtWidgets.QApplication.instance()
        if qt_app is None:
            return
        wanted = self.isVisible() or bool(getattr(self.tool, '__dict__', {}).get('_plugin_active', False))
        if wanted and not self._app_filter_on:
            qt_app.installEventFilter(self)
            self._app_filter_on = True
        elif not wanted and self._app_filter_on:
            qt_app.removeEventFilter(self)
            self._app_filter_on = False

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_event_filter()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.sync_event_filter()

    def set_header(self, widget):
        """Put the widget in the fixed header above the pages; a previous header widget is deleted."""
        if widget is None or widget is self._header_widget:
            return
        old = self._header_widget
        if old is not None:
            self._header_lay.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._header_widget = widget
        self._header_lay.addWidget(widget)

    def set_jog_block(self, widget):
        """Put the jog widget in the fixed jog block; a previous one is deleted. The collapsed state is read from
        the option."""
        if widget is None or widget is self._jog_widget:
            return
        old = self._jog_widget
        if old is not None:
            self._jog_body_lay.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._jog_widget = widget
        self._jog_body_lay.addWidget(widget)
        collapsed = bool(self.tool.app.options.get('global_grbl_jog_collapsed', False))
        self.jog_collapse_button.blockSignals(True)
        self.jog_collapse_button.setChecked(collapsed)
        self.jog_collapse_button.blockSignals(False)
        self._apply_jog_collapsed(collapsed)

    def _apply_jog_collapsed(self, collapsed):
        self.jog_collapse_button.setArrowType(
            QtCore.Qt.ArrowType.RightArrow if collapsed else QtCore.Qt.ArrowType.DownArrow)
        self._jog_body.setVisible(not collapsed)

    def _jog_collapse_toggled(self, collapsed):
        self._apply_jog_collapsed(collapsed)
        self.tool.app.options['global_grbl_jog_collapsed'] = bool(collapsed)
        if collapsed:
            # a continuous jog never survives the collapse of its controls
            self._stop_continuous_jog()

    def _stop_continuous_jog(self):
        stop = getattr(self.tool, '_stop_continuous_jog', None)
        if callable(stop):
            stop()

    def jog_block_visible(self):
        return (self.isVisible() and self._jog_widget is not None
                and not self.jog_collapse_button.isChecked())

    def expand_jog(self):
        self.jog_collapse_button.setChecked(False)

    def collapse_jog(self):
        self.jog_collapse_button.setChecked(True)

    def set_page(self, key, title, widget):
        """Mount a page under its key; a page with the same key is replaced at its position (and stays current).
        The whole dock content scrolls as one, so a page is a plain tab."""
        if widget is None:
            return
        old = self._pages.get(key)
        if old is widget:
            return
        if old is None:
            self._pages[key] = widget
            self.tabs.insertTab(self._insert_index(key), widget, title)
            self._tab_strip.rebuild()
            return
        was_current = self.tabs.currentWidget() is old
        index = self.tabs.indexOf(old)
        self._pages[key] = widget
        if index >= 0:
            self.tabs.removeTab(index)
        old.setParent(None)
        old.deleteLater()
        self.tabs.insertTab(max(index, 0), widget, title)
        if was_current:
            self.tabs.setCurrentWidget(widget)
        self._tab_strip.rebuild()

    def tab_index(self, key):
        """The index of the tab of a page in the tab widget, or -1."""
        page = self._pages.get(key)
        return self.tabs.indexOf(page) if page is not None else -1

    def is_page_current(self, widget):
        """True if the page widget is on the current tab."""
        return widget is not None and self.tabs.currentWidget() is widget and self._page_key(widget) != ''

    PAGE_ORDER = ['camera', 'program', 'console', 'macros', 'io', 'overrides']

    def _insert_index(self, key):
        """The tab position of a new page: before the first mounted page that comes later in the fixed order."""
        rank = self.PAGE_ORDER.index(key) if key in self.PAGE_ORDER else len(self.PAGE_ORDER)
        later = []
        for other in self._pages:
            other_rank = self.PAGE_ORDER.index(other) if other in self.PAGE_ORDER else len(self.PAGE_ORDER)
            index = self.tab_index(other)
            if other != key and other_rank > rank and index >= 0:
                later.append(index)
        return min(later) if later else self.tabs.count()

    def page(self, key):
        return self._pages.get(key)

    def set_page_visible(self, key, visible):
        """Show or hide the tab of a page (a hidden page is not only disabled)."""
        index = self.tab_index(key)
        if index >= 0:
            self.tabs.setTabVisible(index, visible)
            self._tab_strip.sync()

    def set_command_row(self, widget):
        """Mount the command entry row below the log of the Console page; a previous row is replaced."""
        old = self._command_row
        if old is widget or widget is None:
            return
        if old is not None:
            self._console_lay.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._command_row = widget
        self._console_lay.addWidget(widget)

    def show_page(self, key):
        page = self._pages.get(key)
        if page is not None:
            self.tabs.setCurrentWidget(page)

    def _page_key(self, widget):
        """The key of a page widget."""
        for key, page in self._pages.items():
            if page is widget:
                return key
        return ''

    def set_page_enabled(self, key, enabled):
        """Enable or disable the tab of a page (the raw command entry is gated while the camera workflow owns the
        dock)."""
        index = self.tab_index(key)
        if index >= 0:
            self.tabs.setTabEnabled(index, enabled)
            self._tab_strip.sync()

    def set_console_enabled(self, enabled):
        self.set_page_enabled('console', enabled)

    def clear_mounted(self):
        """The Sender UI that mounted the header, the jog block, the command row and the camera and overrides pages
        is gone: remove them, so no click reaches a widget of the deleted UI. The Program and Console pages belong
        to the dock and stay."""
        self._stop_continuous_jog()
        if self._header_widget is not None:
            self._header_lay.removeWidget(self._header_widget)
            self._header_widget.setParent(None)
            self._header_widget.deleteLater()
            self._header_widget = None
        if self._jog_widget is not None:
            self._jog_body_lay.removeWidget(self._jog_widget)
            self._jog_widget.setParent(None)
            self._jog_widget.deleteLater()
            self._jog_widget = None
        if self._command_row is not None:
            self._console_lay.removeWidget(self._command_row)
            self._command_row.setParent(None)
            self._command_row.deleteLater()
            self._command_row = None
        for key in ('camera', 'macros', 'io', 'overrides'):
            widget = self._pages.pop(key, None)
            if widget is not None:
                index = self.tabs.indexOf(widget)
                if index >= 0:
                    self.tabs.removeTab(index)
                widget.setParent(None)
                widget.deleteLater()
        self._tab_strip.rebuild()

    def _tab_changed(self, index):
        key = self._page_key(self.tabs.widget(index))
        self.page_changed.emit(key)

    def _jog_shortcut(self, obj, event):
        """True when a key press may run a Sender key: the Sender is the Plugin tab, the focus is in the notebook or
        the dock window and in no field that edits text."""
        if event.isAutoRepeat():
            return False
        if (QtWidgets.QApplication.activeModalWidget() is not None
                or QtWidgets.QApplication.activePopupWidget() is not None):
            return False
        if self._is_editing(QtWidgets.QApplication.focusWidget()) or self._is_editing(obj):
            return False
        tool = self.tool
        if not tool.__dict__.get('_plugin_active', False):
            return False
        ui = getattr(getattr(tool, 'app', None), 'ui', None)
        try:
            dock = ui.grbl_console_dock
            windows = (ui.notebook.window(), dock.window())
            if not isinstance(obj, QtWidgets.QWidget) or obj.window() not in windows:
                return False
            focus = QtWidgets.QApplication.focusWidget()
            if focus is not None and focus.window() not in windows:
                return False
            return ui.notebook.currentWidget() is ui.plugin_tab
        except (AttributeError, RuntimeError):
            return False

    @staticmethod
    def _is_editing(widget):
        if not isinstance(widget, QtWidgets.QWidget):
            return False
        if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
            return not widget.isReadOnly()
        return isinstance(widget, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox,
                                   QtWidgets.QAbstractItemView, QtWidgets.QKeySequenceEdit))

    def eventFilter(self, obj, event):
        view = self.__dict__.get('program_view')
        if view is None:
            return False
        if event.type() == QtCore.QEvent.Type.KeyPress and self._jog_shortcut(obj, event) \
                and SenderKeys.dispatch(self.tool, event):
            return True
        # a release ends the action of its press wherever the focus is now
        if event.type() == QtCore.QEvent.Type.KeyRelease and SenderKeys.dispatch(self.tool, event):
            return True
        if event.type() == QtCore.QEvent.Type.WindowDeactivate:
            SenderKeys.release_all(self.tool)
        if event.type() == QtCore.QEvent.Type.MouseButtonPress and self.isVisible() \
                and isinstance(obj, QtWidgets.QWidget) \
                and obj is not self.program_area and not self.program_area.isAncestorOf(obj):
            self.clear_program_selection()
        elif event.type() == QtCore.QEvent.Type.MouseButtonRelease and self.isVisible() \
                and isinstance(obj, QtWidgets.QWidget) \
                and obj is not self.program_area and not self.program_area.isAncestorOf(obj):
            # Controls such as Follow can select the progress line in their click handler.
            QtCore.QTimer.singleShot(0, self.clear_program_selection)
        elif event.type() == QtCore.QEvent.Type.Hide and obj is self:
            self.clear_program_selection()
        if event.type() == QtCore.QEvent.Type.MouseButtonRelease and view.isReadOnly() \
                and event.button() == QtCore.Qt.MouseButton.LeftButton and obj is view.viewport():
            QtWidgets.QPlainTextEdit.mouseReleaseEvent(self.program_view, event)
            cursor = view.cursorForPosition(event.position().toPoint())
            index = cursor.blockNumber()
            if index >= self._program_count:
                return True
            modifiers = event.modifiers()
            if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier and self._selection_anchor is not None:
                first, last = sorted((self._selection_anchor, index))
                self._selected_lines = set(range(first, last + 1))
            elif modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
                self._selected_lines.symmetric_difference_update({index})
                self._selection_anchor = index
            else:
                self._selected_lines = {index}
                self._selection_anchor = index
            if index in self._selected_lines:
                cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
            self.program_view.setTextCursor(cursor)
            self._highlight_program_selection()
            self.program_line_selected.emit(
                next(iter(self._selected_lines)) if len(self._selected_lines) == 1 else set(self._selected_lines))
            return True
        # Use Qt's read-only handlers instead of the editor's insertion/comment shortcuts.
        if obj is view:
            if event.type() == QtCore.QEvent.Type.KeyPress:
                QtWidgets.QPlainTextEdit.keyPressEvent(obj, event)
                return True
            if event.type() == QtCore.QEvent.Type.ContextMenu:
                QtWidgets.QPlainTextEdit.contextMenuEvent(obj, event)
                return True
        return super().eventFilter(obj, event)

    def clear_program_selection(self):
        self._selected_lines.clear()
        self._selection_anchor = None
        cursor = self.program_view.textCursor()
        cursor.clearSelection()
        self.program_view.setTextCursor(cursor)
        self._highlight_program_selection()
        if self.tool.view3d is not None:
            self.tool.view3d.set_selected_line(None)

    def _highlight_program_selection(self):
        selections = []
        for index in sorted(self._selected_lines):
            if index >= self._program_loaded:
                continue
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.cursor = QtGui.QTextCursor(self.program_view.document().findBlockByNumber(index))
            selection.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
            selection.format.setBackground(self.program_view.palette().highlight())
            selection.format.setForeground(self.program_view.palette().highlightedText())
            selections.append(selection)
        self.program_view.setExtraSelections(selections + self._progress_selections)

    def set_program(self, lines):
        if lines is self._program_source:
            return
        self._program_source = lines
        self.clear_program_selection()
        self._program_count = len(lines)
        self._program_generation += 1
        self._program_loaded = 0
        self.program_view.clear()
        self._load_next_block(self._program_generation)
        self.update_progress(0)

    def _load_next_block(self, generation):
        if generation != self._program_generation:
            return
        start = self._program_loaded
        end = min(start + self.PROGRAM_BLOCK, self._program_count)
        text = '\n'.join(self._program_source[start:end])
        if start == 0:
            self.program_view.setPlainText(text)
        else:
            cursor = QtGui.QTextCursor(self.program_view.document())
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
            cursor.insertText('\n' + text)
        self._program_loaded = end
        if start > 0:
            wanted = [self._program_line] + list(self._program_errors) + sorted(self._selected_lines)
            if any(start <= index < end for index in wanted):
                self.update_progress(self._acknowledged, self._program_errors)
        if end < self._program_count:
            QtCore.QTimer.singleShot(0, lambda: self._load_next_block(generation))

    def update_progress(self, done, errors=()):
        self._acknowledged = max(0, min(done, self._program_count))
        self._program_errors = sorted(set(i for i in errors if 0 <= i < self._program_count))
        text = '%s: %d / %d' % (_("Acknowledged line"), self._acknowledged, self._program_count)
        if self._program_errors:
            text += ' | %s: %s' % (_("Errors on the lines"),
                                   ', '.join(str(i + 1) for i in self._program_errors[:20]))
        self.program_status.setText(text)

        selections = []
        acknowledged = self._acknowledged - 1
        indexes = [acknowledged] if acknowledged >= 0 and acknowledged not in self._program_errors else []
        for index in indexes + self._program_errors:
            if index >= self._program_loaded:
                continue
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.cursor = QtGui.QTextCursor(self.program_view.document().findBlockByNumber(index))
            selection.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
            if index in self._program_errors:
                selection.format.setBackground(QtGui.QColor('#c62828'))
                selection.format.setForeground(QtGui.QColor('white'))
            else:
                selection.format.setBackground(self.program_view.palette().highlight())
                selection.format.setForeground(self.program_view.palette().highlightedText())
            selections.append(selection)
        self._progress_selections = selections
        self._highlight_program_selection()
        self._program_line = self._program_errors[-1] if self._program_errors else acknowledged
        if self.tool.__dict__.get('_program_paused') is not True:
            self._follow_program(self.follow_cb.isChecked())

    def update_edit_controls(self):
        """Edit is possible only when the machine rules allow it and no simulation runs; Apply works in edit mode."""
        simulation = self.tool.__dict__.get('_simulation')
        allowed = self.tool.action_allowed('edit') is None and not (simulation is not None and simulation.running)
        if not allowed and self.edit_button.isChecked():
            self.edit_button.setChecked(False)
        self.edit_button.setEnabled(allowed)
        self.apply_button.setEnabled(allowed and self.edit_button.isChecked())
        self.revert_button.setEnabled(self.tool.__dict__.get('_edited_program') is not None)

    def set_edited(self, edited):
        self.edit_status.setText(_("Edited program") if edited else '')
        self.revert_button.setEnabled(edited)

    def _edit_toggled(self, checked):
        if checked:
            # the whole program must be in the view before it is edited
            while self._program_loaded < self._program_count:
                self._load_next_block(self._program_generation)
            self.program_view.setReadOnly(False)
            self.program_view.setUndoRedoEnabled(True)
            self._progress_selections = []
            self._highlight_program_selection()
        else:
            self.program_view.setReadOnly(True)
            self.program_view.setUndoRedoEnabled(False)
            # an edit that is not applied is dropped: show the program again
            lines = self._program_source
            self._program_source = None
            self.set_program(lines if lines is not None else [])
        self.update_edit_controls()

    def _apply_clicked(self):
        text = self.program_view.toPlainText()
        self.edit_button.setChecked(False)
        self.program_applied.emit(text)

    def _revert_clicked(self):
        self.edit_button.setChecked(False)
        self.program_reverted.emit()

    def _follow_program(self, checked):
        self._selected_lines.clear()
        self._selection_anchor = None
        self._highlight_program_selection()
        if 0 <= self._program_line < self._program_loaded:
            scrollbars = (self.program_view.horizontalScrollBar(), self.program_view.verticalScrollBar())
            positions = [bar.value() for bar in scrollbars]
            cursor = QtGui.QTextCursor(self.program_view.document().findBlockByNumber(self._program_line))
            cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
            self.program_view.setTextCursor(cursor)
            if checked:
                self.program_view.ensureCursorVisible()
            else:
                for bar, position in zip(scrollbars, positions):
                    bar.setValue(position)

    def append(self, text):
        self.log_view.appendPlainText(text)
