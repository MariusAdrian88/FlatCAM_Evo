"""Geometry Editor: the Import tool copies shapes of other project objects into the edited Geometry."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from PIL import Image, ImageDraw
from PyQt6 import QtCore, QtGui, QtWidgets
from shapely.geometry import GeometryCollection, LinearRing, LineString, MultiPolygon, Polygon, box

from appEditors.appGeoEditor import (AppGeoEditor, DrawToolShape, FCImport, FCImportImage, geometries_at_point,
                                     geometries_in_area, import_source_geometries, import_source_objects)
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


def editor_image_tool(token=1, paths=None, anchor=None, busy=True):
    tool = SimpleNamespace(
        token=token, filename='A', paths=list(paths or []), anchor=anchor,
        ui=SimpleNamespace(import_button=Mock()),
        app=SimpleNamespace(inform=SimpleNamespace(emit=Mock()), log=Mock(), app_units='MM'),
        draw_app=SimpleNamespace(snap_x=0, snap_y=0, update_utility_geometry=Mock(),
                                 delete_utility_geometry=Mock(), storage=Mock()),
        _busy=busy, _cleanup_connected=False)
    tool.draw_app.active_tool = SimpleNamespace(image_tool=tool)
    tool._invalidate_prepared = lambda: GeoImportImageTool._invalidate_prepared(tool)
    tool._clear_preview = lambda: GeoImportImageTool._clear_preview(tool)
    tool.start_image_processing = Mock()
    tool.image_adjustments = ImageImportMixin.image_adjustments
    return tool


def prepared_image(geometries):
    paths = image_paths(geometries)
    anchor = (min(p.bounds[0] for p in paths), min(p.bounds[1] for p in paths)) if paths else None
    return paths, anchor, paths


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


def test_starting_a_new_conversion_clears_the_old_preview():
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    tool = editor_image_tool(token=1, paths=[polygon], anchor=(0.0, 0.0), busy=False)
    tool.image_import_params = Mock(return_value={
        'filename': 'B', 'import_mode': 'raster', 'trace_options': {}, 'dpi': 96, 'mode': 'black',
        'min_area': 0.3, 'mask': [250, 250, 250, 250], 'threshold': 128})
    tool.image_finished = Mock()
    tool.app.worker_task = SimpleNamespace(emit=Mock())
    tool.on_image_import_action = lambda: GeoImportImageTool.on_image_import_action(tool)

    tool.on_image_import_action()

    assert tool.paths == []
    assert tool.anchor is None
    assert tool._busy is True
    tool.draw_app.delete_utility_geometry.assert_called_once()
    tool.ui.import_button.setEnabled.assert_called_with(False)
    tool.app.worker_task.emit.assert_called_once()


def test_failed_or_empty_replacement_clears_the_old_preview():
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    tool = editor_image_tool(token=1, paths=[], anchor=None, busy=True)

    GeoImportImageTool.on_image_finished(tool, 1, prepared_image([polygon]), '')
    assert [path.bounds for path in tool.paths] == [(0.0, 0.0, 2.0, 2.0)]
    assert tool.anchor == (0.0, 0.0)
    tool.draw_app.update_utility_geometry.assert_called_once()
    tool.draw_app.delete_utility_geometry.assert_not_called()

    tool.token = 2
    GeoImportImageTool.on_image_finished(tool, 2, prepared_image([]), '')
    assert tool.paths == []
    assert tool.anchor is None
    tool.draw_app.delete_utility_geometry.assert_called_once()
    tool.draw_app.update_utility_geometry.assert_called_once()
    assert tool.ui.import_button.setEnabled.call_args.args == (True,)
    assert tool.draw_app.storage.mock_calls == []

    tool.token = 3
    GeoImportImageTool.on_image_finished(tool, 3, None, 'boom')
    assert tool.paths == []
    assert tool.draw_app.delete_utility_geometry.call_count == 2
    assert tool.draw_app.storage.mock_calls == []


def test_loading_another_image_invalidates_ready_and_inflight_edit():
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])

    ready = editor_image_tool(token=1, paths=[], anchor=None, busy=True)
    GeoImportImageTool.on_image_finished(ready, 1, prepared_image([polygon]), '')
    old_token = ready.token
    GeoImportImageTool.on_image_loaded(ready, 'B')
    assert ready.token > old_token
    assert ready.paths == []
    assert ready.anchor is None
    assert ready._busy is False
    ready.ui.import_button.setEnabled.assert_called_with(True)
    ready.draw_app.delete_utility_geometry.assert_called_once()
    GeoImportImageTool.on_image_finished(ready, old_token, prepared_image([polygon]), '')
    assert ready.paths == []

    inflight = editor_image_tool(token=1, paths=[], anchor=None, busy=True)
    old_token = inflight.token
    GeoImportImageTool.on_image_loaded(inflight, 'B')
    assert inflight.token > old_token
    assert inflight._busy is False
    assert inflight.ui.import_button.setEnabled.call_args.args == (True,)
    GeoImportImageTool.on_image_finished(inflight, old_token, prepared_image([polygon]), '')
    assert inflight.paths == []


def test_image_load_hook_runs_only_for_an_accepted_replacement(tmp_path):
    class Loader(ImageImportMixin):
        def __init__(self):
            self.filename = 'A'
            self.accepted = []
            self.app = SimpleNamespace(get_last_folder=lambda: str(tmp_path), register_folder=Mock(),
                                       inform=SimpleNamespace(emit=Mock()), log=Mock())
            self.ui = SimpleNamespace(import_button=Mock(),
                                      load_status=SimpleNamespace(color=None, setToolTip=Mock()),
                                      dpi_entry=SimpleNamespace(maximum=lambda: 99999, set_value=Mock()))

        def on_image_loaded(self, filename):
            self.accepted.append(filename)

    tool = Loader()
    tool.ui.import_button.isEnabled.return_value = True

    with patch('appPlugins.ToolImage.ImageImportMixin.QtWidgets.QFileDialog.getOpenFileName',
               return_value=('', '')):
        tool.on_load_image()
    assert tool.filename == 'A'
    assert tool.accepted == []
    tool.ui.import_button.setEnabled.assert_not_called()

    with patch('appPlugins.ToolImage.ImageImportMixin.QtWidgets.QFileDialog.getOpenFileName',
               return_value=(str(tmp_path / 'missing.png'), '')):
        tool.on_load_image()
    assert tool.filename == 'A'
    assert tool.accepted == []
    assert tool.ui.import_button.setEnabled.call_args.args == (True,)
    tool.app.register_folder.assert_not_called()

    replacement = tmp_path / 'b.png'
    Image.new('RGB', (2, 2), 'white').save(replacement)
    with patch('appPlugins.ToolImage.ImageImportMixin.QtWidgets.QFileDialog.getOpenFileName',
               return_value=(str(replacement), '')):
        tool.on_load_image()
    assert tool.filename == str(replacement)
    assert tool.accepted == [str(replacement)]
    tool.app.register_folder.assert_called_once_with(str(replacement))


def test_stale_conversion_after_cancel_is_ignored():
    tool = editor_image_tool(token=1, paths=[], anchor=None, busy=True)

    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    GeoImportImageTool.on_image_finished(tool, 1, prepared_image([polygon]), '')
    assert [path.bounds for path in tool.paths] == [(0.0, 0.0, 2.0, 2.0)]

    GeoImportImageTool.cancel(tool)
    assert tool.paths == []
    assert tool.anchor is None

    GeoImportImageTool.on_image_finished(tool, 1, prepared_image([polygon]), '')
    assert tool.paths == []


def test_editor_image_tool_has_no_gerber_destination(qt_application, tmp_path):
    from appMain import App
    app = SimpleNamespace(
        decimals=4, app_units='MM', resource_location='assets/resources', options={'global_last_folder': 'previous'},
        defaults=SimpleNamespace(get=lambda key, default=None: default, factory_defaults={}),
        log=Mock(), inform=SimpleNamespace(emit=Mock()), worker_task=MagicMock())
    plugin = GeoImportImageTool(app, SimpleNamespace(app=app))

    assert plugin.ui.tf_type_obj_combo.isHidden()
    assert plugin.ui.tf_type_obj_combo_label.isHidden()
    app.get_last_folder = lambda: App.get_last_folder(app)
    app.register_folder = lambda filename: App.register_folder(app, filename)
    filename = tmp_path / 'editor.png'
    Image.new('RGB', (16, 16), (120, 120, 120)).save(filename)
    try:
        with patch('appPlugins.ToolImage.ImageImportMixin.QtWidgets.QFileDialog.getOpenFileName',
                   return_value=(str(filename), '')):
            plugin.on_load_image()
        plugin.prepare_image_preview()
        task = app.worker_task.emit.call_args.args[0]
        task['fcn'](*task['params'])
        assert plugin.ui.preview_label.pixmap().toImage().pixelColor(100, 100).red() == 120
        with patch('appPlugins.ToolImage.ImageImportMixin.QtWidgets.QFileDialog.getOpenFileName',
                   return_value=('', '')) as dialog:
            plugin.on_load_image()
        assert dialog.call_args.kwargs['directory'] == str(tmp_path)
        assert app.options['global_last_folder'] == str(tmp_path)
    finally:
        plugin.cancel()
        plugin.deleteLater()


def test_image_cursor_is_canvas_local_and_restored(qt_application):
    canvas, properties = QtWidgets.QWidget(), QtWidgets.QWidget()
    canvas.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
    app = MagicMock()
    app.decimals, app.app_units, app.resource_location = 4, 'MM', 'assets/resources'
    editor = SimpleNamespace(app=app, canvas=SimpleNamespace(native=canvas))
    with patch.object(GeoImportImageTool, 'run'):
        tool = FCImportImage(editor)
    try:
        assert QtGui.QGuiApplication.overrideCursor() is None
        assert canvas.cursor().shape() == QtCore.Qt.CursorShape.CrossCursor
        assert properties.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
        QtWidgets.QApplication.sendEvent(canvas, QtCore.QEvent(QtCore.QEvent.Type.Leave))
        assert properties.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
        QtWidgets.QApplication.sendEvent(canvas, QtCore.QEvent(QtCore.QEvent.Type.Enter))
        assert canvas.cursor().shape() == QtCore.Qt.CursorShape.CrossCursor
    finally:
        tool.clean_up()
    assert canvas.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
    assert QtGui.QGuiApplication.overrideCursor() is None


def test_editor_progress_cancel_and_stale_completion(qt_application):
    app = MagicMock()
    app.decimals, app.app_units, app.resource_location = 4, 'MM', 'assets/resources'
    editor = SimpleNamespace(app=app, delete_utility_geometry=Mock(), snap_x=0, snap_y=0,
                             update_utility_geometry=Mock())
    plugin = GeoImportImageTool(app, editor)
    editor.active_tool = SimpleNamespace(image_tool=plugin)
    plugin.filename = 'test.png'
    plugin.on_image_import_action()
    token = plugin.token
    progress = plugin._image_progress
    assert progress.isVisible()
    plugin.cancel_image_processing()
    assert not progress.isVisible()
    plugin.on_image_finished(token, prepared_image([box(0, 0, 2, 2)]), '')
    assert plugin.paths == []
    plugin.on_image_import_action()
    current = plugin._image_progress
    plugin.on_image_finished(token, prepared_image([box(0, 0, 2, 2)]), '')
    assert current.isVisible()
    plugin.on_image_finished(plugin.token, prepared_image([box(0, 0, 2, 2)]), '')
    assert not current.isVisible()
    assert len(plugin.paths) == 1
    token = plugin.token
    plugin.ui.mask_bw_entry.slider.setValue(129)
    assert plugin.paths == []
    assert plugin.anchor is None
    plugin.on_image_finished(token, prepared_image([box(0, 0, 2, 2)]), '')
    assert plugin.paths == []
    plugin.cancel()


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


