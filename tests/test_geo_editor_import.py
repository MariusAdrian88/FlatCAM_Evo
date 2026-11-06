"""Geometry Editor: the Import tool copies shapes of other project objects into the edited Geometry."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from PIL import Image, ImageDraw
from shapely.geometry import GeometryCollection, LinearRing, LineString, Polygon, box

from appEditors.appGeoEditor import (AppGeoEditor, DrawToolShape, FCImport, FCImportImage, import_source_geometries)
from appEditors.geo_plugins.GeoImportImagePlugin import GeoImportImageTool, image_paths
from appPlugins.ToolImage.ImageImportMixin import ImageImportMixin
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


def image_tool_for_conversion():
    class ImageTool(ImageImportMixin):
        def __init__(self):
            self.app = SimpleNamespace(
                log=Mock(), options={}, app_units='MM', decimals=4,
                defaults=SimpleNamespace(get=lambda key, default=None: default, factory_defaults={}))

    return ImageTool()


def test_raster_conversion_keeps_holes_and_their_relative_position(tmp_path):
    filename = tmp_path / 'ring.png'
    image = Image.new('RGB', (20, 20), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 17, 17), fill='black')
    draw.rectangle((5, 5, 14, 14), fill='white')
    image.save(filename)

    geos = image_tool_for_conversion().image_geometry(
        str(filename), 'raster', 25.4, 'black', [250, 250, 250, 250], None, 0.0, 'MM')
    paths = image_paths(geos)

    assert len(paths) == 2
    assert all(path.geom_type == 'LinearRing' for path in paths)
    assert sorted(path.bounds for path in paths) == [(2.0, -18.0, 18.0, -2.0), (5.0, -15.0, 15.0, -5.0)]


def test_place_image_uses_lower_left_anchor_and_is_one_undo(tmp_path):
    existing = DrawToolShape(LineString([(0, 0), (1, 0)]))
    editor = editor_with_history([existing])
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)

    outer = Polygon([(2, -18), (18, -18), (18, -2), (2, -2)])
    hole = Polygon([(5, -15), (15, -15), (15, -5), (5, -5)])
    paths = image_paths([Polygon(outer.exterior.coords, [hole.exterior.coords])])

    tool = FCImportImage.__new__(FCImportImage)
    tool.name, tool.draw_app, tool.app = 'import_image', editor, editor.app
    tool.image_tool = SimpleNamespace(paths=paths, anchor=(2.0, -18.0))
    tool.points, tool.geometry, tool.complete = [], [], False
    editor.active_tool = tool

    assert tool.click((10, 20)) != 'fail'
    assert tool.complete
    assert tool.image_tool.paths == []
    AppGeoEditor.on_shape_complete(editor)

    added = [shape for shape in stored(editor) if shape is not existing]
    assert sorted(shape.geo.bounds for shape in added) == [(10.0, 20.0, 26.0, 36.0), (13.0, 23.0, 23.0, 33.0)]
    assert all(isinstance(shape.geo, LinearRing) for shape in added)

    # a second click without a new conversion cannot stamp the image again
    assert tool.click((40, 40)) == 'fail'
    assert len(stored(editor)) == 3

    assert editor.undo()
    assert stored(editor) == [existing]
    assert editor.redo()
    assert len(stored(editor)) == 3


def test_editor_geometry_keeps_paths_and_filters_polygon_leaves():
    tool = image_tool_for_conversion()
    ring = LinearRing([(0, 0), (2, 0), (2, 2), (0, 2)])
    small = box(0, 0, 0.1, 0.1)
    big = box(0, 0, 2, 2)
    collection = GeometryCollection([ring, small, big])
    tool.import_image_handler = lambda *args, **kwargs: [collection]

    geos = tool.image_geometry('ignored', 'raster', 96, 'black', None, None, 0.3, 'MM', keep_paths=True)
    assert any(geometry.geom_type == 'LinearRing' and geometry.bounds == (0, 0, 2, 2) for geometry in geos)
    assert any(geometry.bounds == big.bounds for geometry in geos)
    assert all(geometry.bounds != small.bounds for geometry in geos)

    # the main app keeps its existing area-only behavior: a standalone path is dropped
    tool.import_image_handler = lambda *args, **kwargs: [ring]
    assert tool.image_geometry('ignored', 'raster', 96, 'black', None, None, 0.3, 'MM') == []
    assert tool.image_geometry('ignored', 'raster', 96, 'black', None, None, 0.3, 'MM',
                               keep_paths=True) == [ring]


def test_large_image_moving_extent_keeps_every_path_for_placement(qt_application, tmp_path):
    image = Image.new('L', (66, 66), 'white')
    for y in range(0, 66, 2):
        for x in range(0, 66, 2):
            image.putpixel((x, y), 0)
    filename = tmp_path / 'many_islands.png'
    image.save(filename)
    app = MagicMock()
    app.decimals, app.app_units, app.resource_location = 4, 'MM', 'assets/resources'
    editor = SimpleNamespace(app=app, delete_utility_geometry=Mock(), snap_x=0, snap_y=0,
                             update_utility_geometry=Mock(), in_action=False)
    plugin = GeoImportImageTool(app, editor)
    placement = FCImportImage.__new__(FCImportImage)
    placement.draw_app, placement.app, placement.image_tool = editor, app, plugin
    placement.points = []
    editor.active_tool = placement
    plugin.filename = str(filename)
    plugin.ui.min_area_entry.set_value(0)
    plugin.ui.dpi_entry.set_value(25)
    plugin.on_image_import_action()
    task = app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    assert len(plugin.paths) == 1089
    assert len(plugin.preview_paths) == 1
    assert placement.utility_geometry((10, 20)).geo[0].bounds[:2] == (10, 20)
    assert placement.click((10, 20)) != 'fail'
    assert len(placement.geometry) == 1089
    assert all(shape.geo.geom_type == 'LinearRing' for shape in placement.geometry)
    plugin.cancel()


