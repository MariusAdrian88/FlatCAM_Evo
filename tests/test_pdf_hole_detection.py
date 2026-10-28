#!/usr/bin/env python
"""Minimal regression tests for PDF import geometry and stream extraction."""

import os
import sys
import zlib
from io import BytesIO
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from shapely import Point, Polygon
from shapely.ops import unary_union

from appParsers.ParsePDF import PdfParser
from appParsers.PdfStreamParser import PurePythonPdfParser, PikePdfParser, PdfParserError

# ponytail: tiny synthetic inputs cover the import path without full-board scans.


def test_hole_detection_modes():
    assert PdfParser(units='MM', resolution=10, abort=False).hole_detection_mode == 'both'
    for mode in ('both', 'fill_only', 'stroke_only'):
        parser = PdfParser(units='MM', resolution=10, abort=False, hole_detection_mode=mode)
        assert parser.hole_detection_mode == mode
    with pytest.raises(ValueError, match='Invalid hole_detection_mode'):
        PdfParser(units='MM', resolution=10, abort=False, hole_detection_mode='invalid')


def _parse(stream, units='INCH'):
    return PdfParser(units=units, resolution=64, abort=False).parse_pdf(stream)


def _all_solids(data):
    geometries = [element['solid'] for layer_nr, layer in data.items() if layer_nr != 0
                  for aperture in layer.values() for element in aperture.get('geometry', [])
                  if 'solid' in element]
    return unary_union(geometries) if geometries else Polygon()


def _make_tool(units='MM'):
    from PyQt6 import QtWidgets

    global _QT_APP
    _QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    from appPlugins.ToolPdf.PdfImport import ToolPDF

    tool = ToolPDF.__new__(ToolPDF)
    rendered = []

    class Sink:
        def emit(self, *args):
            pass

    class Progress:
        def new(self, *args):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def new_object(kind, name, init, **kwargs):
        obj = SimpleNamespace(
            regenerate_source=lambda **kwargs: None,
            app=tool.app,
        )
        if kind == 'excellon':
            def add_tool(dia, drills, tool_id):
                obj.tools[tool_id] = {
                    'tooldia': dia,
                    'drills': drills,
                    'solid_geometry': [point.buffer(dia / 2) for point in drills],
                }

            obj.add_tool = add_tool
            obj.create_geometry = lambda: None

        init(obj, tool.app)
        rendered.append(obj)
        return obj

    tool.app = SimpleNamespace(
        app_obj=SimpleNamespace(new_object=new_object),
        proc_container=Progress(),
        file_opened=Sink(),
        inform=Sink(),
        app_units=units,
        log=SimpleNamespace(debug=lambda *args: None, error=lambda *args: None),
    )
    return tool, rendered


def test_parser_even_odd_and_nonzero_winding():
    for operator, area in (('f*', 300.0), ('f', 400.0)):
        data = _parse('0 0 20 20 re 5 5 10 10 re ' + operator)
        assert _all_solids(data).area * 72 ** 2 == pytest.approx(area, abs=1e-6)


def test_parser_skips_a_stray_brace():
    # a '{' outside a string made the tokenizer loop forever
    assert _all_solids(_parse('{ 0 0 10 10 re f }')).area * 72 ** 2 == pytest.approx(100.0)


def test_parser_nested_q_q_transforms():
    stream = ('q 2 0 0 1 0 0 cm '
              'q 1 0 0 1 10 0 cm 0 0 5 5 re f Q '
              '0 0 5 5 re f Q')
    solid = _all_solids(_parse(stream))
    assert tuple(value * 72 for value in solid.bounds) == \
        pytest.approx((0.0, 0.0, 30.0, 5.0), abs=1e-6)
    assert solid.area * 72 ** 2 == pytest.approx(100.0, abs=1e-6)


def test_alternating_colors_make_one_layer_per_color():
    # red pad (drawn twice, as some CAD PDFs do), blue pad, white hole in both,
    # red island in the hole (the same shape as before the hole: it must stay)
    stream = ('1 0 0 rg 0 0 20 20 re f 8 8 4 4 re f 0 0 20 20 re f '
              '0 0 1 rg 30 0 20 20 re f '
              '1 1 1 rg 5 5 40 10 re f '
              '1 0 0 rg 8 8 4 4 re f')
    data = _parse(stream)
    layers = [layer for layer_nr, layer in data.items() if layer_nr != 0]
    assert len(layers) == 2
    solids = [element for layer in layers for aperture in layer.values()
              for element in aperture['geometry'] if 'solid' in element]
    assert len(solids) == 4

    def area(layer):
        painted = Polygon()
        elements = sorted((element for aperture in layer.values()
                           for element in aperture['geometry']), key=lambda element: element['order'])
        for element in elements:
            painted = painted.union(element['solid']) if 'solid' in element \
                else painted.difference(element['clear'])
        return painted.area * 72 ** 2

    red = next(layer for layer in layers if _all_solids({1: layer}).bounds[0] == 0)
    blue = next(layer for layer in layers if layer is not red)
    assert area(red) == pytest.approx(400 - 15 * 10 + 16, abs=1e-6)
    assert area(blue) == pytest.approx(400 - 15 * 10, abs=1e-6)


