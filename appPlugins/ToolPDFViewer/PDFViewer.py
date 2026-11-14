"""Independent, retained-byte PDF tabs. All Qt document and view work stays on the GUI thread."""

import math
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets, QtPdf, QtPrintSupport

from appGUI.GUIElements import FCFileSaveDialog
from .PDFViewerUI import PDFViewerUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


# At most two adjacent 8 MiB band images can coexist during iteration (16 MiB).
# PDFium document caches and the platform print spooler are separate from this raster budget.
MAX_BAND_BYTES = 8 * 1024 * 1024


def render_bands(document, page, size, max_bytes=MAX_BAND_BYTES):
    width, height = size.width(), size.height()
    rows = max_bytes // (width * 4) if width > 0 else 0
    if rows < 1 or height < 1 or max(width, height) >= 2 ** 30:
        raise ValueError(_('PDF page exceeds the regional rendering limit.'))
    for y in range(0, height, rows):
        count = min(rows, height - y)
        options = QtPdf.QPdfDocumentRenderOptions()
        options.setScaledSize(size)
        # Qt uses QRect.right()/bottom() for matrix extents: include the far edge,
        # not width-1/height-1, or each band is scaled down and loses edge pixels.
        options.setScaledClipRect(QtCore.QRect(0, y, width + 1, count + 1))
        image = document.render(page, QtCore.QSize(width, count), options)
        if image.isNull():
            raise ValueError(_('Cannot render PDF page.'))
        yield y, image


def print_target(points, dpi, paper, printable, mode):
    if mode not in ('actual', 'fit') or points.width() <= 0 or points.height() <= 0 or dpi <= 0:
        raise ValueError(_('Invalid print scale or page size.'))
    scale = dpi / 72 if mode == 'actual' else min(
        printable.width() / points.width(), printable.height() / points.height())
    width, height = points.width() * scale, points.height() * scale
    area = paper if mode == 'actual' else printable
    target = QtCore.QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)
    return target, not printable.contains(target)


def selected_pages(count, ranges):
    return [page for page in range(count) if ranges.isEmpty() or ranges.contains(page + 1)]


def page_layout(document, printer, page, mode):
    points = document.pagePointSize(page)
    layout = QtGui.QPageLayout(printer.pageLayout())
    layout.setMode(QtGui.QPageLayout.Mode.StandardMode)
    layout.setOrientation(QtGui.QPageLayout.Orientation.Landscape if points.width() > points.height() else
                          QtGui.QPageLayout.Orientation.Portrait)
    dpi = printer.resolution()
    printable = QtCore.QRectF(layout.paintRectPixels(dpi))
    target, clipped = print_target(points, dpi, QtCore.QRectF(layout.fullRectPixels(dpi)), printable, mode)
    return layout, printable, target, clipped


def print_pages(document, printer, mode, pages):
    if not pages:
        return False
    painter = QtGui.QPainter()
    try:
        for index, page in enumerate(pages):
            layout, printable, target, _clipped = page_layout(document, printer, page, mode)
            if not printer.setPageLayout(layout):
                raise OSError(_('Cannot set printer page layout.'))
            printer.setFullPage(True)
            if index == 0:
                if not painter.begin(printer):
                    raise OSError(_('Cannot start printer.'))
            elif not printer.newPage():
                raise OSError(_('Cannot start next printed page.'))
            painter.setClipRect(printable)
            size = QtCore.QSize(math.ceil(target.width()), math.ceil(target.height()))
            for y, image in render_bands(document, page, size):
                rect = QtCore.QRectF(target.x(), target.y() + y * target.height() / size.height(),
                                     target.width(), image.height() * target.height() / size.height())
                painter.drawImage(rect, image)
        if not painter.end():
            raise OSError(_('Cannot finish printing.'))
        if printer.printerState() in (QtPrintSupport.QPrinter.PrinterState.Error,
                                      QtPrintSupport.QPrinter.PrinterState.Aborted):
            raise OSError(_('Printing failed or was cancelled.'))
        return True
    finally:
        if painter.isActive():
            painter.end()


