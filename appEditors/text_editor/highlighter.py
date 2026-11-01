"""
Lazy syntax highlighter: only the blocks (lines) the editor shows are highlighted, the others when they are scrolled
into view. Qt highlights every block when the text is set: a G-code of 94000 lines took 11 s to show.
"""

from PyQt6 import QtGui

from appEditors.text_editor.lexers import BOLD_TOKENS


class LazyHighlighter(QtGui.QSyntaxHighlighter):
    """
    :ivar editor:   the editor that shows the document (it has `shown_blocks`); with None every block is highlighted
    """

    def __init__(self, document, lexer, palette):
        super().__init__(document)
        self.editor = None
        self.lexer = lexer
        self.formats = {}
        self.set_palette(palette)

    def set_palette(self, palette):
        self.palette = palette
        self.formats = {}
        for name in palette.__dataclass_fields__:
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QColor(getattr(palette, name)))
            if name in BOLD_TOKENS:
                fmt.setFontWeight(QtGui.QFont.Weight.Bold)
            self.formats[name] = fmt

    def set_lexer(self, lexer):
        self.lexer = lexer

    def highlightBlock(self, text):
        if self.editor is not None and self.currentBlockState() == -1:
            first, last = self.editor.shown_blocks
            block = self.currentBlock()
            if not (first <= block.blockNumber() <= last and block.isVisible()):
                # never highlighted and not shown (outside the window, or hidden in a fold): it is highlighted when
                # it is shown (its state stays -1).
                # A block highlighted before (state 0) is always highlighted here: Qt applies the formats set by
                # this call, so returning would remove its colors for good.
                return

        formats = self.formats
        # the first rule has the priority: it is applied last
        for rx, token in reversed(self.lexer.rules):
            fmt = formats[token]
            matches = rx.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(0), match.capturedLength(0), fmt)

        self.setCurrentBlockState(0)