def test_gerber_export_roundtrip_keeps_holes_and_islands():
    from appObjects.GerberObject import GerberObject
    from appParsers.ParseGerber import Gerber
    from test_gerber_parser import MockApp

    stream = ('0 1 0 rg 0 0 20 20 re f '
              '1 1 1 rg 5 5 10 10 re f '
              '0 1 0 rg 7 7 6 6 re f '
              '1 1 1 RG 2 w 0 18 m 20 18 l S')
    data = _parse(stream, units='MM')
    assert [layer for layer in data if layer != 0] == [1]
    tool, rendered = _make_tool()
    tool.layer_rendering_as_gerber('test.pdf', data[1], 1)
    expected = unary_union(rendered[-1].solid_geometry)

    body = GerberObject.export_gerber(rendered[-1], 4, 6)
    text = '%FSLAX46Y46*%\n%MOMM*%\nG01*\n%LPD*%\n' + body + 'M02*\n'
    parser = Gerber(MockApp())
    parser.parse_lines(text.splitlines())
    parsed = unary_union(parser.solid_geometry) if parser.solid_geometry else Polygon()

    assert parsed.symmetric_difference(expected).area == pytest.approx(0.0, abs=5e-3)
    assert parsed.covers(Point(1.0, 1.0))
    assert not parsed.covers(Point(2.0, 2.0))
    assert parsed.covers(Point(3.0, 3.0))
    assert not parsed.covers(Point(10 * 25.4 / 72, 18 * 25.4 / 72))


def test_excellon_filters_and_deduplicates_drills():
    tool, rendered = _make_tool()
    tool.layer_rendering_as_excellon('test.pdf', {0: {'geometry': [
        {'clear': Point(0, 0).buffer(5.0)},
        {'clear': Point(0.002, 0).buffer(0.5)},
        {'clear': Point(0.002, 0).buffer(0.5)},
        {'clear': Point(20, 0).buffer(2.5)},
        {'clear': Point(30, 0).buffer(1.0).envelope},
    ]}}, 0)
    tools = rendered[-1].tools
    counts = {round(spec['tooldia'], 3): len(spec['drills']) for spec in tools.values()}
    assert counts == {1.0: 1, 5.0: 1}
    small = next(spec for spec in tools.values() if spec['tooldia'] < 2)
    assert small['drills'][0].x == pytest.approx(0.002)


def test_empty_layers_make_no_object():
    # a gray fill that a later white fill covers fully, and no round hole
    data = _parse('0.9 0.9 0.9 rg 0 0 20 20 re f 1 1 1 rg 0 0 30 30 re f', units='MM')
    tool, rendered = _make_tool()
    tool.layer_rendering_as_gerber('test.pdf', data[1], 1)
    tool.layer_rendering_as_excellon('test.pdf', data[0], 0)
    assert rendered == []


