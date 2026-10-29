"""ParseFont: point-size scale, glyph topology, layout and font discovery."""

import math
from types import SimpleNamespace

import freetype
import pytest
from fontTools.ttLib.ttCollection import TTCollection

from appParsers.ParseFont import ParseFont


def make_parser():
    parser = ParseFont(SimpleNamespace(inform=SimpleNamespace(emit=lambda *args, **kwargs: None)))
    parser.get_fonts_by_types()
    assert parser.regular_f, 'A usable installed font is required for the real outline test.'
    family = 'Arial' if 'Arial' in parser.regular_f else next(iter(parser.regular_f))
    return parser, family, parser.regular_f[family]


def new_parser():
    return ParseFont(SimpleNamespace(inform=SimpleNamespace(emit=lambda *args, **kwargs: None)))


def parts_of(geometry):
    return list(geometry.geoms) if hasattr(geometry, 'geoms') else [geometry]


def raw_outline_units(path, char, size):
    face = freetype.Face(path)
    try:
        face.set_char_size(int(round(size * 64)))
        face.load_char(char)
        points = face.glyph.outline.points
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return max(xs) - min(xs), max(ys) - min(ys)
    finally:
        del face


def line_height_mm(path, size):
    face = freetype.Face(path)
    try:
        face.set_char_size(int(round(size * 64)))
        return face.size.height / 64.0 * 25.4 / 72.0
    finally:
        del face


def test_text_scale_uses_true_points_and_offsets():
    parser, family, path = make_parser()
    raw_width, raw_height = raw_outline_units(path, 'H', 12)
    mm = parser.font_to_geometry('H', family, 'regular', 12, 'MM', 0, 0)
    assert mm.bounds[2] - mm.bounds[0] == pytest.approx(raw_width / 64.0 * 25.4 / 72.0, rel=1e-6)
    assert mm.bounds[3] - mm.bounds[1] == pytest.approx(raw_height / 64.0 * 25.4 / 72.0, rel=1e-6)

    inch = parser.font_to_geometry('H', family, 'regular', 12, 'IN', 0, 0)
    assert inch.bounds[2] - inch.bounds[0] == pytest.approx(raw_width / 64.0 / 72.0, rel=1e-6)
    assert mm.area / inch.area == pytest.approx(25.4 ** 2)

    moved = parser.font_to_geometry('H', family, 'regular', 12, 'MM', 10, 20)
    assert moved.bounds[0] == pytest.approx(mm.bounds[0] + 10)
    assert moved.bounds[1] == pytest.approx(mm.bounds[1] + 20)
    assert moved.bounds[2] == pytest.approx(mm.bounds[2] + 10)
    assert moved.bounds[3] == pytest.approx(mm.bounds[3] + 20)


def test_multiline_resets_x_and_advances_one_font_line():
    parser, family, path = make_parser()
    advance = line_height_mm(path, 12)
    first_line = parser.font_to_geometry('HH', family, 'regular', 12, 'MM', 5, 0)
    second_line = parser.font_to_geometry('H', family, 'regular', 12, 'MM', 5, 0)
    multiline = parser.font_to_geometry('HH\nH', family, 'regular', 12, 'MM', 5, 0)
    assert multiline.bounds[0] == pytest.approx(first_line.bounds[0])
    assert multiline.bounds[2] == pytest.approx(first_line.bounds[2])
    assert multiline.bounds[1] == pytest.approx(-advance)
    assert multiline.bounds[3] == pytest.approx(first_line.bounds[3])
    assert multiline.area == pytest.approx(first_line.area + second_line.area)


def square(left, bottom, size):
    return [(left, bottom), (left, bottom + size), (left + size, bottom + size), (left + size, bottom)]


