from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PyQt6 import QtCore, QtWidgets, QtGui, QtTest, QtPdfWidgets
from reportlab.pdfgen.canvas import Canvas

from appGUI.GUIElements import FCDetachableTab2


def pdf_bytes(rotation=0):
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(144, 216), invariant=1)
    canvas.setPageRotation(rotation)
    canvas.setFillColorRGB(1, 0, 0)
    canvas.rect(0, 0, 144, 216, fill=1, stroke=0)
    canvas.setFillColorRGB(0, 0, 1)
    canvas.rect(19, 37, 71, 97, fill=1, stroke=0)
    canvas.setLineWidth(0.73)
    canvas.line(0, 0, 144, 216)
    canvas.showPage()
    canvas.save()
    return stream.getvalue()


@pytest.fixture(scope='module')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def viewer_app(tmp_path, application):

    class App(QtCore.QObject):
        inform = QtCore.pyqtSignal(str)
        file_saved = QtCore.pyqtSignal(str, str)

        def get_last_save_folder(self):
            return str(tmp_path)

        get_last_folder = get_last_save_folder

    app = App()
    app.resource_location = 'assets/resources'
    app.ui = SimpleNamespace(plot_tab_area=FCDetachableTab2())
    app.messages = []
    app.inform.connect(app.messages.append)
    yield app
    app.ui.plot_tab_area.deleteLater()
    application.processEvents()


