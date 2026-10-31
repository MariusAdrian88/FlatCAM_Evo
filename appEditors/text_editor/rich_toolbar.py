# ##########################################################
# FlatCAM EVO: rich text toolbar of the Document editor     #
# ##########################################################

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

HEADING_SCALE = {0: 1.0, 1: 2.0, 2: 1.5, 3: 1.17}
BOLD = QtGui.QFont.Weight.Bold.value
NORMAL = QtGui.QFont.Weight.Normal.value


def _color_icon(letter, color):
    """A small icon: a letter over a bar of the given color."""
    pix = QtGui.QPixmap(16, 16)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pix)
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(11)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(128, 128, 128))
    painter.drawText(QtCore.QRect(0, 0, 16, 12), Qt.AlignmentFlag.AlignCenter, letter)
    painter.fillRect(0, 13, 16, 3, color)
    painter.end()
    return QtGui.QIcon(pix)


class RichTextToolbar(QtWidgets.QToolBar):
    """
    Formatting toolbar bound to a QTextEdit. The buttons show the format at the cursor; a command changes the
    editor only on user action (``triggered`` / ``activated``), never when the state follows the cursor.
    """

    def __init__(self, editor, font_sizes=None, resource_location='assets/resources', parent=None):
        super().__init__(parent)
        self.editor = editor
        self.res = resource_location
        self.text_color = QtGui.QColor('black')
        self.highlight_color = QtGui.QColor('yellow')
        self.setMovable(False)
        self.setIconSize(QtCore.QSize(18, 18))

        # font family and size
        self.font_cb = QtWidgets.QFontComboBox()
        self.font_cb.setToolTip(_("Font"))
        self.font_cb.activated.connect(self._on_family)
        self.addWidget(self.font_cb)

        self.size_cb = QtWidgets.QComboBox()
        self.size_cb.setEditable(True)
        self.size_cb.setToolTip(_("Font size"))
        self.size_cb.addItems([str(s) for s in (font_sizes or [])])
        self.size_cb.setMinimumContentsLength(3)
        self.size_cb.setValidator(QtGui.QDoubleValidator(1.0, 400.0, 1, self.size_cb))
        self.size_cb.activated.connect(self._on_size)
        self.size_cb.lineEdit().returnPressed.connect(self._on_size)
        self.addWidget(self.size_cb)

        self.heading_cb = QtWidgets.QComboBox()
        self.heading_cb.setToolTip(_("Heading level"))
        self.heading_cb.addItems([_("Normal"), _("Heading 1"), _("Heading 2"), _("Heading 3")])
        self.heading_cb.activated.connect(self._on_heading)
        self.addWidget(self.heading_cb)
        self.addSeparator()

        # character styles
        self.bold_act = self._toggle(_("Bold"), 'bold32.png', None, "Ctrl+B", self._on_bold)
        self.italic_act = self._toggle(_("Italic"), 'italic32.png', None, "Ctrl+I", self._on_italic)
        self.underline_act = self._toggle(_("Underline"), 'underline32.png', None, "Ctrl+U", self._on_underline)
        self.strike_act = self._toggle(_("Strikethrough"), None, "S", None, self._on_strike)
        f = self.widgetForAction(self.strike_act).font()
        f.setStrikeOut(True)
        self.widgetForAction(self.strike_act).setFont(f)

        self.color_act = self.addAction(_color_icon("A", self.text_color), _("Text color"))
        self.color_act.triggered.connect(self._on_text_color)
        self.highlight_act = self.addAction(_color_icon("A", self.highlight_color), _("Highlight color"))
        self.highlight_act.triggered.connect(self._on_highlight_color)
        self.addSeparator()

        # alignment, exclusive
        self.align_group = QtGui.QActionGroup(self)
        self.align_group.setExclusive(True)
        self.align_acts = {}
        for flag, name, icon in ((Qt.AlignmentFlag.AlignLeft, _("Align Left"), 'align_left32.png'),
                                 (Qt.AlignmentFlag.AlignHCenter, _("Center"), 'align_center32.png'),
                                 (Qt.AlignmentFlag.AlignRight, _("Align Right"), 'align_right32.png'),
                                 (Qt.AlignmentFlag.AlignJustify, _("Justify"), 'align_justify32.png')):
            act = self._toggle(name, icon, None, None, lambda _checked=False, fl=flag: self._on_align(fl))
            self.align_group.addAction(act)
            self.align_acts[flag] = act
        self.addSeparator()

        # lists
        self.bullet_act = self._toggle(_("Bullet list"), None, "•", None,
                                       lambda _c=False: self._on_list(QtGui.QTextListFormat.Style.ListDisc))
        self.number_act = self._toggle(_("Numbered list"), None, "1.", None,
                                       lambda _c=False: self._on_list(QtGui.QTextListFormat.Style.ListDecimal))
        self.addSeparator()

        self.clear_act = self.addAction(_("Clear format"))
        self.clear_act.setText("Tx")
        self.clear_act.setToolTip(_("Clear formatting"))
        self.clear_act.triggered.connect(self._on_clear)

        editor.currentCharFormatChanged.connect(self._on_char_format)
        editor.cursorPositionChanged.connect(self.sync)
        self.sync()

    # ----------------------------------------------------------------------------------------------------------
    def _toggle(self, tip, icon, text, shortcut, slot):
        act = QtGui.QAction(self)
        act.setCheckable(True)
        act.setToolTip(tip + (" (%s)" % shortcut if shortcut else ""))
        if icon:
            act.setIcon(QtGui.QIcon("%s/%s" % (self.res, icon)))
        if text:
            act.setText(text)
        if shortcut:
            act.setShortcut(QtGui.QKeySequence(shortcut))
            act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            self.editor.addAction(act)
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _done(self):
        self.editor.setFocus()
        self.sync()

    def _blocks(self):
        """The blocks of the selection (or the one block of the cursor)."""
        cur = self.editor.textCursor()
        doc = self.editor.document()
        block = doc.findBlock(cur.selectionStart())
        end = doc.findBlock(cur.selectionEnd()).blockNumber()
        out = []
        while block.isValid() and block.blockNumber() <= end:
            out.append(block)
            block = block.next()
        return out

    def _merge(self, fmt):
        self.editor.mergeCurrentCharFormat(fmt)

    # ---------------------------------------------------------------------------------------------- commands
    def _on_family(self, _idx=0):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontFamilies([self.font_cb.currentFont().family()])
        self._merge(fmt)
        self._done()

    def _on_size(self, _idx=0):
        try:
            size = float(self.size_cb.currentText().replace(',', '.'))
        except ValueError:
            return
        if size <= 0:
            return
        fmt = QtGui.QTextCharFormat()
        fmt.setFontPointSize(size)
        self._merge(fmt)
        self._done()

    def _on_bold(self, checked=False):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontWeight(BOLD if checked else NORMAL)
        self._merge(fmt)
        self._done()

    def _on_italic(self, checked=False):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontItalic(checked)
        self._merge(fmt)
        self._done()

    def _on_underline(self, checked=False):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontUnderline(checked)
        self._merge(fmt)
        self._done()

    def _on_strike(self, checked=False):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontStrikeOut(checked)
        self._merge(fmt)
        self._done()

    def _on_text_color(self):
        color = QtWidgets.QColorDialog.getColor(self.text_color, self, _("Text color"))
        if not color.isValid():
            return
        self.text_color = color
        self.color_act.setIcon(_color_icon("A", color))
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QBrush(color))
        self._merge(fmt)
        self._done()

    def _on_highlight_color(self):
        color = QtWidgets.QColorDialog.getColor(self.highlight_color, self, _("Highlight color"))
        if not color.isValid():
            return
        self.highlight_color = color
        self.highlight_act.setIcon(_color_icon("A", color))
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QBrush(color))
        self._merge(fmt)
        self._done()

    def _on_align(self, flag):
        self.editor.setAlignment(flag)
        self._done()

    def _on_list(self, style):
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        blocks = self._blocks()
        current = cur.currentList()
        if current is not None and current.format().style() == style:
            for block in blocks:
                lst = block.textList()
                if lst is not None:
                    lst.remove(block)
                    bc = QtGui.QTextCursor(block)
                    bf = bc.blockFormat()
                    bf.setIndent(0)
                    bc.setBlockFormat(bf)
        else:
            fmt = QtGui.QTextListFormat()
            fmt.setStyle(style)
            fmt.setIndent(1)
            lst = QtGui.QTextCursor(blocks[0]).createList(fmt)
            for block in blocks[1:]:
                lst.add(block)
        cur.endEditBlock()
        self._done()

    def _on_heading(self, idx=0):
        level = max(0, min(3, int(idx)))
        base = self.editor.document().defaultFont().pointSizeF()
        if base <= 0:
            base = 12.0
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        for block in self._blocks():
            bc = QtGui.QTextCursor(block)
            bf = bc.blockFormat()
            bf.setHeadingLevel(level)
            bc.setBlockFormat(bf)
            bc.movePosition(QtGui.QTextCursor.MoveOperation.StartOfBlock)
            bc.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock, QtGui.QTextCursor.MoveMode.KeepAnchor)
            cf = QtGui.QTextCharFormat()
            cf.setFontPointSize(base * HEADING_SCALE[level])
            cf.setFontWeight(BOLD if level else NORMAL)
            bc.mergeCharFormat(cf)
        cur.endEditBlock()
        self._done()

    def _on_clear(self):
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        if cur.hasSelection():
            cur.setCharFormat(QtGui.QTextCharFormat())
        for block in self._blocks():
            bc = QtGui.QTextCursor(block)
            bf = bc.blockFormat()
            bf.setHeadingLevel(0)
            bc.setBlockFormat(bf)
        if not cur.hasSelection():
            cur.setCharFormat(QtGui.QTextCharFormat())
        cur.endEditBlock()
        self.editor.setTextCursor(cur)
        self._done()

    # ------------------------------------------------------------------------------------------------- state
    def _on_char_format(self, fmt):
        self._show_char_format(fmt)

    def sync(self):
        """Show the format at the cursor."""
        cur = self.editor.textCursor()
        self._show_char_format(self.editor.currentCharFormat())
        flags = cur.blockFormat().alignment()
        if flags & Qt.AlignmentFlag.AlignHCenter:
            flag = Qt.AlignmentFlag.AlignHCenter
        elif flags & Qt.AlignmentFlag.AlignRight:
            flag = Qt.AlignmentFlag.AlignRight
        elif flags & Qt.AlignmentFlag.AlignJustify:
            flag = Qt.AlignmentFlag.AlignJustify
        else:
            flag = Qt.AlignmentFlag.AlignLeft
        self.align_acts[flag].setChecked(True)

        lst = cur.currentList()
        style = lst.format().style() if lst is not None else None
        self.bullet_act.setChecked(style == QtGui.QTextListFormat.Style.ListDisc)
        self.number_act.setChecked(style == QtGui.QTextListFormat.Style.ListDecimal)
        self.heading_cb.setCurrentIndex(max(0, min(3, cur.blockFormat().headingLevel())))

    def _show_char_format(self, fmt):
        default = self.editor.document().defaultFont()
        self.bold_act.setChecked(fmt.fontWeight() > NORMAL and fmt.hasProperty(QtGui.QTextFormat.Property.FontWeight))
        self.italic_act.setChecked(fmt.fontItalic())
        self.underline_act.setChecked(fmt.fontUnderline())
        self.strike_act.setChecked(fmt.fontStrikeOut())
        families = fmt.fontFamilies()
        family = families[0] if families else default.family()
        self.font_cb.setCurrentFont(QtGui.QFont(family))
        size = fmt.fontPointSize() or default.pointSizeF()
        if size > 0:
            text = ('%g' % size)
            if self.size_cb.currentText() != text:
                self.size_cb.setCurrentText(text)