def build_font(family, advance, glyphs, kern=None, style='Regular'):
    """A minimal TrueType font; glyphs maps a one character name to its contours."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0

    order = ['.notdef'] + list(glyphs)
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(name): name for name in glyphs})
    built = {'.notdef': TTGlyphPen(None).glyph()}
    for name, contours in glyphs.items():
        pen = TTGlyphPen(None)
        for contour in contours:
            pen.moveTo(contour[0])
            for point in contour[1:]:
                pen.lineTo(point)
            pen.closePath()
        built[name] = pen.glyph()
    builder.setupGlyf(built)
    builder.setupHorizontalMetrics({name: (advance, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': family, 'styleName': style})
    builder.setupOS2()
    builder.setupPost()
    builder.setupMaxp()
    if kern:
        table = newTable('kern')
        table.version = 0
        subtable = KernTable_format_0()
        subtable.version = 0
        subtable.coverage = 1
        subtable.format = 0
        subtable.kernTable = kern
        table.kernTables = [subtable]
        builder.font['kern'] = table
    return builder.font


def build_cubic_font(directory):
    """A CFF font whose only glyph is a 600 unit circle drawn with four cubic curves."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    control = 300 * 0.5522847498
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(['.notdef', 'o'])
    builder.setupCharacterMap({ord('o'): 'o'})
    pen = T2CharStringPen(600, None)
    pen.moveTo((200, 500))
    pen.curveTo((200, 500 + control), (500 - control, 800), (500, 800))
    pen.curveTo((500 + control, 800), (800, 500 + control), (800, 500))
    pen.curveTo((800, 500 - control), (500 + control, 200), (500, 200))
    pen.curveTo((500 - control, 200), (200, 500 - control), (200, 500))
    pen.closePath()
    notdef = T2CharStringPen(600, None)
    notdef.moveTo((0, 0))
    notdef.lineTo((0, 700))
    notdef.lineTo((600, 700))
    notdef.lineTo((600, 0))
    notdef.closePath()
    builder.setupCFF('Cubic', {'FullName': 'Cubic'},
                     {'.notdef': notdef.getCharString(), 'o': pen.getCharString()}, {})
    builder.setupHorizontalMetrics({'.notdef': (600, 0), 'o': (600, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': 'CubicTest', 'styleName': 'Regular'})
    builder.setupOS2()
    builder.setupPost()
    path = directory / 'cubic.otf'
    builder.save(str(path))
    return path


def test_cubic_outlines_keep_their_true_size_and_area(tmp_path):
    build_cubic_font(tmp_path)
    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    geom = parser.font_to_geometry('o', 'CubicTest', 'regular', 100, 'MM')
    scale = (100 * 64 / 1000) * ParseFont.SCALE_MM
    assert geom.bounds[2] - geom.bounds[0] == pytest.approx(600 * scale, rel=1e-4)
    assert geom.bounds[3] - geom.bounds[1] == pytest.approx(600 * scale, rel=1e-4)
    assert geom.area == pytest.approx(math.pi * 300 ** 2 * scale ** 2, rel=1e-3)


def test_glyph_fill_rule_keeps_islands_and_merges_overlaps(tmp_path):
    build_font('FillRule', 600, {
        'n': [square(100, 100, 800), list(reversed(square(200, 200, 600))), square(300, 300, 400)],
        'p': [square(100, 100, 400), square(400, 400, 400)],
    }).save(str(tmp_path / 'fill.ttf'))
    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    scale = (100 * 64 / 1000) * ParseFont.SCALE_MM
    # glyph, parts, holes, unit_area: an island inside a hole is painted again; overlapping contours paint their union
    for glyph, parts, holes, unit_area in (('n', 2, 1, 440000), ('p', 1, 0, 310000)):
        geom = parser.font_to_geometry(glyph, 'FillRule', 'regular', 100, 'MM')
        outline = parts_of(geom)
        assert len(outline) == parts
        assert sum(len(part.interiors) for part in outline) == holes
        assert geom.area == pytest.approx(unit_area * scale ** 2, rel=1e-3)


def test_font_collections_use_the_indexed_face(tmp_path):
    from appParsers.ParsePcbDoc import PcbDocParser
    from appParsers.SVG.text import FontService

    first = build_font('CollFace', 600, {'o': [square(100, 100, 200)], 'H': [square(0, 0, 500)]})
    second = build_font('CollOther', 1000, {'o': [square(100, 100, 800)], 'H': [square(0, 0, 500)]},
                        kern={('o', 'o'): -200})
    collection = TTCollection()
    collection.fonts = [first, second]
    collection.save(str(tmp_path / 'pair.ttc'))

    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    entry = parser.regular_f['CollOther']
    assert isinstance(entry, str) and entry.face_index == 1

    scale = (100 * 64 / 1000) * ParseFont.SCALE_MM
    geom = parser.font_to_geometry('o', 'CollOther', 'regular', 100, 'MM')
    assert geom.bounds[2] - geom.bounds[0] == pytest.approx(800 * scale, rel=1e-3)

    # the SVG importer renders and measures the same face, kerning included
    outline, advance = FontService(SimpleNamespace(f_parse=parser)).render('oo', ('CollOther',), False, False)
    assert outline.bounds[2] - outline.bounds[0] == pytest.approx(1.6, rel=1e-3)
    assert advance == pytest.approx(1.8)

    # the PcbDoc importer reads the same face: 10mm cap height makes this 800 unit 'o' 16mm wide
    layout = PcbDocParser()
    layout._fonts[('CollOther', 'Regular')] = entry
    single = layout._font_geometry('o', 'CollOther', 'Regular', 10.0)
    assert single.bounds[2] - single.bounds[0] == pytest.approx(16.0, rel=1e-3)
    kerned = layout._font_geometry('oo', 'CollOther', 'Regular', 10.0)
    assert kerned.bounds[2] - kerned.bounds[0] == pytest.approx(32.0, rel=1e-3)


@pytest.fixture(scope='session')
def qt_fonts():
    """A real application so QTextLayout can shape; the UI always has one."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def build_shaped_font(directory, family, glyphs, advances, cmap, features=None):
    """A TrueType font with the given glyphs so shaping tests need no installed fonts."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString

    order = ['.notdef'] + list(glyphs)
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    built = {'.notdef': TTGlyphPen(None).glyph()}
    for name, contours in glyphs.items():
        pen = TTGlyphPen(None)
        for contour in contours:
            pen.moveTo(contour[0])
            for point in contour[1:]:
                pen.lineTo(point)
            pen.closePath()
        built[name] = pen.glyph()
    builder.setupGlyf(built)
    builder.setupHorizontalMetrics({name: (advances.get(name, 600), 0) for name in order})
    builder.setupHorizontalHeader(ascent=1000, descent=-200)
    builder.setupNameTable({'familyName': family, 'styleName': 'Regular'})
    builder.setupOS2()
    builder.setupPost()
    builder.setupMaxp()
    if features:
        addOpenTypeFeaturesFromString(builder.font, features)
    path = directory / (family.replace(' ', '_') + '.ttf')
    builder.save(str(path))
    return path


def bdf_row(bits):
    bits += '0' * ((8 - len(bits) % 8) % 8)
    return ''.join('%02X' % int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def bdf_glyph(name, code, advance, width, height, ox, oy, ink):
    rows = [bdf_row(''.join('1' if ink(x, y) else '0' for x in range(width))) for y in range(height)]
    return ('STARTCHAR %s\nENCODING %d\nSWIDTH 500 0\nDWIDTH %d 0\nBBX %d %d %d %d\nBITMAP\n%s\nENDCHAR\n'
            % (name, code, advance, width, height, ox, oy, '\n'.join(rows)))


def build_bitmap_font(directory, family='BitmapPixelCheck'):
    """A 16 pixel BDF strike: a ring with a counter, a diagonal and a tiny dot."""
    header = ('STARTFONT 2.1\n'
              'FONT -Test-Tiny-Medium-R-Normal--16-160-72-72-C-160-ISO10646-1\n'
              'SIZE 16 72 72\nFONTBOUNDINGBOX 16 16 0 0\n'
              'STARTPROPERTIES 5\nFONT_ASCENT 16\nFONT_DESCENT 0\n'
              'FAMILY_NAME "%s"\nWEIGHT_NAME "Medium"\nSLANT "R"\nENDPROPERTIES\n'
              'CHARS 3\n' % family)
    ring = bdf_glyph('O', 79, 16, 12, 12, 2, 2, lambda x, y: x in (0, 11) or y in (0, 11))
    triangle = bdf_glyph('A', 65, 16, 16, 16, 0, 0,
                         lambda x, y: 2 <= y <= 13 and 8 - (y - 2) // 2 <= x <= 8 + (y - 2) // 2)
    dot = bdf_glyph('period', 46, 4, 2, 2, 6, 6, lambda x, y: True)
    path = directory / 'bitmap.bdf'
    path.write_text(header + ring + triangle + dot + 'ENDFONT\n')
    return path


def test_shaped_arabic_uses_contextual_forms_and_matching_advance(tmp_path, qt_fonts):
    from appParsers.SVG.text import FontService

    build_shaped_font(
        tmp_path, 'ArabicShapedCheck',
        {'beh': [square(0, 0, 400)], 'beh.init': [square(0, 0, 300)], 'beh.fina': [square(0, 0, 250)]},
        {'beh': 600, 'beh.init': 500, 'beh.fina': 400}, {0x0628: 'beh'},
        'languagesystem DFLT dflt;\nlanguagesystem arab dflt;\n'
        'feature init { sub beh by beh.init; } init;\n'
        'feature fina { sub beh by beh.fina; } fina;\n')
    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    path = parser.regular_f['ArabicShapedCheck']

    geom = parser.font_to_geometry('\u0628\u0628', 'ArabicShapedCheck', 'regular', 100, 'MM')
    unit = 64 * ParseFont.SCALE_MM
    parts = sorted(parts_of(geom), key=lambda part: part.bounds[0])
    assert len(parts) == 2
    # contextual forms are 250 and 300 units; isolated letters would both be 400
    assert [round((part.bounds[2] - part.bounds[0]) / unit) for part in parts] == [25, 30]

    assert parser.text_advance('\u0628\u0628', path, 100) == pytest.approx(90)
    assert parser.text_advance('\u0628', path, 100) == pytest.approx(60)

    # the SVG importer measures the run with the same shaped metrics it renders with
    outline, advance = FontService(SimpleNamespace(f_parse=parser)).render(
        '\u0628\u0628', ('ArabicShapedCheck',), False, False)
    assert advance == pytest.approx(0.9, rel=1e-3)
    assert outline.bounds[2] - outline.bounds[0] == pytest.approx(0.7, rel=1e-3)


def test_bidi_run_is_placed_in_visual_order(tmp_path, qt_fonts):
    build_shaped_font(
        tmp_path, 'BidiShapedCheck',
        {'A': [square(0, 0, 300)], 'alef': [square(0, 1000, 100)], 'bet': [square(0, 2000, 100)]},
        {'A': 600, 'alef': 300, 'bet': 350},
        {ord('A'): 'A', 0x05D0: 'alef', 0x05D1: 'bet'})
    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    path = parser.regular_f['BidiShapedCheck']

    geom = parser.font_to_geometry('A\u05d0\u05d1', 'BidiShapedCheck', 'regular', 100, 'MM')
    parts = parts_of(geom)
    assert len(parts) == 3
    # the glyphs sit at different heights, so each part is identifiable
    corner, alef, bet = sorted(parts, key=lambda part: part.bounds[1])
    assert corner.bounds[0] < bet.bounds[0] < alef.bounds[0]
    assert parser.text_advance('A\u05d0\u05d1', path, 100) == pytest.approx(125)


def test_bitmap_font_geometry_uses_pixels_and_smoothing(tmp_path):
    build_bitmap_font(tmp_path)
    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    unit = 64 * ParseFont.SCALE_MM

    exact = parser.font_to_geometry('O', 'BitmapPixelCheck', 'regular', 16, 'MM', 0, 0,
                                    bitmap_smoothing=0)
    assert len(parts_of(exact)) == 1
    assert len(parts_of(exact)[0].interiors) == 1
    assert exact.area == pytest.approx(44 * unit ** 2, rel=1e-6)
    assert exact.bounds == pytest.approx((2 * unit, 2 * unit, 14 * unit, 14 * unit))

    dot = parser.font_to_geometry('.', 'BitmapPixelCheck', 'regular', 16, 'MM', 0, 0,
                                  bitmap_smoothing=0)
    assert dot.area == pytest.approx(4 * unit ** 2, rel=1e-6)

    smooth = parser.font_to_geometry('O', 'BitmapPixelCheck', 'regular', 16, 'MM', 0, 0,
                                     bitmap_smoothing=0.5)
    assert len(parts_of(smooth)[0].interiors) == 1
    assert smooth.area == pytest.approx(44 * unit ** 2, rel=0.05)

    triangle = parser.font_to_geometry('A', 'BitmapPixelCheck', 'regular', 16, 'MM', 0, 0,
                                       bitmap_smoothing=0)
    smoothed = parser.font_to_geometry('A', 'BitmapPixelCheck', 'regular', 16, 'MM', 0, 0,
                                       bitmap_smoothing=0.5)
    assert triangle.area == pytest.approx(72 * unit ** 2, rel=1e-6)
    assert not math.isclose(triangle.area, smoothed.area, rel_tol=1e-6)
    assert abs(smoothed.area - triangle.area) / triangle.area < 0.1
    assert len(smoothed.exterior.coords) < len(triangle.exterior.coords)

    inches = parser.font_to_geometry('O', 'BitmapPixelCheck', 'regular', 16, 'IN', 0, 0,
                                     bitmap_smoothing=0)
    assert exact.area / inches.area == pytest.approx(25.4 ** 2)
    moved = parser.font_to_geometry('O', 'BitmapPixelCheck', 'regular', 16, 'MM', 10, 20,
                                    bitmap_smoothing=0)
    assert moved.bounds[0] == pytest.approx(exact.bounds[0] + 10)
    assert moved.bounds[1] == pytest.approx(exact.bounds[1] + 20)


def test_gpos_mark_is_placed_above_the_base(tmp_path, qt_fonts):
    anchor_x = 900   # the mark overhangs its base
    build_shaped_font(
        tmp_path, 'MarkPositionCheck%s' % anchor_x,
        {'A': [square(0, 0, 400)], 'acute': [square(0, 0, 100)]},
        {'A': 600, 'acute': 0}, {ord('A'): 'A', 0x0301: 'acute'},
        'languagesystem DFLT dflt;\nlanguagesystem latn dflt;\n'
        'markClass acute <anchor 0 0> @TOP_MARKS;\n'
        'feature mark { pos base A <anchor %s 600> mark @TOP_MARKS; } mark;\n' % anchor_x)
    parser = new_parser()
    parser.get_fonts_by_types([str(tmp_path)])
    family = 'MarkPositionCheck%s' % anchor_x
    path = parser.regular_f[family]
    unit = 64 * ParseFont.SCALE_MM

    geom = parser.font_to_geometry('A\u0301', family, 'regular', 100, 'MM')
    parts = parts_of(geom)
    assert len(parts) == 2
    base, mark = sorted(parts, key=lambda part: part.bounds[1])
    assert base.bounds == pytest.approx((0, 0, 40 * unit, 40 * unit))
    # An overhanging mark must not change the logical advance of its base.
    assert mark.bounds[0] == pytest.approx(anchor_x / 10 * unit, rel=1e-3)
    assert mark.bounds[1] == pytest.approx(60 * unit, rel=1e-3)
    assert mark.bounds[3] == pytest.approx(70 * unit, rel=1e-3)
    assert parser.text_advance('A\u0301', path, 100) == pytest.approx(60)

    from appParsers.SVG.text import FontService
    _, advance = FontService(SimpleNamespace(f_parse=parser)).render('A\u0301', (family,), False, False)
    assert advance == pytest.approx(0.6)

    inches = parser.font_to_geometry('A\u0301', family, 'regular', 100, 'IN')
    assert geom.area / inches.area == pytest.approx(25.4 ** 2)
    moved = parser.font_to_geometry('A\u0301', family, 'regular', 100, 'MM', 10, 20)
    assert moved.bounds[0] == pytest.approx(geom.bounds[0] + 10)
    assert moved.bounds[1] == pytest.approx(geom.bounds[1] + 20)