def pixels(image):
    return np.frombuffer(image.constBits().asstring(image.sizeInBytes()), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine() // 4, 4)[:, :image.width()].copy()


def test_ctrl_wheel_zooms_from_fit_and_keeps_toolbar_synchronized(viewer_app, application):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(), 'zoom.pdf')
    tab.resize(800, 600)
    tab.show()
    application.processEvents()
    viewport = tab.view.viewport()
    position = QtCore.QPointF(viewport.rect().center())
    wheel = QtGui.QWheelEvent(
        position, QtCore.QPointF(viewport.mapToGlobal(position.toPoint())),
        QtCore.QPoint(), QtCore.QPoint(0, 120), QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.ControlModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(viewport, wheel)
    assert tab.view.zoomMode() == QtPdfWidgets.QPdfView.ZoomMode.Custom
    assert tab.zoom.value() == pytest.approx(tab.view.zoomFactor() * 100, abs=0.01)
    assert tab.zoom_slider.value() == round(tab.zoom.value())
    previous = tab.view.zoomFactor()
    QtWidgets.QApplication.sendEvent(viewport, wheel)
    assert tab.view.zoomFactor() == pytest.approx(previous * 1.1)
    assert tab.zoom.value() == pytest.approx(tab.view.zoomFactor() * 100, abs=0.01)
    tab.zoom_slider.setValue(175)
    assert tab.view.zoomFactor() == pytest.approx(1.75)
    assert tab.zoom.value() == 175
    tab.zoom.setValue(100)
    assert tab.view.zoomFactor() == 1
    assert tab.zoom_slider.value() == 100
    tab.view.setFocus()
    QtTest.QTest.keyClick(tab.view, QtCore.Qt.Key.Key_Plus, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert tab.view.zoomFactor() == pytest.approx(1.1)
    assert tab.zoom.value() == 110
    for button in (tab.fit_page, tab.fit_width):
        button.click()
        application.processEvents()
        fitted_percent = tab.zoom.value()
        QtWidgets.QApplication.sendEvent(viewport, wheel)
        assert tab.view.zoomMode() == QtPdfWidgets.QPdfView.ZoomMode.Custom
        assert tab.view.zoomFactor() * 100 == pytest.approx(min(1000, fitted_percent * 1.1), abs=0.01)
        assert tab.zoom.value() == pytest.approx(tab.view.zoomFactor() * 100, abs=0.01)
        assert tab.zoom_slider.value() == round(tab.zoom.value())
    QtTest.QTest.keyClick(tab.view, QtCore.Qt.Key.Key_0, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert tab.view.zoomFactor() == 1
    assert tab.zoom.value() == tab.zoom_slider.value() == 100
    QtTest.QTest.keyClick(tab.view, QtCore.Qt.Key.Key_Minus, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert tab.view.zoomFactor() == pytest.approx(1 / 1.1)
    assert tab.zoom.value() == pytest.approx(100 / 1.1, abs=0.01)
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_ctrl_wheel_keeps_artwork_under_the_mouse_pointer(viewer_app, application):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(600, 600), invariant=1)
    canvas.setFillColorRGB(0, 0, 1)
    canvas.rect(280, 280, 40, 40, fill=1, stroke=0)
    canvas.showPage()
    canvas.save()
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(stream.getvalue(), 'anchor.pdf')
    area = viewer_app.ui.plot_tab_area
    area.resize(900, 600)
    area.show()
    tab.zoom.setValue(200)
    application.processEvents()
    for bar in (tab.view.horizontalScrollBar(), tab.view.verticalScrollBar()):
        bar.setValue(bar.maximum() // 2)

    def blue_bounds():
        for attempt in range(100):
            QtTest.QTest.qWait(10)
            image = tab.view.viewport().grab().toImage().convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            y, x = np.where(np.all(pixels(image)[:, :, :3] == (0, 0, 255), axis=2))
            if len(x):
                return np.array([x.min(), y.min(), x.max(), y.max()]) / image.devicePixelRatio()
        pytest.fail('The PDF artwork was not rendered')

    before = blue_bounds()
    position = QtCore.QPointF(float(before[0] + 20), float(before[1] + 30))
    for delta, scale in ((120, 1.1), (-120, 1 / 1.1)):
        event = QtGui.QWheelEvent(
            position, QtCore.QPointF(tab.view.viewport().mapToGlobal(position.toPoint())),
            QtCore.QPoint(), QtCore.QPoint(0, delta), QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.ControlModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(tab.view.viewport(), event)
        after = blue_bounds()
        anchor = np.array([position.x(), position.y(), position.x(), position.y()])
        assert after == pytest.approx(anchor + (before - anchor) * scale, abs=2)
        assert tab.zoom.value() == pytest.approx(tab.view.zoomFactor() * 100, abs=0.01)
        before = after
    area.closeTab(0)


def test_right_drag_pans_without_menu_but_right_click_opens_it(viewer_app, application):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(), 'pan.pdf')
    tab.resize(800, 600)
    tab.show()
    tab.zoom.setValue(1000)
    application.processEvents()
    bar = tab.view.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 2)
    before = bar.value()
    viewport = tab.view.viewport()
    start = viewport.rect().center()
    end = start + QtCore.QPoint(0, 50)
    with patch.object(QtWidgets.QMenu, 'exec', return_value=None) as menu:
        QtTest.QTest.mousePress(viewport, QtCore.Qt.MouseButton.RightButton, pos=start)
        context_event = QtGui.QContextMenuEvent(
            QtGui.QContextMenuEvent.Reason.Mouse, start, viewport.mapToGlobal(start))
        QtWidgets.QApplication.sendEvent(viewport, context_event)
        assert not menu.called
        QtTest.QTest.mouseMove(viewport, end)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.MouseButton.RightButton, pos=end)
        assert bar.value() == before - 50
        QtWidgets.QApplication.sendEvent(viewport, context_event)
        assert not menu.called
        QtTest.QTest.mouseClick(viewport, QtCore.Qt.MouseButton.RightButton, pos=start)
        assert menu.call_count == 1
    before = bar.value()
    QtWidgets.QApplication.sendEvent(viewport, QtGui.QWheelEvent(
        QtCore.QPointF(start), QtCore.QPointF(viewport.mapToGlobal(start)), QtCore.QPoint(),
        QtCore.QPoint(0, -120), QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False))
    assert bar.value() > before
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_pdf_menu_actions_have_icons(viewer_app):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(), 'menu.pdf')
    menus = []
    with patch.object(QtWidgets.QMenu, 'exec', lambda menu, position: menus.append(menu)):
        viewer.context_menu(tab, QtCore.QPoint())
        menu = menus[0]
        actions = [action for action in menu.actions() if not action.isSeparator()]
        assert actions
        assert all(not action.icon().isNull() for action in actions)
    viewer_app.ui.plot_tab_area.closeTab(0)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_regional_bands_preserve_full_page_scale_and_edges(viewer_app, rotation):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer, render_bands
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(rotation), 'board.pdf')
    size = tab.document.pagePointSize(0)
    target = QtCore.QSize(301, round(301 * size.height() / size.width()))
    reference = pixels(tab.document.render(0, target))
    actual = np.empty_like(reference)
    for y, image in render_bands(tab.document, 0, target, max_bytes=301 * 4 * 67):
        assert image.sizeInBytes() <= 301 * 4 * 67
        actual[y:y + image.height()] = pixels(image)
    # PDFium's regional rasterizer rounds antialiasing differently, not positions or coverage.
    assert np.abs(actual.astype(int) - reference.astype(int)).max() <= 2
    viewer.close_owned_tabs(None)
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_owned_tabs_use_identity_and_invalid_update_keeps_previous_document(viewer_app):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    viewer = PDFViewer(viewer_app)
    owner = []
    equal_but_independent_owner = []
    independent = viewer.open_bytes(pdf_bytes(), 'independent.pdf')
    first = viewer.open_bytes(pdf_bytes(), 'film.pdf', owner)
    other = viewer.open_bytes(pdf_bytes(), 'other.pdf', equal_but_independent_owner)
    assert viewer.update_owned(owner, b'not a PDF', 'broken.pdf') is None
    assert first.document.pageCount() == 1
    assert first.data == pdf_bytes()
    assert viewer.update_owned(owner, pdf_bytes(180), 'regenerated.pdf') is first
    assert viewer_app.ui.plot_tab_area.count() == 3
    viewer.close_owned_tabs(owner)
    assert viewer_app.ui.plot_tab_area.count() == 2
    assert independent.document.pageCount() == other.document.pageCount() == 1
    viewer_app.ui.plot_tab_area.closeTab(0)
    assert viewer_app.ui.plot_tab_area.count() == 1
    assert other.document.pageCount() == 1
    viewer.close_owned_tabs(equal_but_independent_owner)