class PDFViewer(QtCore.QObject):
    request = QtCore.pyqtSignal(str, object)

    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self.tabs = []
        self.request.connect(self.on_request, QtCore.Qt.ConnectionType.QueuedConnection)
        app.ui.plot_tab_area.tab_closed_signal.connect(self.on_tab_closed)
        app_obj = getattr(app, 'app_obj', None)
        if app_obj is not None:
            app_obj.object_changed.connect(self.on_document_changed)

    def on_request(self, method, arguments):
        getattr(self, method)(*arguments)

    def on_gui_thread(self, method, arguments):
        if QtCore.QThread.currentThread() != self.thread():
            self.request.emit(method, arguments)
            return False
        return True

    def open_dialog(self):
        return self.app.f_handlers.on_file_open_document()

    def open_document(self, obj):
        data = obj.pdf_data
        name = obj.obj_options['name']
        for tab in self.tabs:
            if tab.owner is obj and tab.data == data:
                self.reattach(tab)
                area = self.app.ui.plot_tab_area
                tab.display_name = name
                area.setTabText(area.indexOf(tab), self.tab_label(name, tab))
                area.setCurrentWidget(tab)
                return tab
        return self.update_owned(obj, data, name)

    def on_document_changed(self, obj):
        if getattr(obj, 'document_type', None) == 'pdf' and any(tab.owner is obj for tab in self.tabs):
            self.open_document(obj)

    def open_file(self, path, display_name=None):
        if not self.on_gui_thread('open_file', (path, display_name)):
            return None
        try:
            return self.open_bytes(Path(path).read_bytes(), display_name or Path(path).name)
        except OSError as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Cannot open PDF'), error))

    def open_bytes(self, data, display_name, owner=None):
        data = bytes(data)
        if not self.on_gui_thread('open_bytes', (data, display_name, owner)):
            return None
        try:
            tab = PDFViewerUI(data, display_name, owner)
        except (ValueError, RuntimeError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            return None
        self.tabs.append(tab)
        area = self.app.ui.plot_tab_area
        area.addTab(tab, self.tab_label(display_name))
        area.setCurrentWidget(tab)
        tab.view.customContextMenuRequested.connect(lambda position: self.context_menu(tab, position))
        return tab

    def tab_label(self, name, current=None):
        area = self.app.ui.plot_tab_area
        names = {area.tabText(index) for index in range(area.count()) if area.widget(index) is not current}
        names.update(key.removeprefix('FlatCAM ') for key in area.detachedTabs)
        label, number = name, 2
        while label in names:
            label = '%s (%d)' % (name, number)
            number += 1
        return label

    def reattach(self, tab):
        area = self.app.ui.plot_tab_area
        if area.indexOf(tab) < 0:
            for window in list(area.detachedTabs.values()):
                if window.contentWidget is tab and window.isVisible():
                    window.close()
                    break

    def update_owned(self, owner, data, display_name):
        data = bytes(data)
        if not self.on_gui_thread('update_owned', (owner, data, display_name)):
            return None
        if owner is None:
            raise ValueError('An owned preview requires an owner')
        for tab in self.tabs:
            if tab.owner is owner:
                try:
                    tab.set_pdf(data, display_name)
                except (ValueError, RuntimeError) as error:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % error)
                    return None
                area = self.app.ui.plot_tab_area
                self.reattach(tab)
                area.setTabText(area.indexOf(tab), self.tab_label(display_name, tab))
                area.setCurrentWidget(tab)
                return tab
        return self.open_bytes(data, display_name, owner)

    def close_owned_tabs(self, owner):
        if not self.on_gui_thread('close_owned_tabs', (owner,)) or owner is None:
            return
        area = self.app.ui.plot_tab_area
        for tab in list(self.tabs):
            if tab.owner is owner:
                self.reattach(tab)
                index = area.indexOf(tab)
                if index >= 0:
                    area.closeTab(index)

    def on_tab_closed(self, name, index):
        tab = self.app.ui.plot_tab_area.widget(index)
        if tab in self.tabs:
            self.tabs.remove(tab)
            tab.release()
            # The signal precedes removeTab(); don't re-enter closeTab or delete the widget synchronously.
            tab.deleteLater()

    def context_menu(self, tab, position):
        menu = QtWidgets.QMenu(tab)
        menu.addAction(QtGui.QIcon(self.app.resource_location + '/save_as.png'),
                       _('Save...'), lambda: self.save(tab))
        menu.addAction(QtGui.QIcon(self.app.resource_location + '/printer32.png'),
                       _('Print...'), lambda: self.print_document(tab))
        menu.exec(tab.view.viewport().mapToGlobal(position))

    def save(self, tab):
        if getattr(tab.owner, 'kind', None) == 'document':
            return tab.owner.save_document()
        data = tab.data
        filename, _filter = FCFileSaveDialog.get_saved_filename(
            parent=tab, caption=_('Save PDF'),
            directory=str(Path(self.app.get_last_save_folder()) / Path(tab.display_name).name),
            ext_filter=_('PDF Files (*.pdf)'))
        if not filename or tab not in self.tabs:
            return False
        chosen = filename
        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'
        # the dialog already confirmed the name it returned; only a name changed by the extension needs a question
        if filename != chosen and Path(filename).exists() and QtWidgets.QMessageBox.question(
                tab, _('Overwrite file?'), _('The file already exists. Replace it?')) != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return False
        output = QtCore.QSaveFile(filename)
        if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or output.write(data) != len(data):
            output.cancelWriting()
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Cannot save PDF'), output.errorString()))
            return False
        if not output.commit():
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Cannot save PDF'), output.errorString()))
            return False
        self.app.file_saved.emit('PDF', filename)
        self.app.inform.emit('[success] %s: %s' % (_('PDF saved'), filename))
        return True

    def print_document(self, tab):
        choices = [_('Actual Size (100%)'), _('Fit to Page')]
        choice, accepted = QtWidgets.QInputDialog.getItem(
            tab, _('Print PDF'), _('Print scale'), choices, 0, False)
        if not accepted:
            return False
        mode = 'actual' if choice == choices[0] else 'fit'
        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.PrinterMode.HighResolution)
        dialog = QtPrintSupport.QPrintDialog(printer, tab)
        dialog.setWindowTitle(_('Print PDF') + ' - ' + tab.display_name)
        dialog.setMinMax(1, tab.document.pageCount())
        dialog.setOption(QtPrintSupport.QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
        dialog.setOption(QtPrintSupport.QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, True)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return False
        pages = selected_pages(tab.document.pageCount(), printer.pageRanges())
        if printer.printRange() == QtPrintSupport.QPrinter.PrintRange.CurrentPage:
            pages = [tab.page.value() - 1]
        if printer.pageOrder() == QtPrintSupport.QPrinter.PageOrder.LastPageFirst:
            pages.reverse()
        try:
            if any(page_layout(tab.document, printer, page, mode)[3] for page in pages):
                if QtWidgets.QMessageBox.warning(
                        tab, _('Print clipping'),
                        _('The PDF exceeds the paper or printable margins and will be clipped. '
                          'Actual Size will not shrink it. Continue?'),
                        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                        QtWidgets.QMessageBox.StandardButton.No) != QtWidgets.QMessageBox.StandardButton.Yes:
                    return False
            if not print_pages(tab.document, printer, mode, pages):
                return False
        except (ValueError, OSError, RuntimeError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            return False
        self.app.inform.emit('[success] %s: %s' % (_('Sent to printer'), tab.display_name))
        return True
