"""
Minimap: a narrow overview at the right of a CodeEdit. A cached image shows one horizontal bar per line (its length is
the length of the text of the line); the lines of a long text are averaged into the rows of the widget. The rectangle
of the part shown follows the vertical scroll bar; click and drag scroll the editor.
"""

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

MINIMAP_WIDTH = 80
MIN_LINES = 300         # a shorter text hides the minimap
REBUILD_DELAY_MS = 300
PAD = 2                 # empty pixels at the left of the bars
PIXELS_PER_CHAR = 2


def build_density(text, width, max_rows):
    """
    Gray levels (uint8, rows x width) of the lines of the text: a row covers one line, or several lines when the
    text has more lines than max_rows (the level is the fraction of those lines whose bar reaches the pixel).
    """
    lengths = np.fromiter(map(len, text.split('\n')), dtype=np.int64)
    count = lengths.size
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


class Minimap(QtWidgets.QWidget):
    """Child of a CodeEdit: it takes the right viewport margin of the editor while it is shown."""

    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit
        self.wanted = False
        self.image = None
        self._rows = 0
        self._dragging = False
        self.setFixedWidth(MINIMAP_WIDTH)
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
        """The choice of the user; a text of less than MIN_LINES lines hides the minimap anyway."""
        self.wanted = bool(wanted)
        self.update_visibility()

    def is_active(self):
        return self.wanted and self.edit.blockCount() >= MIN_LINES

    def update_visibility(self, *args):
        active = self.is_active()
        if active != (not self.isHidden()):
            self.setVisible(active)
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
        levels = build_density(self.edit.toPlainText(), MINIMAP_WIDTH, max(1, self.height()))
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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.scroll_to_y(event.position().y())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.scroll_to_y(event.position().y())

    def mouseReleaseEvent(self, event):
        self._dragging = False