def test_detached_owned_preview_closes_without_touching_same_named_independent_pdf(viewer_app):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    viewer = PDFViewer(viewer_app)
    independent = viewer.open_bytes(pdf_bytes(), 'same.pdf')
    owner = object()
    owned = viewer.open_bytes(pdf_bytes(), 'same.pdf', owner)
    area = viewer_app.ui.plot_tab_area
    area.detachTab(area.indexOf(owned), QtCore.QPoint(0, 0))
    window = owned.window()
    viewer.close_owned_tabs(owner)
    assert not window.isVisible()
    assert area.count() == 1
    assert independent.document.pageCount() == 1
    area.closeTab(0)


def test_file_loading_retains_bytes_after_original_is_removed(viewer_app, tmp_path):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    path = tmp_path / 'source.pdf'
    original = pdf_bytes()
    path.write_bytes(original)
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_file(str(path))
    path.unlink()
    assert tab.data == original
    assert not tab.document.render(0, QtCore.QSize(100, 150)).isNull()
    destination = tmp_path / 'saved.pdf'
    with patch('appPlugins.ToolPDFViewer.PDFViewer.FCFileSaveDialog.get_saved_filename',
               return_value=(str(destination), 'PDF Files (*.pdf)')):
        assert viewer.save(tab)
    assert destination.read_bytes() == original
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_print_scale_is_explicit_and_actual_size_never_shrinks(viewer_app):
    from appPlugins.ToolPDFViewer.PDFViewer import print_target, selected_pages
    paper = QtCore.QRectF(0, 0, 600, 800)
    printable = QtCore.QRectF(20, 20, 560, 760)
    actual, clipped = print_target(QtCore.QSizeF(144, 216), 300, paper, printable, 'actual')
    assert actual.width() == pytest.approx(600)
    assert actual.height() == pytest.approx(900)
    assert clipped
    fitted, clipped = print_target(QtCore.QSizeF(144, 216), 300, paper, printable, 'fit')
    assert fitted.width() / fitted.height() == pytest.approx(144 / 216)
    assert printable.contains(fitted)
    assert not clipped
    ranges = QtGui.QPageRanges()
    ranges.addRange(2, 3)
    ranges.addPage(5)
    assert selected_pages(6, ranges) == [1, 2, 4]
    assert selected_pages(2, QtGui.QPageRanges()) == [0, 1]


