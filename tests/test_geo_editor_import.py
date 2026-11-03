"""Geometry Editor: the Import tool copies shapes of other project objects into the edited Geometry."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore
from shapely.geometry import LineString, MultiPolygon, box

from appEditors.appGeoEditor import (AppGeoEditor, DrawToolShape, FCImport, geometries_at_point, geometries_in_area,
                                     import_source_geometries, import_source_objects)
from tests.test_geo_editor_history import editor_with_history, qt_application  # noqa: F401


def source(kind, geometry, plot=True, units='MM', **extra):
    return SimpleNamespace(kind=kind, solid_geometry=geometry, obj_options={'plot': plot}, units=units, **extra)


@pytest.fixture
def setup():
    edited = source('geometry', [])
    gerber = source('gerber', [box(10, 10, 12, 12), box(20, 20, 22, 22)])
    excellon = source('excellon', [box(30, 30, 31, 31)])
    geometry = source('geometry', [LineString([(40, 0), (50, 0)])])
    hidden = source('gerber', [box(60, 60, 62, 62)], plot=False)
    cncjob = source('cncjob', [box(70, 70, 72, 72)])
    existing = DrawToolShape(LineString([(0, 0), (1, 0)]))

    editor = editor_with_history([existing])
    editor.fcgeometry = edited
    editor.canvas = SimpleNamespace(pixel_size=lambda: 0.01)
    editor.tool_shape = SimpleNamespace(clear=Mock(), redraw=Mock(), add=Mock())
    editor.app.collection = SimpleNamespace(get_list=lambda: [edited, gerber, excellon, geometry, hidden, cncjob])
    editor.app.app_units = 'MM'
    editor.app.inform = SimpleNamespace(emit=Mock())
    editor.select_tool = Mock()
    tool = FCImport(editor)
    editor.active_tool = tool
    return SimpleNamespace(editor=editor, tool=tool, existing=existing, edited=edited, gerber=gerber,
                           excellon=excellon, geometry=geometry, hidden=hidden, cncjob=cncjob)


def stored(editor):
    return list(editor.storage.get_objects())


def test_pick_from_gerber(setup):
    setup.tool.click_release((11, 11))
    assert [item['geo'].bounds for item in setup.tool.picked] == [(10, 10, 12, 12)]


def test_area_select_enclosing_and_touching(setup):
    setup.tool.area_select((0, 0), (25, 25), True)
    assert len(setup.tool.picked) == 2
    setup.tool.picked = []
    setup.tool.area_select((11, 11), (21, 21), False)
    assert len(setup.tool.picked) == 2
    setup.tool.picked = []
    setup.tool.area_select((11, 11), (21, 21), True)
    assert setup.tool.picked == []


def test_commit_adds_copies_source_unchanged_and_one_undo(setup):
    original = setup.gerber.solid_geometry[0]
    setup.tool.click_release((11, 11))
    setup.tool.click_release((45, 0))
    assert setup.tool.commit() is None
    shapes = stored(setup.editor)
    assert len(shapes) == 3
    added = [shp for shp in shapes if shp is not setup.existing]
    assert sorted(shp.geo.bounds for shp in added) == [(10, 10, 12, 12), (40, 0, 50, 0)]
    assert all(shp.geo is not original for shp in added)
    assert setup.gerber.solid_geometry[0] is original and len(setup.gerber.solid_geometry) == 2
    assert setup.editor.undo()
    assert stored(setup.editor) == [setup.existing]
    assert setup.editor.redo()
    assert len(stored(setup.editor)) == 3


def test_source_in_other_units_is_scaled():
    inch = source('gerber', [box(0, 0, 1, 1)], units='IN')
    assert import_source_geometries(inch, 'MM')[0].bounds == (0, 0, 25.4, 25.4)
    assert import_source_geometries(inch, 'IN')[0].bounds == (0, 0, 1, 1)


