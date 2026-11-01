"""
FoldModel: code folding of a CodeEdit. The regions come from Lexer.fold_regions() on a debounced scan of the whole
text; a folded region hides its lines after the first one with QTextBlock.setVisible(False).
"""

import bisect
from contextlib import contextmanager

from PyQt6 import QtCore, QtGui

SCAN_DELAY_MS = 300


class FoldModel(QtCore.QObject):
    """
    :ivar regions:  sorted list of (first, last) block numbers (both included) found by the last scan
    :ivar starts:   first -> last of the regions (the largest one when several start on the same line)
    :ivar folded:   first -> last of the folded regions
    """

    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit
        self.regions = []
        self.starts = {}
        self.folded = {}
        self.reveal_enabled = True      # False: a cursor in hidden text does not unfold (internal moves)
        self._hidden = []
        self._hidden_starts = []
        self._busy = False
        self._count = edit.document().blockCount()

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SCAN_DELAY_MS)
        self._timer.timeout.connect(self.rescan)

        edit.document().contentsChange.connect(self._on_contents_change)
        edit.cursorPositionChanged.connect(self.reveal_cursor)

    # ------------------------------------------------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------------------------------------------------
    def schedule(self):
        self._timer.start()

    def _ensure_fresh(self):
        if self._timer.isActive():
            self._timer.stop()
            self.rescan()

    def reset(self):
        """The text is replaced: no region, no fold (the blocks of the new text are all visible)."""
        self._timer.stop()
        self.regions = []
        self.starts = {}
        self.folded = {}
        self._refresh_intervals()
        self._count = self.edit.document().blockCount()

    def rescan(self):
        """Finds the regions again; a folded region stays folded when its first line still starts a region."""
        self._timer.stop()
        edit = self.edit
        regions = edit.lexer.fold_regions(edit.toPlainText()) if edit.lexer is not None else []
        starts = {}
        for first, last in regions:
            if last > starts.get(first, -1):
                starts[first] = last
        self.regions = regions
        self.starts = starts
        self._count = edit.document().blockCount()
        folded = {first: starts[first] for first in self.folded if first in starts}
        if folded != self.folded:
            old = self.folded
            self.folded = folded
            self._apply(0, max([self._count] + [last for last in old.values()]))
            self.reveal_cursor()
        edit.gutter.update()

    # ------------------------------------------------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------------------------------------------------
    def is_folded(self, number):
        return number in self.folded

    def region_starting_at(self, number):
        """(first, last) of the region that starts on the line, or None."""
        self._ensure_fresh()
        last = self.starts.get(number)
        return None if last is None else (number, last)

    def _containing(self, number, folded=None):
        """The innermost region that contains the line; folded: True/False to take only folded / unfolded ones."""
        best = None
        for first, last in self.regions:
            if first > number:
                break
            if last >= number and (folded is None or (first in self.folded) == folded):
                if best is None or first >= best[0]:
                    best = (first, last)
        return best

    # ------------------------------------------------------------------------------------------------------------
    # fold / unfold
    # ------------------------------------------------------------------------------------------------------------
    def fold(self, number):
        region = self.region_starting_at(number)
        if region is None or number in self.folded:
            return False
        first, last = region
        # a cursor inside the region goes to the end of the first line, else it would unfold the region at once
        cursor = self.edit.textCursor()
        if first < cursor.blockNumber() <= last:
            cursor = QtGui.QTextCursor(self.edit.document().findBlockByNumber(first))
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock)
            self.edit.setTextCursor(cursor)
        self.folded[first] = last
        self._apply(first, last)
        return True

    def unfold(self, number):
        if number not in self.folded:
            return False
        last = self.folded.pop(number)
        self._apply(number, last)
        return True

    def toggle(self, number):
        if self.is_folded(number):
            return self.unfold(number)
        return self.fold(number)

    def fold_all(self):
        self._ensure_fresh()
        self.folded = dict(self.starts)
        # a cursor in a region goes to the end of the first line of the outermost one
        number = self.edit.textCursor().blockNumber()
        covering = [first for first, last in self.folded.items() if first < number <= last]
        if covering:
            cursor = QtGui.QTextCursor(self.edit.document().findBlockByNumber(min(covering)))
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock)
            self.edit.setTextCursor(cursor)
        self._apply(0, self.edit.document().blockCount())

    def unfold_all(self):
        if not self.folded:
            return
        last = max(self.folded.values())
        self.folded = {}
        self._apply(0, last)

    def fold_at_cursor(self):
        """Folds the innermost unfolded region that contains the cursor line."""
        self._ensure_fresh()
        region = self._containing(self.edit.textCursor().blockNumber(), folded=False)
        return region is not None and self.fold(region[0])

    def unfold_at_cursor(self):
        """Unfolds the region folded on the cursor line (else the innermost folded one that contains it)."""
        number = self.edit.textCursor().blockNumber()
        if number in self.folded:
            return self.unfold(number)
        region = self._containing(number, folded=True)
        return region is not None and self.unfold(region[0])

    def reveal(self, number):
        """Unfolds every region that hides the block."""
        for first, last in list(self.folded.items()):
            if first < number <= last:
                self.unfold(first)

    def reveal_cursor(self):
        if not self.folded or not self.reveal_enabled:
            return
        block = self.edit.textCursor().block()
        if not block.isVisible():
            self.reveal(block.blockNumber())

    @contextmanager
    def all_visible(self):
        """Every line visible inside the block (printing); the folds are back after it."""
        folded = self.folded
        if not folded:
            yield
            return
        self.folded = {}
        self._apply(0, max(folded.values()))
        try:
            yield
        finally:
            self.folded = folded
            self._apply(0, max(folded.values()))

    # ------------------------------------------------------------------------------------------------------------
    # blocks
    # ------------------------------------------------------------------------------------------------------------
    def _intervals(self):
        """Merged (first hidden, last hidden) block numbers of the folded regions."""
        merged = []
        for first, last in sorted(self.folded.items()):
            if merged and first + 1 <= merged[-1][1] + 1:
                if last > merged[-1][1]:
                    merged[-1][1] = last
            else:
                merged.append([first + 1, last])
        return merged

    def _refresh_intervals(self):
        self._hidden = self._intervals()
        self._hidden_starts = [interval[0] for interval in self._hidden]

    def hidden_intervals(self):
        """[first, last] block numbers (both included) hidden by the folds, merged and sorted."""
        return self._hidden

    def visible_count(self):
        """The number of lines that the folds do not hide."""
        return self.edit.document().blockCount() - sum(last - first + 1 for first, last in self._hidden)

    def visible_index(self, number):
        """The index among the visible lines of a visible block (the inverse of block_of_visible)."""
        hidden = 0
        for first, last in self._hidden:
            if first > number:
                break
            hidden += min(last, number) - first + 1
        return number - hidden

    def block_of_visible(self, index):
        """The block number of the visible line at the index (0 = the first visible line)."""
        number = index
        for first, last in self._hidden:
            if first > number:
                break
            number += last - first + 1
        return number

    def next_visible(self, number):
        """The number of the first block at or after `number` that is not hidden by a fold."""
        index = bisect.bisect_right(self._hidden_starts, number) - 1
        if index >= 0 and self._hidden[index][1] >= number:
            return self._hidden[index][1] + 1
        return number

    def _apply(self, low, high):
        """Sets the visibility of the blocks low..high from the folded regions and redraws."""
        doc = self.edit.document()
        self._refresh_intervals()
        hidden = self._hidden
        index = 0
        block = doc.findBlockByNumber(max(0, low))
        number = block.blockNumber()
        while block.isValid() and number <= high:
            while index < len(hidden) and hidden[index][1] < number:
                index += 1
            visible = not (index < len(hidden) and hidden[index][0] <= number)
            if block.isVisible() != visible:
                block.setVisible(visible)
                # (a block hidden has no line; without this the scroll bar range is wrong)
                block.setLineCount(max(1, block.layout().lineCount()) if visible else 0)
            block = block.next()
            number += 1
        # (no markContentsDirty: it lays out every block of the range again, 2 s for 100000 lines; the document
        # size changes without a signal, so the scroll bar range is set here)
        layout = doc.documentLayout()
        layout.requestUpdate()
        edit = self.edit
        bar = edit.verticalScrollBar()
        bar.setRange(0, max(0, int(layout.documentSize().height()) - bar.pageStep()))
        edit.viewport().update()
        edit.gutter.update()
        edit.highlight_shown_blocks()
        if edit.minimap is not None:
            # (the image has no row for a hidden line: it is made again for the new folds)
            edit.minimap.schedule()

    # ------------------------------------------------------------------------------------------------------------
    # edits
    # ------------------------------------------------------------------------------------------------------------
    def _on_contents_change(self, position, removed, added):
        # (formatting of the highlighter and the redraw of a fold change no characters)
        if self._busy or removed == added:
            return
        self.schedule()
        doc = self.edit.document()
        count = doc.blockCount()
        delta = count - self._count
        self._count = count
        if not self.folded:
            return
        edited = doc.findBlock(position).blockNumber()
        kept = {}
        low = high = None
        for first, last in self.folded.items():
            inside = first <= edited <= last and (edited > first or delta != 0)
            removed_start = delta < 0 and edited < first <= edited - delta
            if inside or removed_start:
                low = first if low is None else min(low, first)
                high = last + max(delta, 0) if high is None else max(high, last + max(delta, 0))
            elif first > edited:
                kept[first + delta] = last + delta
            else:
                kept[first] = last
        self.folded = kept
        if low is not None:
            self._apply(low, high)
        else:
            self._refresh_intervals()