def _make_pdf(contents_refs, stream_objects):
    parts = [b'%PDF-1.4\n']
    parts.append(b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n')
    parts.append(b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n')
    if len(contents_refs) > 1:
        contents = b'[' + b' '.join(b'%d 0 R' % number for number in contents_refs) + b']'
    else:
        contents = b'%d 0 R' % contents_refs[0]
    parts.append(b'3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents ' +
                 contents + b' >>\nendobj\n')
    for number, dictionary, data in stream_objects:
        parts.append(b'%d 0 obj\n<< ' % number + dictionary + b' /Length ' +
                     str(len(data)).encode() + b' >>\nstream\n' + data +
                     b'\nendstream\nendobj\n')
    parts.append(b'%%EOF\n')
    return b''.join(parts)


def test_first_page_contents_are_extracted_in_order(tmp_path):
    first = b'0 0 10 10 re f'
    second = b'100 100 10 10 re f\n(endobj endstream 5 0 obj) Tj'
    pdf = _make_pdf([5, 4], [
        (4, b'/Filter /FlateDecode', zlib.compress(first)),
        (5, b'', second),
        (6, b'', b'unreferenced stream'),
    ])
    path = tmp_path / 'ordered.pdf'
    path.write_bytes(pdf)
    assert PurePythonPdfParser().extract_content_streams(str(path)) == \
        (second + b'\n' + first).decode('ascii')


def _stream(dictionary, data):
    return b'<<' + dictionary + b'/Length %d>>\nstream\n' % len(data) + data + b'\nendstream'


def _xref_pdf(direct, in_stream=None):
    """A PDF 1.5 file with a cross-reference stream (pikepdf needs one); the root is object 1.

    direct: {number: body}; in_stream: {number: (object stream number, index)}.
    """
    in_stream = in_stream or {}
    pdf, offsets = b'%PDF-1.5\n', {}
    for number, body in sorted(direct.items()):
        offsets[number] = len(pdf)
        pdf += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref_number = max(list(direct) + list(in_stream)) + 1
    offsets[xref_number] = len(pdf)
    # W [1 4 2]: type 1 = offset in the file, type 2 = index in an object stream
    xref = b'\x00' + bytes(6)
    for number in range(1, xref_number + 1):
        if number in offsets:
            xref += b'\x01' + offsets[number].to_bytes(4, 'big') + bytes(2)
        elif number in in_stream:
            stream_number, index = in_stream[number]
            xref += b'\x02' + stream_number.to_bytes(4, 'big') + index.to_bytes(2, 'big')
        else:
            xref += b'\x00' + bytes(6)
    return pdf + b'%d 0 obj\n' % xref_number + _stream(
        b'/Type /XRef/Root 1 0 R/Size %d/W [1 4 2]' % (xref_number + 1), xref) + \
        b'\nendobj\nstartxref\n%d\n%%%%EOF\n' % offsets[xref_number]


def _square_font():
    """A TrueType font with 1 glyph (gid 1, char 'A'): a square of 400 units, advance 500."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def glyph(size):
        pen = TTGlyphPen(None)
        if size:
            pen.moveTo((0, 0))
            pen.lineTo((0, size))
            pen.lineTo((size, size))
            pen.lineTo((size, 0))
            pen.closePath()
        return pen.glyph()

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(['.notdef', 'A'])
    builder.setupCharacterMap({ord('A'): 'A'})
    builder.setupGlyf({'.notdef': glyph(0), 'A': glyph(400)})
    builder.setupHorizontalMetrics({'.notdef': (500, 0), 'A': (500, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': 'Square', 'styleName': 'Regular'})
    builder.setupOS2()
    builder.setupPost()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


@pytest.mark.parametrize('parser_class', [PurePythonPdfParser, PikePdfParser])
@pytest.mark.parametrize('font_type', ['Type0', 'TrueType', 'not embedded'])
def test_text_is_imported_as_glyph_outlines(tmp_path, parser_class, font_type):
    parser = parser_class()
    if not parser.is_available():
        pytest.skip('pikepdf is not installed')
    program = _square_font()
    descriptor = b'<</Type /FontDescriptor/FontName /Square/Flags 4/FontBBox [0 0 400 400]' + \
        (b'/FontFile2 7 0 R>>' if font_type != 'not embedded' else b'>>')
    if font_type == 'TrueType':
        font = b'<</Type /Font/Subtype /TrueType/BaseFont /Square/FirstChar 65/LastChar 65' \
               b'/Widths [500]/FontDescriptor 6 0 R>>'
        codes = b'(AA)'
    else:
        font = b'<</Type /Font/Subtype /Type0/BaseFont /Square/Encoding /Identity-H' \
               b'/DescendantFonts [<</Type /Font/Subtype /CIDFontType2/BaseFont /Square' \
               b'/CIDSystemInfo <</Registry (Adobe)/Ordering (Identity)/Supplement 0>>' \
               b'/FontDescriptor 6 0 R/CIDToGIDMap /Identity/W [1 [500]]>>]>>'
        codes = b'<00010001>'
    # 2 glyphs of 4 pt at 10 pt size, a second line invisible (Tr 3)
    page = b'BT /F1 10 Tf 1 0 0 1 20 30 Tm ' + codes + b' Tj 3 Tr 0 -20 Td ' + codes + b' Tj ET'
    pdf = _xref_pdf({
        1: b'<</Type /Catalog/Pages 2 0 R>>',
        2: b'<</Type /Pages/Count 1/Kids [3 0 R]>>',
        3: b'<</Type /Page/Parent 2 0 R/MediaBox [0 0 100 100]'
           b'/Resources <</Font <</F1 5 0 R>>>>/Contents 4 0 R>>',
        4: _stream(b'', page),
        5: font,
        6: descriptor,
        7: _stream(b'/Length1 %d' % len(program), program),
    })
    path = tmp_path / 'text.pdf'
    path.write_bytes(pdf)

    pdf_parser = PdfParser(units='INCH', resolution=64, abort=False)
    solid = _all_solids(pdf_parser.parse_pdf(parser.extract_content_streams(str(path))))

    if font_type == 'not embedded':
        assert solid.is_empty
        assert 'text' in pdf_parser.unsupported_features
    else:
        assert tuple(value * 72 for value in solid.bounds) == \
            pytest.approx((20.0, 30.0, 29.0, 34.0), abs=1e-6)
        assert solid.area * 72 ** 2 == pytest.approx(32.0, abs=1e-6)


@pytest.mark.parametrize('parser_class', [PurePythonPdfParser, PikePdfParser])
def test_object_stream_page_with_form_xobject_is_extracted(tmp_path, parser_class):
    parser = parser_class()
    if not parser.is_available():
        pytest.skip('pikepdf is not installed')
    # PDF 1.5 layout: the catalog, the page tree and the page live in a compressed
    # object stream, and the page draws its artwork through a Form XObject. The line
    # width of the trace comes only from an ExtGState (/LW), with no 'w' operator.
    bodies = [
        (1, b'<</Type /Catalog/Pages 2 0 R>>'),
        (2, b'<</Type /Pages/Count 1/Kids [3 0 R]>>'),
        (3, b'<</Type /Page/Parent 2 0 R/Resources 6 0 R/Contents 5 0 R>>'),
        (6, b'<</XObject <</P0 4 0 R>>/ExtGState <</G1 8 0 R>>>>'),
        (8, b'<</CA 1/LW 2/LC 0>>'),
    ]
    header, data = [], b''
    for number, body in bodies:
        header.append(b'%d %d' % (number, len(data)))
        data += body + b'\n'
    header = b' '.join(header) + b'\n'
    objstm = zlib.compress(header + data)
    form = zlib.compress(b'0 0 10 10 re f /G1 gs 0 20 m 10 20 l S')
    page = b'q /P0 Do Q'
    direct = {
        4: _stream(b'/Type /XObject/Subtype /Form/BBox [0 0 10 10]/Matrix [1 0 0 1 5 5]'
                   b'/Filter /FlateDecode', form),
        5: _stream(b'', page),
        7: _stream(b'/Type /ObjStm/N 5/First %d/Filter /FlateDecode' % len(header), objstm),
    }
    in_stream = {number: (7, index) for index, (number, _) in enumerate(bodies)}
    path = tmp_path / 'objstm.pdf'
    path.write_bytes(_xref_pdf(direct, in_stream))

    content = parser.extract_content_streams(str(path))

    # pad 5..15 x 5..15 and the 2 pt wide trace along y = 25
    solid = _all_solids(_parse(content))
    min_y, max_y = solid.bounds[1] * 72, solid.bounds[3] * 72
    assert (min_y, max_y) == pytest.approx((5.0, 26.0), abs=1e-6)


def test_unsupported_pdf_filter_is_reported(tmp_path):
    pdf = _make_pdf([4], [(4, b'/Filter /LZWDecode', b'not-lzw')])
    path = tmp_path / 'unsupported.pdf'
    path.write_bytes(pdf)
    with pytest.raises(PdfParserError, match='Unsupported PDF filter'):
        PurePythonPdfParser().extract_content_streams(str(path))


def test_encrypted_pdf_is_rejected(tmp_path):
    pdf = _make_pdf([4], [(4, b'', b'0 0 10 10 re\nf')])
    pdf = pdf.replace(b'%%EOF', b'trailer\n<< /Encrypt 9 0 R >>\n%%EOF')
    path = tmp_path / 'encrypted.pdf'
    path.write_bytes(pdf)
    with pytest.raises(PdfParserError, match='Encrypted'):
        PurePythonPdfParser().extract_content_streams(str(path))


def test_stop_cancels_a_long_pdf_parse(tmp_path):
    # the parser of the plugin kept the value of the abort flag at the plugin install (False): Stop could not cancel
    # a long parse
    from PyQt6 import QtWidgets
    from appCommon.Common import GracefulException
    from appPlugins.ToolPdf.PdfImport import ToolPDF
    from tests.helpers_objects import make_app_0b

    global _QT_APP
    _QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = make_app_0b()
    tool = ToolPDF(app)
    path = tmp_path / 'board.pdf'
    path.write_bytes(b'%PDF-1.4')

    def extract(filename, stop):
        app.abort_flag = stop     # the user presses Stop while the file is read
        return '0 0 10 10 re f\n' * 2000

    for stop in (True, False):
        app.abort_flag = False    # (the application resets it when it is idle again)
        tool.pdf_stream_parser = SimpleNamespace(name='test', is_available=lambda: True,
                                                 extract_content_streams=lambda filename: extract(filename, stop))
        if stop:
            with pytest.raises(GracefulException):
                tool.open_pdf(str(path))
        else:
            tool.open_pdf(str(path))
            assert _all_solids(tool.pdf_parsed['board.pdf']['pdf']).area == pytest.approx(100 * (25.4 / 72) ** 2)
        # the promise is gone: the rendering of the files (also of a next import) can start
        assert tool.parsing_promises == []


if __name__ == '__main__':
    sys.exit(pytest.main([__file__]))
