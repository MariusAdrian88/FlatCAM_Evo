"""One PDF document and its navigation controls, owned by a plot-area tab."""

from PyQt6 import QtCore, QtWidgets, QtPdf, QtPdfWidgets

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class PDFView(QtPdfWidgets.QPdfView):
    displayed_zoom_changed = QtCore.pyqtSignal(float)

    def __init__(self, parent):
        super().__init__(parent)
        self.pan_start = None
        self.pan_position = None
        self.panning = False
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.zoomFactorChanged.connect(self.update_zoom)
        self.zoomModeChanged.connect(self.update_zoom)
        self.pageNavigator().currentPageChanged.connect(self.update_zoom)

    def displayed_zoom(self):
        document = self.document()
        if self.zoomMode() == self.ZoomMode.Custom or document is None or not document.pageCount():
            return self.zoomFactor()
        # QPdfView keeps zoomFactor unchanged in fit modes; match its page layout calculation.
        size = (document.pagePointSize(self.pageNavigator().currentPage()) *
                (QtWidgets.QApplication.primaryScreen().logicalDotsPerInch() / 72)).toSize()
        margins = self.documentMargins()
        width = max(1, self.viewport().width() - margins.left() - margins.right())
        if self.zoomMode() == self.ZoomMode.FitToWidth:
            return width / max(1, size.width())
        available = QtCore.QSize(width, max(1, self.viewport().height() - self.pageSpacing()))
        return size.scaled(available, QtCore.Qt.AspectRatioMode.KeepAspectRatio).width() / max(1, size.width())

    def update_zoom(self, *args):
        self.displayed_zoom_changed.emit(self.displayed_zoom())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_zoom()

    def zoom_by(self, factor):
        zoom = max(0.05, min(10, self.displayed_zoom() * factor))
        self.setZoomMode(self.ZoomMode.Custom)
        self.setZoomFactor(zoom)

    def page_offset(self, zoom):
        size = self.document().pagePointSize(self.pageNavigator().currentPage())
        width = round(size.width() * QtWidgets.QApplication.primaryScreen().logicalDotsPerInch() / 72 * zoom)
        margins = self.documentMargins()
        x = (max(self.viewport().width(), width + margins.left() + margins.right()) - width) / 2
        return QtCore.QPointF(x, margins.top())

    def wheelEvent(self, event):
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta and self.document() is not None and self.document().pageCount():
                zoom = self.displayed_zoom()
                scroll = QtCore.QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
                anchor = (scroll + event.position() - self.page_offset(zoom)) / zoom
                self.zoom_by(1.1 if delta > 0 else 1 / 1.1)
                scroll = anchor * self.zoomFactor() + self.page_offset(self.zoomFactor()) - event.position()
                self.horizontalScrollBar().setValue(round(scroll.x()))
                self.verticalScrollBar().setValue(round(scroll.y()))
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            if event.key() in (QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Equal):
                self.zoom_by(1.1)
            elif event.key() == QtCore.Qt.Key.Key_Minus:
                self.zoom_by(1 / 1.1)
            elif event.key() == QtCore.Qt.Key.Key_0:
                self.setZoomMode(self.ZoomMode.Custom)
                self.setZoomFactor(1)
            else:
                return super().keyPressEvent(event)
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() in (QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.MiddleButton):
            self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            self.pan_start = self.pan_position = event.position().toPoint()
            self.panning = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pan_start is None:
            return super().mouseMoveEvent(event)
        position = event.position().toPoint()
        if (position - self.pan_start).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
            self.panning = True
        if self.panning:
            self.viewport().setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            delta = position - self.pan_position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.pan_position = position
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.pan_start is not None and event.button() in (
                QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.MiddleButton):
            show_menu = event.button() == QtCore.Qt.MouseButton.RightButton and not self.panning
            self.pan_start = self.pan_position = None
            self.viewport().unsetCursor()
            event.accept()
            if show_menu:
                self.customContextMenuRequested.emit(event.position().toPoint())
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        # Mouse menus open on release only, so Windows' press-time menu can't interrupt a drag.
        if event.reason() == event.Reason.Keyboard:
            self.customContextMenuRequested.emit(self.viewport().rect().center())
        event.accept()


