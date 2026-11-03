"""Polygonize selected outlines without discarding unused geometry."""
from unittest.mock import MagicMock, Mock

import pytest
from PyQt6 import QtGui, QtWidgets
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import unary_union

from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape
from appGUI.MainGUI import MainGUI
from tests.test_geo_editor_history import editor_with_history, run_worker


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.mark.parametrize('kind', ['segments'])
def test_polygonize_closed_paths_and_separate_segments_with_one_undo(kind):
    polygon = box(0, 0, 2, 2)
    coords = list(polygon.exterior.coords)
    geometries = {
        'ring': [polygon.exterior],
        'closed_path': [LineString(coords)],
        'segments': [LineString([a, b]) for a, b in zip(coords, coords[1:])],
        'crossing': [polygon.exterior, LineString([(0, 1), (2, 1)])]
    }[kind]
    originals = [DrawToolShape(geo) for geo in geometries]
    editor = editor_with_history(originals, originals)
    AppGeoEditor.polygonize(editor)
    assert list(editor.storage.get_objects()) == originals
    run_worker(editor)
    results = list(editor.storage.get_objects())
    assert len(results) == (2 if kind == 'crossing' else 1)
    assert all(isinstance(shape.geo, Polygon) and shape.geo.is_valid for shape in results)
    assert unary_union([shape.geo for shape in results]).equals(polygon)
    assert set(editor.selected) == set(results)
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(originals)
    assert editor.selected == originals
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == results


def test_polygonize_preserves_unused_lines_existing_polygons_and_partial_record_metadata():
    outline = box(0, 0, 2, 2).exterior
    # This path contributes an edge and has a dangling tail that must survive.
    partial = DrawToolShape(LineString([(2, 2), (2, 0), (3, 0)]))
    partial.data['custom'] = {'label': 'tail'}
    rest = DrawToolShape(LineString([(2, 0), (0, 0), (0, 2), (2, 2)]))
    unused = DrawToolShape(LineString([(8, 0), (9, 0)]))
    existing = DrawToolShape(box(10, 0, 12, 2))
    unselected = DrawToolShape(box(20, 0, 22, 2).exterior)
    originals = [partial, rest, unused, existing, unselected]
    editor = editor_with_history(originals, originals[:-1])
    AppGeoEditor.polygonize(editor)
    run_worker(editor)
    shapes = list(editor.storage.get_objects())
    assert unused in shapes and existing in shapes and unselected in shapes
    assert partial not in shapes and rest not in shapes
    result, = [shape for shape in shapes if shape not in originals and isinstance(shape.geo, Polygon)]
    assert result.geo.equals(Polygon(outline))
    tail, = [shape for shape in shapes if shape not in originals and isinstance(shape.geo, LineString)]
    assert tail.geo.equals(LineString([(2, 0), (3, 0)]))
    assert tail.data['custom'] == partial.data['custom']
    assert tail.data['bounds'] == tail.geo.bounds
    assert editor.undo() and set(editor.storage.get_objects()) == set(originals)


def test_polygonize_nodes_multipart_linework_and_preserves_nested_faces():
    outer, inner = box(0, 0, 4, 4), box(1, 1, 2, 2)
    original = DrawToolShape(MultiLineString([outer.exterior.coords, inner.exterior.coords]))
    editor = editor_with_history([original], [original])
    AppGeoEditor.polygonize(editor)
    run_worker(editor)
    polygons = [shape.geo for shape in editor.storage.get_objects()]
    assert len(polygons) == 2
    assert any(poly.equals(outer.difference(inner)) for poly in polygons)
    assert any(poly.equals(inner) for poly in polygons)
    assert sum(poly.area for poly in polygons) == outer.area


@pytest.mark.parametrize('geometries', [[], [box(0, 0, 2, 2)]])
def test_polygonize_without_usable_enclosure_leaves_storage_and_history_unchanged(geometries):
    originals = [DrawToolShape(geo) for geo in geometries]
    editor = editor_with_history(originals, originals)
    AppGeoEditor.polygonize(editor)
    run_worker(editor)
    assert list(editor.storage.get_objects()) == originals
    assert editor.selected == originals
    assert not editor.undo()
    assert not any('[success]' in str(call) for call in editor.app.inform.emit.call_args_list)


