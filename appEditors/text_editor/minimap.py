"""
Minimap: a narrow overview at the right of a CodeEdit. A cached image shows one horizontal bar per line (its length is
the length of the text of the line); the lines of a long text are averaged into the rows of the widget. The rectangle
of the part shown follows the vertical scroll bar; click and drag scroll the editor. A lens at the left of the minimap
shows the code under the mouse pointer.
"""

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from appEditors.text_editor.highlighter import LazyHighlighter

Qt = QtCore.Qt

MINIMAP_WIDTH = 80
LENS_LINES = 15         # lines of code in the lens; the line under the pointer is in the middle
LENS_WIDTH = 520        # the most pixels; a narrow editor gets a narrower lens
REBUILD_DELAY_MS = 300
PAD = 2                 # empty pixels at the left of the bars
PIXELS_PER_CHAR = 2


def build_density(text, width, max_rows, hidden=()):
    """
    Gray levels (uint8, rows x width) of the lines of the text: a row covers one line, or several lines when the
    text has more lines than max_rows (the level is the fraction of those lines whose bar reaches the pixel).

    :param hidden:  (first, last) line numbers (both included) hidden by folds: they get no row, as they have no
                    line in the editor (the scroll bar does not count them)
    """
    lengths = np.fromiter(map(len, text.split('\n')), dtype=np.int64)
    if hidden:
        keep = np.ones(lengths.size, dtype=bool)
        for first, last in hidden:
            keep[first:last + 1] = False
        lengths = lengths[keep]
    count = lengths.size
    if count == 0:
        return np.zeros((1, width), dtype=np.uint8)
    rows = max(1, min(count, max_rows))
    longest = width - PAD
    bars = np.minimum(lengths * PIXELS_PER_CHAR, longest)
    bins = (np.arange(count, dtype=np.int64) * rows) // count
    histogram = np.bincount(bins * (longest + 1) + bars, minlength=rows * (longest + 1)).reshape(rows, longest + 1)
    # reaching[:, k] = lines of the row with a bar of at least k pixels
    reaching = histogram[:, ::-1].cumsum(axis=1)[:, ::-1]
    per_row = np.maximum(1, np.bincount(bins, minlength=rows))[:, None]
    levels = np.zeros((rows, width), dtype=np.uint8)
    levels[:, PAD:] = (reaching[:, 1:] * 255) // per_row
    return levels


class _LensView(QtWidgets.QPlainTextEdit):
    """The text of the lens, with the block numbers of the editor in a gutter at the left (the text itself has no
    number: the highlight rules that start at the line start must match)."""

    def __init__(self):
        super().__init__()
        self.numbers = []           # block number + 1 of each line
        self.number_color = QtGui.QColor('#808080')
        self.gutter_color = QtGui.QColor('#e6e6e6')
        self.gutter = QtWidgets.QWidget(self)
        self.gutter.paintEvent = self._paint_gutter

    def set_numbers(self, numbers, number_color, gutter_color):
        self.numbers = numbers
        self.number_color = QtGui.QColor(number_color)
        self.gutter_color = QtGui.QColor(gutter_color)
        width = self.fontMetrics().horizontalAdvance('9' * len(str(max(numbers, default=1)))) + 10
        self.setViewportMargins(width, 0, 0, 0)
        area = self.contentsRect()
        self.gutter.setGeometry(area.left(), area.top(), width, area.height())
        self.gutter.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        area = self.contentsRect()
        self.gutter.setGeometry(area.left(), area.top(), self.viewportMargins().left(), area.height())

    def _paint_gutter(self, event):
        painter = QtGui.QPainter(self.gutter)
        painter.fillRect(event.rect(), self.gutter_color)
        painter.setPen(self.number_color)
        painter.setFont(self.font())
        offset = self.contentOffset()
        width = self.gutter.width() - 5
        block = self.firstVisibleBlock()
        while block.isValid():
            row = block.blockNumber()
            if row >= len(self.numbers):
                break
            rect = self.blockBoundingGeometry(block).translated(offset)
            painter.drawText(QtCore.QRectF(0, rect.top(), width, rect.height()),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), str(self.numbers[row]))
            block = block.next()
        painter.end()