class PDFViewerUI(QtWidgets.QWidget):
    def __init__(self, data, display_name, owner=None):
        super().__init__(None)
        self.setObjectName('pdf_viewer_tab')
        self.owner = owner
        self.document = None
        self.buffer = None
        self.data = b''
        self.display_name = display_name
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        layout.addLayout(controls)
        self.previous = QtWidgets.QToolButton(self)
        self.previous.setText('◀')
        self.previous.setToolTip(_('Previous page'))
        self.next = QtWidgets.QToolButton(self)
        self.next.setText('▶')
        self.next.setToolTip(_('Next page'))
        self.page = QtWidgets.QSpinBox(self)
        self.page.setAccessibleName(_('Page'))
        self.page_count = QtWidgets.QLabel(self)
        for widget in (self.previous, self.page, self.page_count, self.next):
            controls.addWidget(widget)
        self.fit_page = QtWidgets.QPushButton(_('Fit page'), self)
        self.fit_width = QtWidgets.QPushButton(_('Fit width'), self)
        self.zoom = QtWidgets.QDoubleSpinBox(self)
        self.zoom.setRange(5, 1000)
        self.zoom.setValue(100)
        self.zoom.setSuffix('%')
        self.zoom.setAccessibleName(_('Zoom'))
        self.zoom.setToolTip(_(
            '<p>Set the zoom percentage or use these PDF navigation controls.</p>'
            '<table cellspacing="4">'
            '<tr><th>Control / gesture</th><th>Action</th></tr>'
            '<tr><td>Zoom entry / slider</td><td>Set zoom (5–1000%)</td></tr>'
            '<tr><td>Ctrl+wheel</td><td>Zoom at pointer</td></tr>'
            '<tr><td>Wheel</td><td>Scroll</td></tr>'
            '<tr><td>Right or middle drag</td><td>Pan</td></tr>'
            '<tr><td>Ctrl++ / Ctrl+-</td><td>Zoom in / out</td></tr>'
            '<tr><td>Ctrl+0</td><td>Reset to 100%</td></tr>'
            '<tr><td>Fit page / Fit width</td><td>Fit whole page / page width</td></tr>'
            '<tr><td>Right-click</td><td>Save / Print menu</td></tr>'
            '</table>'))
        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self)
        self.zoom_slider.setRange(5, 1000)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setAccessibleName(_('Zoom'))
        self.zoom_slider.setMinimumWidth(100)
        self.zoom_slider.setMaximumWidth(200)
        for widget in (self.fit_page, self.fit_width, self.zoom_slider, self.zoom):
            controls.addWidget(widget)
        controls.addStretch()
        self.view = PDFView(self)
        layout.addWidget(self.view)
        self.view.setPageMode(QtPdfWidgets.QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.FitInView)
        self.previous.clicked.connect(lambda: self.page.setValue(self.page.value() - 1))
        self.next.clicked.connect(lambda: self.page.setValue(self.page.value() + 1))
        self.page.valueChanged.connect(self.go_to_page)
        self.view.pageNavigator().currentPageChanged.connect(self.on_page_changed)
        self.fit_page.clicked.connect(lambda: self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.FitInView))
        self.fit_width.clicked.connect(lambda: self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.FitToWidth))
        self.zoom.valueChanged.connect(self.on_zoom)
        self.zoom_slider.valueChanged.connect(self.on_zoom)
        self.view.displayed_zoom_changed.connect(self.on_view_zoom)
        try:
            self.set_pdf(data, display_name)
        except (ValueError, RuntimeError):
            self.deleteLater()
            raise

    def set_pdf(self, data, display_name):
        document = QtPdf.QPdfDocument(self)
        buffer = QtCore.QBuffer(document)
        # setData owns its storage; QBuffer(QByteArray) would borrow a temporary's address.
        buffer.setData(data)
        if not buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            document.deleteLater()
            raise ValueError(_('Cannot open PDF buffer.'))
        document.load(buffer)
        if document.status() != QtPdf.QPdfDocument.Status.Ready or not document.pageCount():
            document.close()
            buffer.close()
            document.deleteLater()
            raise ValueError(_('Invalid or unsupported PDF document.'))
        previous_document = self.document
        self.view.setDocument(document)
        self.document, self.buffer = document, buffer
        self.data, self.display_name = bytes(data), display_name
        self.page.setRange(1, document.pageCount())
        self.page.setValue(1)
        self.page_count.setText('/ %d' % document.pageCount())
        self.go_to_page(1)
        self.view.update_zoom()
        if previous_document is not None:
            previous_document.close()
            previous_document.deleteLater()

    def go_to_page(self, page):
        self.view.pageNavigator().jump(page - 1, QtCore.QPointF())
        self.previous.setEnabled(page > 1)
        self.next.setEnabled(self.document is not None and page < self.document.pageCount())

    def on_page_changed(self, page):
        blocker = QtCore.QSignalBlocker(self.page)
        self.page.setValue(page + 1)
        del blocker

    def on_zoom(self, percent):
        self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(percent / 100)

    def on_view_zoom(self, factor):
        zoom_blocker = QtCore.QSignalBlocker(self.zoom)
        slider_blocker = QtCore.QSignalBlocker(self.zoom_slider)
        self.zoom.setValue(factor * 100)
        self.zoom_slider.setValue(round(factor * 100))
        del zoom_blocker, slider_blocker

    def release(self):
        # QPdfView connects its link model to the document; passing None triggers
        # "QObject::connect(QPdfDocument, QPdfLinkModel): invalid nullptr parameter".
        # Swap in a fresh empty document owned by the view so internal connections stay valid.
        self.view.setDocument(QtPdf.QPdfDocument(self.view))
        if self.document is not None:
            self.document.close()
            self.buffer.close()
            self.document.deleteLater()
            self.document = None
            self.buffer = None
        self.data = b''
        self.owner = None