def test_large_page_renders_only_bounded_bands(viewer_app):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer, render_bands, MAX_BAND_BYTES
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(), 'large.pdf')
    bands = render_bands(tab.document, 0, QtCore.QSize(40000, 80000))
    y, first = next(bands)
    assert y == 0 and first.width() == 40000
    assert first.sizeInBytes() <= MAX_BAND_BYTES
    y, second = next(bands)
    assert y == first.height()
    assert second.sizeInBytes() <= MAX_BAND_BYTES
    assert not second.isNull()
    bands.close()
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_save_cancel_and_failure_leave_retained_pdf_retryable(viewer_app, tmp_path):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(), 'board.pdf')
    with patch('appPlugins.ToolPDFViewer.PDFViewer.FCFileSaveDialog.get_saved_filename',
               return_value=('', 'PDF Files (*.pdf)')):
        assert not viewer.save(tab)
    destination = tmp_path / 'missing' / 'board.pdf'
    with patch('appPlugins.ToolPDFViewer.PDFViewer.FCFileSaveDialog.get_saved_filename',
               return_value=(str(destination), 'PDF Files (*.pdf)')):
        assert not viewer.save(tab)
    assert tab.data == pdf_bytes()
    assert tab.document.pageCount() == 1
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_print_to_pdf_keeps_physical_page_size_and_reports_cancel(viewer_app, tmp_path):
    from PyQt6.QtPrintSupport import QPrinter
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer, print_pages
    viewer = PDFViewer(viewer_app)
    tab = viewer.open_bytes(pdf_bytes(), 'board.pdf')
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / 'printed.pdf'))
    printer.setPageSize(QtGui.QPageSize(QtCore.QSizeF(144, 216), QtGui.QPageSize.Unit.Point))
    assert print_pages(tab.document, printer, 'actual', [0])
    printed = viewer.open_file(str(tmp_path / 'printed.pdf'))
    points = printed.document.pagePointSize(0)
    assert points.width() == pytest.approx(144, abs=1)
    assert points.height() == pytest.approx(216, abs=1)
    viewer_app.messages.clear()
    with patch('appPlugins.ToolPDFViewer.PDFViewer.QtWidgets.QInputDialog.getItem', return_value=('', False)):
        assert not viewer.print_document(tab)
    assert not any('[success]' in message for message in viewer_app.messages)
    viewer_app.ui.plot_tab_area.closeTab(1)
    viewer_app.ui.plot_tab_area.closeTab(0)


def test_png_preview_keeps_bytes_dpi_and_dpr_aware_physical_scale(viewer_app):
    from appGUI.ImagePreview import ImagePreview, physical_zoom
    image = QtGui.QImage(200, 100, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtCore.Qt.GlobalColor.blue)
    image.setDotsPerMeterX(round(192 / 0.0254))
    image.setDotsPerMeterY(round(192 / 0.0254))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    data = bytes(buffer.data())
    preview = ImagePreview(data, 'film.png', 192)
    assert preview.data == data
    assert preview.pixel_size == QtCore.QSize(200, 100)
    assert preview.dpi == 192
    assert physical_zoom(192, 192, 2) == 0.5
    previous = preview.data
    with pytest.raises(ValueError):
        preview.set_image(b'invalid PNG', 'bad.png', 192)
    assert preview.data == previous
    preview.deleteLater()


def test_release_disconnects_without_qt_nullptr_warning_and_keeps_other_tabs_alive(viewer_app, application):
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    warnings = []

    def handler(msg_type, context, message):
        warnings.append(message)

    QtCore.qInstallMessageHandler(handler)
    try:
        viewer = PDFViewer(viewer_app)
        area = viewer_app.ui.plot_tab_area
        first = viewer.open_bytes(pdf_bytes(), 'first.pdf')
        second = viewer.open_bytes(pdf_bytes(90), 'second.pdf')
        assert area.count() == 2
        assert first.document.pageCount() == 1
        assert second.document.pageCount() == 1
        # Close first tab through the full lifecycle: closeTab → on_tab_closed → release()
        area.closeTab(0)
        application.processEvents()
        assert area.count() == 1
        # Second tab still renders after first is closed
        assert second.document.pageCount() == 1
        assert not second.document.render(0, QtCore.QSize(80, 120)).isNull()
        # Close second tab through the full lifecycle
        area.closeTab(0)
        application.processEvents()
        assert area.count() == 0
        # No Qt nullptr warnings were emitted during the close lifecycle
        assert not any('nullptr' in w for w in warnings)
        assert not any('invalid nullptr' in w for w in warnings)
    finally:
        QtCore.qInstallMessageHandler(None)