class MinimapLens(QtWidgets.QFrame):
    """A popup with some lines of the code, in the colors of the editor; it takes no focus and no mouse input."""

    def __init__(self, edit):
        super().__init__(edit, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.edit = edit
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFrameShape(QtWidgets.QFrame.Shape.Box)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        self.view = _LensView()
        self.view.setReadOnly(True)
        self.view.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.view)
        # (a few lines: every block is highlighted, editor None)
        self.highlighter = LazyHighlighter(self.view.document(), edit.lexer, edit.colors)
        self.text = ''          # the text shown: line numbers and lines
        self.center = None      # block number of the line under the pointer

    def show_lines(self, center, global_y, right):
        """Shows the lines around the block `center` (visible lines only); the lens is centered on global_y and ends
        at the global x `right`."""
        edit = self.edit
        fold = edit.fold
        index = fold.visible_index(center)
        count = fold.visible_count()
        start = max(0, min(index - LENS_LINES // 2, count - LENS_LINES))
        numbers = [fold.block_of_visible(k) for k in range(start, min(count, start + LENS_LINES))]
        doc = edit.document()
        lines = [doc.findBlockByNumber(number).text()[:300] for number in numbers]

        if self.highlighter.lexer is not edit.lexer or self.highlighter.palette is not edit.colors:
            self.highlighter.set_lexer(edit.lexer)
            self.highlighter.set_palette(edit.colors)
        if self.view.font() != edit.font():
            self.view.setFont(edit.font())
        self.view.setPalette(edit.palette())
        text = '\n'.join(lines)
        if text != self.text:
            self.view.setPlainText(text)
            self.text = text
        self.center = center
        self.view.set_numbers([number + 1 for number in numbers], edit.colors.gutter_fg, edit.colors.gutter_bg)
        self._mark(numbers.index(center) if center in numbers else -1)

        metrics = QtGui.QFontMetrics(self.view.font())
        height = metrics.lineSpacing() * len(lines) + 2 * self.view.document().documentMargin() + 4
        lens_width = min(LENS_WIDTH, max(200, edit.viewport().width() * 2 // 3))
        self.resize(int(lens_width), int(height))
        top = global_y - self.height() // 2
        area = edit.mapToGlobal(QtCore.QPoint(0, 0)).y()
        top = max(area, min(top, area + edit.height() - self.height()))
        self.move(int(right - self.width()), int(top))
        self.show()

    def _mark(self, row):
        """The line under the pointer gets the current line color."""
        selections = []
        if row >= 0:
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(QtGui.QColor(self.edit.colors.current_line_bg))
            selection.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QtGui.QTextCursor(self.view.document().findBlockByNumber(row))
            selections.append(selection)
        self.view.setExtraSelections(selections)


class Minimap(QtWidgets.QWidget):
    """Child of a CodeEdit: it takes the right viewport margin of the editor while it is shown."""

    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit
        self.wanted = False
        self.image = None
        self._rows = 0
        self._dragging = False
        self.lens = None        # MinimapLens, made at the first hover
        self.setFixedWidth(MINIMAP_WIDTH)
        self.setMouseTracking(True)
        self.hide()

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REBUILD_DELAY_MS)
        self._timer.timeout.connect(self.rebuild)

        edit.document().contentsChange.connect(self._on_contents_change)
        edit.blockCountChanged.connect(self.update_visibility)
        scroll_bar = edit.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._repaint)
        scroll_bar.rangeChanged.connect(self._repaint)
        edit.attach_minimap(self)

    # ------------------------------------------------------------------------------------------------------------
    # visibility
    # ------------------------------------------------------------------------------------------------------------
    def set_wanted(self, wanted):
        """The choice of the user (the Map button)."""
        self.wanted = bool(wanted)
        self.update_visibility()

    def is_active(self):
        return self.wanted

    def update_visibility(self, *args):
        active = self.is_active()
        if active != (not self.isHidden()):
            self.setVisible(active)
            if not active:
                self.hide_lens()
            self.edit.set_right_margin(MINIMAP_WIDTH if active else 0)
            if active:
                self.schedule()

    def _repaint(self, *args):
        self.update()

    def schedule(self):
        if self.is_active():
            self._timer.start()

    def _on_contents_change(self, position, removed, added):
        # (the formatting of the highlighter changes no characters)
        if removed != added:
            self.schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule()

    # ------------------------------------------------------------------------------------------------------------
    # image
    # ------------------------------------------------------------------------------------------------------------
    def rebuild(self):
        self._timer.stop()
        if not self.is_active():
            return
        colors = self.edit.colors
        levels = build_density(self.edit.toPlainText(), MINIMAP_WIDTH, max(1, self.height()),
                               hidden=self.edit.fold.hidden_intervals())
        rows = levels.shape[0]
        data = levels.tobytes()
        image = QtGui.QImage(data, MINIMAP_WIDTH, rows, MINIMAP_WIDTH, QtGui.QImage.Format.Format_Indexed8)
        background = QtGui.QColor(colors.minimap_bg)
        foreground = QtGui.QColor(colors.minimap_fg)
        table = []
        for level in range(256):
            mix = level / 255.0
            table.append(QtGui.QColor(
                round(background.red() + (foreground.red() - background.red()) * mix),
                round(background.green() + (foreground.green() - background.green()) * mix),
                round(background.blue() + (foreground.blue() - background.blue()) * mix)).rgb())
        image.setColorTable(table)
        self.image = image.copy()
        self._rows = rows
        self.update()

    # ------------------------------------------------------------------------------------------------------------
    # paint and mouse
    # ------------------------------------------------------------------------------------------------------------
    def _scale(self):
        """(scroll bar units in total, pixels of the content): the part shown is value / total of the pixels."""
        bar = self.edit.verticalScrollBar()
        return bar.maximum() + bar.pageStep(), self._rows

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(self.edit.colors.minimap_bg))
        if self.image is not None:
            painter.drawImage(0, 0, self.image)
        total, pixels = self._scale()
        if total > 0 and pixels > 0:
            bar = self.edit.verticalScrollBar()
            top = bar.value() / total * pixels
            height = max(6.0, bar.pageStep() / total * pixels)
            color = QtGui.QColor(self.edit.colors.minimap_viewport)
            color.setAlpha(70)
            painter.fillRect(QtCore.QRectF(0, top, self.width(), height), color)
            color.setAlpha(160)
            painter.setPen(color)
            painter.drawRect(QtCore.QRectF(0, top, self.width() - 1, height))
        painter.end()

    def scroll_to_y(self, y):
        """Scrolls the editor so the part at the height y of the minimap is in the middle of the window."""
        total, pixels = self._scale()
        if total <= 0 or pixels <= 0:
            return
        bar = self.edit.verticalScrollBar()
        fraction = min(1.0, max(0.0, y / pixels))
        bar.setValue(round(fraction * total - bar.pageStep() / 2))

    def block_at_y(self, y):
        """The block number of the line drawn at the height y of the minimap, or None below the last line."""
        pixels = self._rows
        if pixels <= 0 or not 0 <= y < pixels:
            return None
        fold = self.edit.fold
        count = fold.visible_count()
        index = min(count - 1, int(y / pixels * count))
        return fold.block_of_visible(index)

    def show_lens(self, y):
        number = self.block_at_y(y)
        if number is None:
            self.hide_lens()
            return
        if self.lens is None:
            self.lens = MinimapLens(self.edit)
        origin = self.mapToGlobal(QtCore.QPoint(0, int(y)))
        self.lens.show_lines(number, origin.y(), origin.x() - 4)

    def hide_lens(self):
        if self.lens is not None:
            self.lens.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.hide_lens()
            self.scroll_to_y(event.position().y())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.scroll_to_y(event.position().y())
        else:
            self.show_lens(event.position().y())

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self.show_lens(event.position().y())

    def leaveEvent(self, event):
        self.hide_lens()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.hide_lens()
        super().hideEvent(event)
