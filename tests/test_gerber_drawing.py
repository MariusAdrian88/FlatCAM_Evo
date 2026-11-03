"""Numeric Gerber drawing creates explicit material, not guessed aperture sweeps."""


import pytest
from PyQt6 import QtWidgets
from shapely.affinity import rotate, translate
from shapely.geometry import box, MultiPolygon
from shapely.ops import unary_union

from appEditors.grb_plugins.GrbDrawingPlugin import rectangle_region, text_region
from appParsers.ParseFont import ParseFont
from test_gerber_editor_clear import editor_save
from test_gerber_editor_pick import make_editor, aperture, pad
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, _headless_qt, make_gerber, NATIVE_GBR, reparse_gerber,
)


@pytest.mark.parametrize('values', [(0, 0, 0, 2, 'c', 's', 0, 0, 16),
                                   (0, 0, 4, 2, 'c', 'r', 2, 0, 16)])
def test_rectangle_rejects_invalid_input(values):
    with pytest.raises(ValueError):
        rectangle_region(*values)


def font_parser(editor):
    parser = ParseFont(editor.app)
    parser.get_fonts_by_types()
    assert parser.regular_f, 'A usable installed font is required for the real outline test.'
    return parser, 'Arial' if 'Arial' in parser.regular_f else next(iter(parser.regular_f))


def test_text_holes_units_and_transform():
    editor = make_editor()
    parser, family = font_parser(editor)
    mm = text_region(parser, 'Oi', family, 'regular', 12, 'MM', 0, 0, 0)
    inch = text_region(parser, 'Oi', family, 'regular', 12, 'IN', 0, 0, 0)
    assert mm.is_valid and not mm.is_empty
    parts = list(mm.geoms) if isinstance(mm, MultiPolygon) else [mm]
    assert sum(len(p.interiors) for p in parts) >= 1
    assert len(parts) >= 2
    assert mm.area / inch.area == pytest.approx(25.4 ** 2)
    moved = text_region(parser, 'Oi', family, 'regular', 12, 'MM', 10, 20, 90)
    assert moved.equals_exact(translate(rotate(mm, 90, origin=(0, 0)), 10, 20), 1e-10)
    for text, size, x in [('', 12, 0), ('O', 0, 0), ('O', float('nan'), 0), ('O', 12, float('inf'))]:
        with pytest.raises(ValueError):
            text_region(parser, text, family, 'regular', size, 'MM', x, 0, 0)


def test_region_commit_preserves_owners_and_undo():
    existing = pad(20, 20, 1)
    editor = make_editor({10: aperture([existing])})
    polygon = box(0, 0, 5, 5).difference(box(1, 1, 4, 4))
    added = editor.add_region_geometry(MultiPolygon([polygon, box(2, 2, 3, 3)]))
    assert len(added) == 2
    assert editor.storage_dict[10]['geometry'] == [existing]
    assert editor.storage_dict[editor.last_aperture_selected]['type'] == 'REG'
    assert editor.loaded_as_regions
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict) == [10]
    assert editor.redo()
    assert unary_union([s.geo['solid'] for s in editor.selected]).equals(unary_union([polygon, box(2, 2, 3, 3)]))


def skip_without_fonts():
    # the offscreen Qt platform has an empty font database: the font combo box cannot select a family
    if QtWidgets.QFontComboBox().count() == 0:
        pytest.skip('no fonts in the Qt font database (offscreen platform)')


def test_copper_pour_clearance_parts_and_history():
    from appEditors.grb_plugins.GrbDrawingPlugin import copper_pour
    from appEditors.grb_plugins.GrbCommon import DrawToolShape
    obstacles = [box(4, -1, 5, 11), box(1, 1, 2, 2), box(-1, 3, -0.1, 4), box(50, 50, 51, 51)]
    shapes = [DrawToolShape({'solid': p}) for p in obstacles]
    editor = make_editor({10: aperture(shapes)})
    outline = box(0, 0, 10, 10)
    expected = outline
    for obstacle in obstacles:
        expected = expected.difference(obstacle.buffer(0.3, quad_segs=16))
    result = copper_pour(editor, outline, 0.3, 16)
    assert result.equals(expected)
    assert isinstance(result, MultiPolygon)
    assert sum(len(p.interiors) for p in result.geoms) == 1
    editor.add_region_geometry(result)
    assert editor.storage_dict[10]['geometry'] == shapes
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict) == [10]
    assert editor.redo()
    assert unary_union([s.geo['solid'] for s in editor.selected]).equals(expected)


def test_copper_pour_native_export_reparse(app, tmp_path):
    from appEditors.grb_plugins.GrbDrawingPlugin import copper_pour
    editor = make_editor({10: aperture([pad(2, 2, 1)])})
    result = copper_pour(editor, box(0, 0, 5, 5), 0.2, 16)
    editor.add_region_geometry(result)
    tools = {key: {**value, 'geometry': [shape.geo for shape in value['geometry']]}
             for key, value in editor.storage_dict.items()}
    expected = unary_union([shape.geo['solid'] for value in editor.storage_dict.values()
                            for shape in value['geometry']])
    edited = editor_save(app, make_gerber(app, NATIVE_GBR), tools, loaded_as_regions=True)
    again = reparse_gerber(app, edited.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)


def test_text_and_rectangle_native_export_reparse(app, tmp_path):
    editor = make_editor()
    parser, family = font_parser(editor)
    editor.add_region_geometry(text_region(parser, 'Oi', family, 'regular', 12, 'MM', 0, 0, 20))
    editor.add_region_geometry(rectangle_region(20, 20, 4, 2, 'c', 'r', 0.5, 30, 16))
    tools = {key: {**value, 'geometry': [shape.geo for shape in value['geometry']]}
             for key, value in editor.storage_dict.items()}
    expected = unary_union([shape.geo['solid'] for value in editor.storage_dict.values() for shape in value['geometry']])
    source = make_gerber(app, NATIVE_GBR)
    edited = editor_save(app, source, tools, loaded_as_regions=editor.loaded_as_regions)
    again = reparse_gerber(app, edited.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)


def opened_panel(editor, kind='rectangle'):
    editor.on_region_drawing(kind)
    panel = editor.region_drawing_panel
    assert panel is not None
    return panel


