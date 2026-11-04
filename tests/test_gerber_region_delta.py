"""Material regions and structural rendering retain unrelated records."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from shapely.geometry import box, MultiPolygon, LineString
from shapely.ops import unary_union

from appEditors.appGerberEditor import ImportEditorGrb
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from test_gerber_editor_pick import make_editor, aperture
from test_gerber_editor_clear import editor_save
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, _headless_qt, make_gerber, NATIVE_GBR, reparse_gerber,
)


@pytest.mark.parametrize('operation', ['union', 'difference'])
def test_material_regions_preserve_holes_islands_and_history(operation):
    ring = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
    island = box(4, 4, 5, 5)
    first = DrawToolShape({'solid': MultiPolygon([ring, island])})
    second = DrawToolShape({'solid': box(7, -1, 11, 11)})
    editor = make_editor({10: aperture([first]), 11: aperture([second])})
    editor.selected = [first, second]
    before = editor.geometry_revision
    expected = {'union': first.geo['solid'].union(second.geo['solid']),
                'intersection': first.geo['solid'].intersection(second.geo['solid']),
                'difference': first.geo['solid'].difference(second.geo['solid']),
                'explode': first.geo['solid'].union(second.geo['solid'])}[operation]
    assert editor.apply_region_operation(operation) != 'fail'
    assert editor.geometry_revision == before + 1
    assert editor.loaded_as_regions
    assert all(editor.storage_dict[key]['type'] == 'REG' for key in editor.storage_dict
               if editor.storage_dict[key]['geometry'])
    assert unary_union([shape.geo['solid'] for shape in editor.selected]).equals(expected)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.storage_dict[11]['geometry'] == [second]
    assert editor.redo()
    assert unary_union([shape.geo['solid'] for shape in editor.selected]).equals(expected)


@pytest.mark.parametrize('kind', ['gerber'])
def test_import_polygon_sources_and_index_lifecycle(kind):
    polygon = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
    source = SimpleNamespace(kind=kind, obj_options={'name': 'source', 'plot': True},
                             solid_geometry=[polygon], drawing_tolerance=0.001, multigeo=False)
    tool = SimpleNamespace(app=SimpleNamespace(collection=MagicMock()),
                           draw_app=SimpleNamespace(gerber_obj=object()))
    tool.app.collection.get_list.return_value = [source]
    assert list(ImportEditorGrb.import_candidates(tool, box(3, 3, 4, 4))) == []
    found = list(ImportEditorGrb.import_candidates(tool, box(-1, -1, 11, 11)))
    assert found[0][1][0]['type'] == 'REG'
    source.solid_geometry[:] = [box(20, 20, 21, 21)]
    assert list(ImportEditorGrb.import_candidates(tool, box(-1, -1, 11, 11))) == []
    tool.app.collection.get_list.return_value = []
    assert list(ImportEditorGrb.import_candidates(tool, box(-1, -1, 11, 11))) == []


@pytest.mark.parametrize('operation', ['difference'])
def test_region_native_save_reparse(app, tmp_path, operation):
    source = make_gerber(app, NATIVE_GBR)
    first = DrawToolShape({'solid': box(0, 0, 10, 10).difference(box(2, 2, 8, 8))})
    second = DrawToolShape({'solid': MultiPolygon([box(7, -1, 11, 11), box(4, 4, 5, 5)])})
    editor = make_editor({10: aperture([first, second])})
    editor.selected = [first, second]
    editor.apply_region_operation(operation)
    tools = {key: {**value, 'geometry': [shape.geo for shape in value['geometry']]}
             for key, value in editor.storage_dict.items() if value['geometry']}
    expected = unary_union([shape.geo['solid'] for shape in editor.selected])
    edited = editor_save(app, source, tools, loaded_as_regions=editor.loaded_as_regions)
    again = reparse_gerber(app, edited.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)


def test_import_preflight_rejects_the_whole_batch():
    editor = make_editor()
    tool = SimpleNamespace(draw_app=editor, app=editor.app, sel_storage=[{
        'type': 'REG', 'size': 0, 'geometry': [DrawToolShape({'solid': box(0, 0, 1, 1)}),
                                             DrawToolShape({'solid': LineString([(0, 0), (1, 1)])})]}])
    before = editor.geometry_revision
    assert ImportEditorGrb.import_shapes(tool) == 'fail'
    assert editor.storage_dict == {}
    assert editor.geometry_revision == before
    assert not editor.history.undo_stack




@pytest.mark.parametrize('operation', ['union', 'difference'])
def test_material_regions_of_a_board_match_geos_without_clipper(operation):
    from unittest import mock
    from appCommon import clipper_ops
    tiles = [box(x * 1.5, y * 1.5, x * 1.5 + 2, y * 1.5 + 2) for x in range(10) for y in range(10)]
    expected = unary_union(tiles) if operation == 'union' else tiles[0].difference(unary_union(tiles[1:]))
    for clipper in (True, False):
        shapes = [DrawToolShape({'solid': tile}) for tile in tiles]
        editor = make_editor({10: aperture(shapes)})
        editor.selected = shapes
        with mock.patch.object(clipper_ops, 'pyclipr', clipper_ops.pyclipr if clipper else None):
            assert editor.apply_region_operation(operation) != 'fail'
        result = unary_union([shape.geo['solid'] for shape in editor.selected])
        assert result.symmetric_difference(expected).area < 1e-6


def test_saving_a_board_of_records_fuses_the_copper_like_geos(app):
    from unittest import mock
    from appCommon import clipper_ops
    source = make_gerber(app, NATIVE_GBR)
    tiles = [box(x * 1.5, y * 1.5, x * 1.5 + 2, y * 1.5 + 2) for x in range(10) for y in range(10)]
    tools = {10: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': tile} for tile in tiles]}}
    saved = []
    for clipper in (True, False):
        with mock.patch.object(clipper_ops, 'pyclipr', clipper_ops.pyclipr if clipper else None):
            saved.append(unary_union(editor_save(app, source, tools, loaded_as_regions=True).solid_geometry))
    assert saved[0].symmetric_difference(saved[1]).area < 1e-6
    assert saved[0].symmetric_difference(unary_union(tiles)).area < 1e-6
