"""Runtime check of the PDF and Film preview stack, run by the release check inside the frozen build."""


def pdf_runtime_smoke(application):
    """Run inside the application runtime, after pruning; no native dialog or hardware claim."""
    from io import BytesIO
    from PyQt6 import QtCore, QtPdf, QtPdfWidgets, QtPrintSupport, QtSvg, sip
    from reportlab.pdfgen.canvas import Canvas

    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(144, 72))
    canvas.setFillColorRGB(1, 0, 0)
    canvas.rect(0, 0, 144, 72, fill=1, stroke=0)
    canvas.save()
    document = QtPdf.QPdfDocument(application)
    buffer = QtCore.QBuffer(document)
    buffer.setData(stream.getvalue())
    if not buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError('Frozen PDF buffer could not be opened')
    document.load(buffer)
    if document.status() != QtPdf.QPdfDocument.Status.Ready or document.pageCount() != 1:
        raise RuntimeError('Frozen PDF document could not be loaded')
    image = document.render(0, QtCore.QSize(144, 72))
    if image.isNull() or image.pixelColor(72, 36).name() != '#ff0000':
        raise RuntimeError('Frozen PDF page did not render the expected pixels')
    view = QtPdfWidgets.QPdfView(None)
    view.setDocument(document)
    printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.PrinterMode.HighResolution)
    dialog = QtPrintSupport.QPrintDialog(printer, None)
    renderer = QtSvg.QSvgRenderer(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    if not renderer.isValid():
        raise RuntimeError('Frozen QtSvg runtime is unavailable')
    sip.delete(dialog)
    sip.delete(view)
    document.close()
    sip.delete(document)
    print('FLATCAM_PDF_RUNTIME_SMOKE_OK', flush=True)
    from shapely import box
    from appPlugins.ToolFilm import FilmGen
    from appPlugins.ToolPDFViewer.PDFViewerUI import PDFViewerUI
    from appGUI.ImagePreview import ImagePreview

    svg = FilmGen.create_positive_svg(FilmGen.create_svg_geometry(box(0, 0, 20, 10), 0.01),
                                     (0, 0, 20, 10), 0, '#ff0000', 1, 'mm')
    pdf = FilmGen.render_film(svg, 'pdf', None, 'MM', (0, 0, 20, 10))
    preview = PDFViewerUI(pdf, 'smoke.pdf')
    if preview.document.pageCount() != 1:
        raise RuntimeError('Frozen Film PDF preview failed')
    image = preview.document.render(0, QtCore.QSize(100, 50))
    if image.isNull() or image.pixelColor(50, 25).name() != '#ff0000':
        raise RuntimeError('Frozen Film PDF preview pixels are incorrect')
    sip.delete(preview)
    png = FilmGen.render_film(svg, 'png', None, 'MM', (0, 0, 20, 10), 96)
    preview = ImagePreview(png, 'smoke.png', 96)
    if preview.pixel_size != QtCore.QSize(76, 38) or preview.data != png:
        raise RuntimeError('Frozen Film PNG preview failed')
    sip.delete(preview)
    print('FLATCAM_FILM_PREVIEW_SMOKE_OK', flush=True)
