"""PCB preview geometry, physical layer stack, and interactive 3D rendering."""

from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from shapely import LineString, Point, Polygon, box


def source(solid=(), follow=(), units='MM'):
    from appPlugins.ToolPCBViewer.PCBViewerGen import Source
    return Source(solid, follow, units)


def build(sources, units='MM'):
    from appPlugins.ToolPCBViewer.PCBViewerGen import PCBGeometry
    return {layer.name: layer for layer in PCBGeometry().build(sources, units)}


def cap_area(layer):
    triangles = layer.vertices[layer.faces]
    top = triangles[np.all(triangles[:, :, 2] == 1, axis=1), :, :2].astype(float)
    a, b = top[:, 1] - top[:, 0], top[:, 2] - top[:, 0]
    return np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2


def test_concave_outline_nested_cutouts_and_drilled_mesh():
    shell = Polygon([(0, 0), (20, 0), (20, 12), (12, 12), (12, 7), (8, 7), (8, 12), (0, 12)])
    cutout = box(2, 2, 6, 6)
    island = box(3, 3, 4, 4)
    drill = Point(17, 5).buffer(0.5, quad_segs=16)
    outline = source(follow=[LineString(p.exterior.coords) for p in (shell, cutout, island)])
    layers = build({'outline': outline, 'drills': source([drill]), 'copper_top': source([shell])})
    expected = shell.difference(cutout).union(island).difference(drill)
    board = layers['board']
    assert board.geometry.symmetric_difference(expected).area < 1e-8
    assert layers['copper_top'].geometry.symmetric_difference(expected).area < 1e-8
    assert cap_area(board) == pytest.approx(expected.area, rel=1e-6)
    triangles = board.vertices[board.faces].astype(float)
    volume = np.einsum('ij,ij->i', triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    assert volume == pytest.approx(expected.area, rel=1e-6)


def test_bottom_layers_keep_original_coordinates_without_mutating_sources():
    # Neither layer is symmetric, and their own bounds have different centers.
    copper = source([box(2, 1, 3, 2), box(4, 3, 6, 4)])
    overlay = source([box(1, 5, 2, 6)])
    layers = build({'outline': source([box(0, 0, 20, 10)]),
                    'copper_top': copper, 'copper_bottom': copper, 'overlay_bottom': overlay})
    assert layers['copper_bottom'].geometry.covers(Point(2.5, 1.5))
    assert layers['copper_bottom'].geometry.covers(Point(5, 3.5))
    assert layers['overlay_bottom'].geometry.bounds == pytest.approx((1, 5, 2, 6))
    assert layers['copper_top'].geometry.covers(Point(2.5, 1.5))
    assert copper.solid[0].bounds == (2, 1, 3, 2)


def test_soldermask_is_negative_clipped_and_drilled_on_both_sides():
    opening = box(1, 1, 3, 3)
    drill = Point(5, 5).buffer(0.5)
    board = box(0, 0, 10, 10)
    layers = build({'outline': source([board]), 'drills': source([drill]),
                    'soldermask_top': source([opening]), 'soldermask_bottom': source([opening]),
                    'overlay_top': source([box(-5, -5, 15, 15)])})
    # All selected geometry contributes to the common bounds; this overhanging overlay is centered on the board.
    assert not layers['soldermask_top'].geometry.covers(Point(2, 2))
    assert layers['soldermask_top'].geometry.covers(Point(8, 2))
    assert not layers['soldermask_bottom'].geometry.covers(Point(2, 2))
    assert layers['soldermask_bottom'].geometry.covers(Point(8, 2))
    for layer in layers.values():
        assert not layer.geometry.covers(Point(5, 5))
        assert layer.geometry.difference(board).is_empty


def test_preview_without_outline_uses_combined_bounds_and_handles_empty_layers():
    copper = source([box(-3, 2, 1, 4), box(3, 5, 7, 8)])
    layers = build({'copper_top': copper, 'soldermask_top': source([box(-3, 2, 7, 8)])})
    assert layers['board'].geometry.bounds == (-3, 2, 7, 8)
    assert layers['soldermask_top'].geometry.is_empty
    with pytest.raises(ValueError, match='[Oo]bject|[Gg]eometry'):
        build({})
    with pytest.raises(ValueError, match='[Oo]utline|[Cc]losed'):
        build({'outline': source(follow=[LineString([(0, 0), (1, 1)])]), 'copper_top': copper})


@pytest.fixture
def viewer_app():
    import logging
    from PyQt6 import QtCore, QtGui, QtWidgets
    from tests.test_plotcanvas3d import CanvasApp
    from appGUI.PlotCanvas3d import PlotCanvas3d
    from appGUI.VisPyPatches import apply_patches
    from appGUI.GUIElements import FCDetachableTab2
    from appHandlers.appUIActions import AppUIActions

    class Signals(QtCore.QObject):
        task = QtCore.pyqtSignal(dict)
        cleanup = QtCore.pyqtSignal()
        object_changed = QtCore.pyqtSignal(object)
        status = QtCore.pyqtSignal(object, str, str)
        inform = QtCore.pyqtSignal(str)

    class Collection(QtGui.QStandardItemModel):
        def get_by_name(self, name):
            return next((obj for obj in self.objects if obj.obj_options['name'] == name), None)

        def get_list(self):
            return self.objects

        def get_active(self):
            return self.objects[0]

    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_patches()
    app = CanvasApp()
    app.signals = Signals()
    app.worker_task = app.signals.task
    app.worker_task.connect(lambda task: task['fcn'](*task['params']))
    app.cleanup = app.signals.cleanup
    app.object_status_changed = app.signals.status
    app.app_obj = SimpleNamespace(object_changed=app.signals.object_changed)
    app.inform = app.signals.inform
    app.messages = []
    app.inform.connect(app.messages.append)
    app.defaults = SimpleNamespace(report_usage=lambda name: None, get=lambda key, default=None: default)
    app.log = logging.getLogger(__name__)
    app.app_plugins = []
    app.decimals = 4
    app.resource_location = 'assets/resources'
    app.plugin_tab_locked = False
    app.collection = Collection()
    app.collection.objects = [SimpleNamespace(kind='gerber', obj_options={'name': 'source'}, units='MM',
                                              solid_geometry=[box(1000, 2000, 1020, 2010)], follow_geometry=[]),
                              SimpleNamespace(kind='excellon', obj_options={'name': 'drill'}, units='MM',
                                              solid_geometry=[Point(1005, 2005).buffer(0.5)], tools={})]
    for kind in ('gerber', 'excellon'):
        root = QtGui.QStandardItem(kind.title())
        app.collection.appendRow(root)
        for obj in app.collection.objects:
            if obj.kind == kind:
                root.appendRow(QtGui.QStandardItem(obj.obj_options['name']))
    app.ui.notebook = QtWidgets.QTabWidget()
    app.ui.properties_tab = QtWidgets.QWidget()
    app.ui.plugin_tab = QtWidgets.QWidget()
    app.ui.plugin_tab.setObjectName('plugin_tab')
    app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(app.ui.plugin_tab)
    app.ui.plugin_scroll_area = QtWidgets.QScrollArea()
    app.ui.plugin_scroll_area.setWidgetResizable(True)
    app.ui.plugin_tab_layout.addWidget(app.ui.plugin_scroll_area)
    app.ui.notebook.addTab(app.ui.properties_tab, 'Properties')
    app.ui.notebook.addTab(app.ui.plugin_tab, 'Plugin')
    app.ui.splitter = QtWidgets.QSplitter()
    app.ui.plot_tab_area = FCDetachableTab2()
    app.ui.toggle_coords = lambda **kwargs: None
    app.ui.toggle_delta_coords = lambda **kwargs: None
    app.ui.coords_toolbar = QtWidgets.QWidget(app.ui)
    app.ui.delta_coords_toolbar = QtWidgets.QWidget(app.ui)
    app.area_3d_tab = QtWidgets.QWidget()
    app.area_3d_tab.setObjectName('3D_area_tab')
    app.ui.plot_tab_area.addTab(app.area_3d_tab, 'Plot 3D')
    app.plotcanvas3d = PlotCanvas3d(QtWidgets.QVBoxLayout(app.area_3d_tab), app)
    app.plotcanvas3d.size = (800, 600)
    app.plotcanvas3d.central_widget.size = (800, 600)
    app.plotcanvas3d.view.size = (800, 600)
    actions = AppUIActions(app)
    app.on_3d_area = actions.on_3d_area
    app.on_notebook_closed = actions.on_notebook_closed
    app.ui.plot_tab_area.tab_closed_signal.connect(actions.on_plot_area_tab_closed)
    yield app, qapp
    tool = getattr(app, 'pcb_viewer_tool', None)
    if tool is not None:
        tool.on_plugin_cleanup()
    if app.plotcanvas3d is not None:
        app.plotcanvas3d.close()
    for widget in (app.ui.notebook, app.ui.splitter, app.ui.plot_tab_area, app.ui):
        widget.deleteLater()
    qapp.processEvents()


def test_mesh_rendering_and_fit_survive_resize_and_show_both_sides(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewerView import PCBView
    app, qapp = viewer_app
    canvas = app.plotcanvas3d
    view = PCBView(canvas)
    material = {'board_thickness': 0.8, 'copper_thickness': 18, 'mask_thickness': 100,
                'board_color': '#804020FF', 'copper_color': '#FFD700FF', 'board_alpha': 255,
                'overlay_color': '#FF0000FF', 'soldermask_color': '#FF000080'}
    layers = build({'outline': source([box(1000, 2000, 1020, 2010)]),
                    'overlay_top': source([box(1002, 2002, 1004, 2004)]),
                    'overlay_bottom': source([box(1002, 2002, 1004, 2004)])})
    for layer in layers.values():
        view.set_layer(layer)
    view.update_style(material, 'MM')
    for side, channel in (('top', 0), ('bottom', 0)):
        canvas.view.camera.set_view(side)
        canvas.fit_view()
        image = canvas.render()[:, :, :3].astype(int)
        others = np.delete(image, channel, axis=2).max(axis=2)
        assert np.count_nonzero(image[:, :, channel] - others > 100) > 100
    canvas.view.size = (500, 400)
    canvas.on_view_resize(None)
    points = canvas.view.camera._scene_transform.map(np.array([
        (1000, 2000, 0), (1020, 2010, 0.8)]))
    screen = points[:, :2] / points[:, 3:]
    assert np.all(screen >= 0)
    assert np.all(screen <= (500, 400))
    view.clear()
    assert canvas.content_bounds is None


def test_center_camera_enables_trackball_and_lmb_restores_screen_rotation(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    from tests.test_plotcanvas3d import TestPlotCanvas3d

    app, qapp = viewer_app
    tool = ToolPCBViewer(app)
    tool.run()
    qapp.processEvents()
    canvas = tool.view.canvas
    camera = canvas.view.camera
    mouse_event = TestPlotCanvas3d.mouse_event
    points = np.array([(1010, 2005, 0, 1), (1020, 2005, 0, 1)])
    for reference in ('center', 'lmb', 'center'):
        tool.ui.camera_reference.set_value(reference)
        tool.ui.camera_reference.activated_custom.emit(reference)
        camera.set_view('top')
        canvas.fit_view()
        camera.viewbox_mouse_event(mouse_event('mouse_move', (650, 560), (760, 300), (1,)))
        projected = camera._scene_transform.map(points)
        screen = projected[:, :2] / projected[:, 3:]
        normal = camera._scene_transform.map(points[0] + (0, 0, 10, 0))
        if reference == 'center':
            assert abs(screen[1, 1] - screen[0, 1]) > 1
            np.testing.assert_allclose(normal[:2] / normal[3], screen[0], atol=1e-5)
        else:
            # The existing two-axis rotation tilts this board normal out of the screen.
            assert np.linalg.norm(normal[:2] / normal[3] - screen[0]) > 1
        camera.viewbox_mouse_event(mouse_event('mouse_release'))


def test_bad_color_during_build_can_be_corrected(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    from tests.conftest import _raise_qt_errors
    app, qapp = viewer_app
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    tool.run(source=app.collection.objects[0])
    tool.ui.colors['board'].set_value('#12')
    qapp.processEvents()
    _raise_qt_errors()
    tool.ui.colors['board'].set_value('#9B8049FF')
    tool._timer.stop()
    tool.on_redraw()
    assert tool.view.layers['board'][1]


def _export_material():
    return dict(board_thickness=0.8, copper_thickness=18, mask_thickness=100,
                board_alpha=128, board_color='#80402080', copper_color='#FFD700FF',
                soldermask_color='#00800080', overlay_color='#FFFFFFFF')


def _export_streams(data):
    import re
    import zlib
    objects = _pdf3d_objects(data)
    streams = {}
    for number, obj in objects.items():
        if b'\nstream\n' in obj:
            dictionary, payload = obj.split(b'\nstream\n', 1)
            length = int(re.search(rb'/Length\s+(\d+)', dictionary)[1])
            payload = payload[:length]
            streams[number] = (dictionary, zlib.decompress(payload)
                               if b'/FlateDecode' in dictionary else payload)
    return objects, streams


@pytest.mark.parametrize('units', ['MM', 'IN'])
def test_pcb_export_mesh_roundtrip_preserves_precision_stack_and_sources(units):
    from appPlugins.ToolPCBViewer.PCBViewerExport import export_meshes
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene
    from tests.u3d_reader import read_u3d

    factor = 1 / 25.4 if units == 'IN' else 1
    x, y = 1e7, -1e7
    outline = box(x, y, x + 20 * factor, y + 10 * factor)
    artwork = box(x + 2 * factor, y + factor, x + 3 * factor, y + 2 * factor)
    layers = build({'outline': source([outline], units=units),
                    **{name: source([artwork], units=units) for name in
                       ('copper_top', 'copper_bottom', 'overlay_top', 'overlay_bottom')},
                    'soldermask_top': source([outline], units=units)}, units)
    saved = {name: (layer.vertices.copy(), layer.faces.copy(), layer.origin)
             for name, layer in layers.items()}
    meshes = export_meshes(tuple(layers.values()), _export_material(), units)
    assert meshes
    assert {mesh['name'] for mesh in meshes} == {
        name for name, layer in layers.items() if len(layer.faces)}
    scene = read_u3d(write_scene(meshes))
    stack = {'board': (0, 0.8), 'copper_top': (0.8, 0.818),
             'copper_bottom': (-0.018, 0), 'overlay_top': (0.918, 0.919),
             'overlay_bottom': (-0.019, -0.018)}
    for mesh in meshes:
        name = mesh['name']
        layer = layers[name]
        assert mesh['positions'].dtype == np.float64
        assert mesh['faces'].dtype == np.uint32
        assert np.array_equal(mesh['faces'], layer.faces)
        expected_xy = layer.vertices[:, :2].astype(float) + (
            np.array(layer.origin) - layers['board'].origin)
        assert np.allclose(mesh['positions'][:, :2], expected_xy, atol=1e-12, rtol=0)
        assert np.ptp(mesh['positions'][:, 2]) == pytest.approx(
            (stack[name][1] - stack[name][0]) * factor)
        assert mesh['positions'][:, 2].min() == pytest.approx(stack[name][0] * factor)
        decoded = scene['meshes'][scene['nodes'][name]['resource']]
        assert np.allclose(decoded['positions'], mesh['positions'], atol=1e-6, rtol=0)
        assert np.array_equal(decoded['faces'], layer.faces)
        shading, = scene['chains'][(0, name)]['shading']
        shader = scene['shaders'][shading['shader_lists'][0][0]]
        material = scene['materials'][shader['material']]
        assert material['diffuse'] == pytest.approx(mesh['color'])
        assert material['opacity'] == pytest.approx(128 / 255 if name == 'board' else 1)
    for name, layer in layers.items():
        vertices, faces, origin = saved[name]
        assert np.array_equal(layer.vertices, vertices)
        assert np.array_equal(layer.faces, faces)
        assert layer.origin == origin


def test_pcb_export_frame_fits_projected_bounds_with_five_percent_padding():
    import itertools
    from appPlugins.ToolPCBViewer.PDF3DWriter import export_frame
    from vispy.util.quaternion import Quaternion
    from vispy.util import transforms

    bounds = np.array([(-17, -3, -0.12), (23, 8, 0.919)])
    frame = export_frame(bounds)
    q = Quaternion.create_from_axis_angle(-np.degrees(np.arctan(1 / np.sqrt(2))), 1, 0, 0, degrees=True)
    q *= Quaternion.create_from_axis_angle(45, 0, 1, 0, degrees=True)
    angle, x, y, z = q.get_axis_angle()
    rotation = transforms.rotate(np.degrees(angle), (x, z, y))[:3, :3].T @ np.array(
        [[1, 0, 0], [0, 0, 1], [0, 1, 0]])
    center = bounds.mean(axis=0)
    projected = (np.array(list(itertools.product(*zip(*bounds)))) - center) @ rotation
    extent = np.ptp(projected[:, :2], axis=0)
    page = np.asarray(frame['page_size'])
    assert np.array_equal(frame['bounds'], bounds)
    assert frame['center'] == pytest.approx(center)
    assert frame['rotation'] == pytest.approx(rotation)
    assert page.max() == pytest.approx(297 * 72 / 25.4)
    assert page[0] / page[1] == pytest.approx(extent[0] / extent[1])
    assert page / frame['scale'] == pytest.approx(extent * 1.05)
    assert np.asarray(frame['eye']) + frame['distance'] * rotation[:, 2] == pytest.approx(center)
    assert np.all(((np.array(list(itertools.product(*zip(*bounds)))) - frame['eye']) @ rotation)[:, 2] > 0)


def test_pcb_export_poster_is_rendered_iso_and_not_double_blended(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewerExport import export_meshes, render_poster
    from appPlugins.ToolPCBViewer.PDF3DWriter import export_frame
    from appPlugins.ToolPCBViewer.PCBViewerGen import Layer
    app, _qapp = viewer_app
    layers = tuple(build({'outline': source([box(1e7, 1e7, 1e7 + 20, 1e7 + 10)])}).values())
    values = _export_material()
    values['board_color'] = '#FF0000FF'
    values['board_alpha'] = 255
    positions = np.concatenate([mesh['positions'] for mesh in export_meshes(layers, values, 'MM')])
    frame = export_frame(np.array([positions.min(axis=0), positions.max(axis=0)]))
    opaque = render_poster(layers, values, 'MM', frame)
    assert opaque.dtype == np.uint8 and opaque.ndim == 3 and opaque.shape[2] == 3
    assert max(opaque.shape[:2]) == pytest.approx(1600, abs=2)
    assert opaque.shape[1] / opaque.shape[0] == pytest.approx(
        frame['page_size'][0] / frame['page_size'][1], abs=0.005)
    assert opaque[0, 0] == pytest.approx((191, 191, 191), abs=1)
    red = opaque[:, :, 0].astype(int) - opaque[:, :, 1:].max(axis=2).astype(int) > 80
    assert np.count_nonzero(red) > 1000
    camera = app.plotcanvas3d.view.camera
    camera.set_view('bottom')
    camera.center = (1234, -5678, 9)
    camera.scale_factor = 321
    state = camera.get_state()
    assert np.array_equal(render_poster(layers, values, 'MM', frame), opaque)
    assert camera.get_state() == state
    values['board_alpha'] = 128
    half = render_poster(layers, values, 'MM', frame)
    # The closed board blends both surfaces over grey with two-sided rendering.
    interior = red & (opaque[:, :, 1] < 5)
    assert np.count_nonzero(interior) > 1000
    assert np.median(half[:, :, 1][interior]) == pytest.approx(191 * (1 - 128 / 255) ** 2, abs=4)

    layers = (Layer('board', box(0, 0, 20, 10),
                    np.array([[0, 0, 1], [20, 0, 1], [20, 10, 1], [0, 10, 1]], dtype=np.float32),
                    np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32), origin=(0, 0)),)
    positions = np.concatenate([mesh['positions'] for mesh in export_meshes(layers, values, 'MM')])
    frame = export_frame(np.array([positions.min(axis=0), positions.max(axis=0)]))
    values['board_alpha'] = 255
    opaque = render_poster(layers, values, 'MM', frame)
    interior = opaque[:, :, 1] < 5
    assert np.count_nonzero(interior) > 1000
    values['board_alpha'] = 128
    half = render_poster(layers, values, 'MM', frame)
    # An open quad blends once, without an extra framebuffer-alpha composite.
    assert np.median(half[:, :, 1][interior]) == pytest.approx(191 * (1 - 128 / 255), abs=4)


def test_pcb_export_pdf_embeds_exact_rgb_poster_and_shared_fallback(tmp_path):
    import re
    from appPlugins.ToolPCBViewer.PDF3DWriter import write, export_frame
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene
    poster = np.arange(7 * 11 * 3, dtype=np.uint8).reshape(7, 11, 3)
    bounds = np.array([(0, 0, 0), (1, 1, 1)])
    path = tmp_path / 'poster.pdf'
    write(path, write_scene([_u3d_cube()]), bounds, poster=poster)
    objects, streams = _export_streams(path.read_bytes())
    image_number, (dictionary, image) = next((number, stream) for number, stream in streams.items()
                                             if b'/Subtype /Image' in stream[0])
    assert b'/FlateDecode' in dictionary and b'/DeviceRGB' in dictionary
    assert re.search(rb'/Width\s+11\b', dictionary)
    assert re.search(rb'/Height\s+7\b', dictionary)
    assert re.search(rb'/BitsPerComponent\s+8\b', dictionary)
    assert image == poster.tobytes()
    annotation, = (obj for obj in objects.values() if b'/Subtype /3D' in obj)
    form_number = int(re.search(rb'/AP\s*<<\s*/N\s+(\d+) 0 R', annotation)[1])
    form_dict, form = streams[form_number]
    assert b'/Subtype /Form' in form_dict
    image_name = re.search(rb'/([^\s/]+)\s+' + str(image_number).encode() + rb' 0 R', form_dict)[1]
    assert b'/' + image_name + b' Do' in form
    page, = (obj for obj in objects.values() if re.search(rb'/Type /Page\b', obj))
    media = list(map(float, re.search(rb'/MediaBox\s*\[([^]]+)\]', page)[1].split()))
    assert media == pytest.approx([0, 0, *export_frame(bounds)['page_size']])
    content = streams[int(re.search(rb'/Contents\s+(\d+) 0 R', page)[1])][1]
    form_name = re.search(rb'/([^\s/]+)\s+' + str(form_number).encode() + rb' 0 R', page)[1]
    assert b'/' + form_name + b' Do' in content


def _export_tool(viewer_app, monkeypatch, path):
    from appGUI.GUIElements import FCFileSaveDialog
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    app, qapp = viewer_app
    app.get_last_save_folder = lambda: str(path.parent)
    monkeypatch.setattr(FCFileSaveDialog, 'get_saved_filename', lambda *args, **kwargs: (str(path), '*.pdf'))
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    tool.run(source=app.collection.objects[0])
    qapp.processEvents()
    return tool, app, qapp


def test_pcb_export_controller_writes_ui_geometry_opacity_and_snapshot(viewer_app, monkeypatch, tmp_path):
    from tests.u3d_reader import read_u3d
    path = tmp_path / 'board.pdf'
    tool, app, qapp = _export_tool(viewer_app, monkeypatch, path)
    tool.ui.board_thickness.set_value(1.2)
    tool.ui.board_alpha.set_value(96)
    tasks = []
    app.worker_task.disconnect()
    app.worker_task.connect(tasks.append)
    assert tool.ui.export_pdf_button.isEnabled()
    tool.on_export_pdf()
    tool.on_export_pdf()
    assert len(tasks) == 1
    app.collection.objects[0].solid_geometry = [box(3000, 4000, 3040, 4030)]
    app.app_obj.object_changed.emit(app.collection.objects[0])
    job = tasks.pop()
    job['fcn'](*job['params'])
    qapp.processEvents()
    _objects, streams = _export_streams(path.read_bytes())
    scene = read_u3d(next(payload for dictionary, payload in streams.values() if b'/Subtype /U3D' in dictionary))
    board = scene['meshes'][scene['nodes']['board']['resource']]
    points = np.array(board['positions'])
    assert np.ptp(points, axis=0) == pytest.approx((20, 10, 1.2))
    assert points[:, :2].min(axis=0) == pytest.approx((-10, -5))
    shading, = scene['chains'][(0, 'board')]['shading']
    shader = scene['shaders'][shading['shader_lists'][0][0]]
    assert scene['materials'][shader['material']]['opacity'] == pytest.approx(96 / 255)
    assert any(b'/Subtype /Image' in dictionary for dictionary, _payload in streams.values())


def test_pcb_export_readiness_blocks_pending_failed_and_closed_preview(viewer_app, monkeypatch, tmp_path):
    from appGUI.GUIElements import FCFileSaveDialog
    path = tmp_path / 'not-ready.pdf'
    tool, app, qapp = _export_tool(viewer_app, monkeypatch, path)
    dialogs = []
    monkeypatch.setattr(FCFileSaveDialog, 'get_saved_filename', lambda *args, **kwargs: dialogs.append(True))
    tasks = []
    app.worker_task.disconnect()
    app.worker_task.connect(tasks.append)
    tool.on_selection_changed()
    tool.on_export_pdf()
    tool._timer.stop()
    tool.on_redraw()
    tool.on_export_pdf()
    assert not tool.ui.export_pdf_button.isEnabled()
    def fail_build(*args, **kwargs):
        raise ValueError('Invalid board geometry')
    monkeypatch.setattr(tool._engine, 'build', fail_build)
    job = tasks.pop()
    job['fcn'](*job['params'])
    qapp.processEvents()
    tool.on_export_pdf()
    assert not tool.ui.export_pdf_button.isEnabled()
    assert 'Invalid board geometry' in tool.ui.status.text()
    tool.on_plugin_cleanup()
    tool.on_export_pdf()
    assert not dialogs and not path.exists()


def test_pcb_export_atomic_failure_retry_and_close_cancel(viewer_app, monkeypatch, tmp_path):
    from pathlib import Path
    from appPlugins.ToolPCBViewer import PDF3DWriter
    path = tmp_path / 'existing.pdf'
    path.write_bytes(b'Original destination')
    tool, app, qapp = _export_tool(viewer_app, monkeypatch, path)
    original_write = PDF3DWriter.write
    def fail_write(destination, *args, **kwargs):
        Path(destination).write_bytes(b'Partial PDF')
        raise OSError('PDF output failed')
    monkeypatch.setattr(PDF3DWriter, 'write', fail_write)
    tool.on_export_pdf()
    qapp.processEvents()
    assert path.read_bytes() == b'Original destination'
    assert set(tmp_path.iterdir()) == {path}
    assert any('PDF output failed' in message for message in app.messages) or 'PDF output failed' in tool.ui.status.text()
    assert tool.ui.export_pdf_button.isEnabled()
    monkeypatch.setattr(PDF3DWriter, 'write', original_write)
    tool.on_export_pdf()
    qapp.processEvents()
    assert path.read_bytes().startswith(b'%PDF-1.7')
    assert set(tmp_path.iterdir()) == {path}
    saved = path.read_bytes()
    tasks = []
    app.worker_task.disconnect()
    app.worker_task.connect(tasks.append)
    tool.on_export_pdf()
    tool.on_plugin_cleanup()
    job, = tasks
    job['fcn'](*job['params'])
    qapp.processEvents()
    assert path.read_bytes() == saved
    assert set(tmp_path.iterdir()) == {path}
    assert tool.view is None


def _u3d_cube(name='Cube', offset=(0, 0, 0), color=(0.8, 0.4, 0.2)):
    positions = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                          (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], dtype=float)
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return dict(name=name, positions=positions + offset, faces=faces, color=color)


@pytest.mark.parametrize('explicit_normals', [False, True])
def test_u3d_writer_preserves_geometry_materials_and_scene_links(explicit_normals):
    from pathlib import Path
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene
    from tests.u3d_reader import read_u3d

    if not explicit_normals:
        golden = (Path(__file__).parent / 'fixtures/u3d_acrobat_cube.u3d').read_bytes()
        assert write_scene([_u3d_cube(name='Orange cube')]) == golden
    meshes = [_u3d_cube(), _u3d_cube('Blue cube', (3, -2, 1), (0.1, 0.2, 0.9))]
    if explicit_normals:
        for mesh in meshes:
            triangles = mesh['positions'][mesh['faces']]
            normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            mesh['normals'] = normals / np.linalg.norm(normals, axis=1)[:, None]
            mesh['normal_indices'] = np.repeat(np.arange(12)[:, None], 3, axis=1)
    data = write_scene(meshes)
    scene = read_u3d(data)
    assert scene['header']['profile'] == 4
    assert scene['header']['file_size'] == len(data)
    assert scene['header']['declaration_size'] < len(data)
    assert len(scene['meshes']) == 2
    for original in meshes:
        model = scene['nodes'][original['name']]
        assert model['kind'] == 'model' and model['visibility'] == 3
        assert scene['nodes'][model['parents'][0]['name']]['kind'] == 'group'
        mesh = scene['meshes'][model['resource']]
        assert np.allclose(mesh['positions'], original['positions'])
        assert np.array_equal(mesh['faces'], original['faces'])
        triangles = np.array(mesh['positions'])[mesh['faces']]
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normals = np.array(mesh['normals'])[mesh['normal_indices']]
        assert np.allclose(normals, (cross / np.linalg.norm(cross, axis=1)[:, None])[:, None])
        shading, = scene['chains'][(0, original['name'])]['shading']
        shader = scene['shaders'][shading['shader_lists'][0][0]]
        material = scene['materials'][shader['material']]
        assert material['diffuse'] == pytest.approx(original['color'])
        assert material['opacity'] == 1
        assert shader['attributes'] == 1
    assert {light['type'] for light in scene['lights'].values()} == {0, 1}
    assert {node['resource'] for node in scene['nodes'].values() if node['kind'] == 'light'} == set(scene['lights'])


def test_u3d_writer_preserves_opacity_and_two_sided_plating(subtests):
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene
    from tests.u3d_reader import read_u3d

    meshes = []
    for name, opacity in (('Opaque', 1), ('Translucent', 0.35), ('Transparent', 0)):
        mesh = _u3d_cube(name)
        mesh['opacity'] = opacity
        meshes.append(mesh)
    meshes.append(dict(name='Plating', positions=np.array([(0, 0, 0), (0.01, 0, 0),
                                                          (0.01, 0, 1), (0, 0, 1)]),
                       faces=[(0, 1, 2), (0, 2, 3)], normals=[(0, -1, 0)],
                       normal_indices=[(0, 0, 0), (0, 0, 0)], color=(0.8, 0.4, 0.2)))
    scene = read_u3d(write_scene(meshes))
    assert scene['header']['profile'] == 4
    assert len(scene['meshes']) == len(meshes)
    for original in meshes:
        with subtests.test(mesh=original['name']):
            model = scene['nodes'][original['name']]
            assert model['visibility'] == 3
            mesh = scene['meshes'][model['resource']]
            assert np.array_equal(mesh['faces'], original['faces'])
            assert np.allclose(mesh['positions'], original['positions'])
            triangles = np.array(mesh['positions'])[mesh['faces']]
            cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            normals = np.array(mesh['normals'])[mesh['normal_indices']]
            assert np.allclose(normals, (cross / np.linalg.norm(cross, axis=1)[:, None])[:, None])
            shading, = scene['chains'][(0, original['name'])]['shading']
            assert set(mesh['shading_ids']) == {0}
            shader_name, = shading['shader_lists'][0]
            shader = scene['shaders'][shader_name]
            material = scene['materials'][shader['material']]
            assert material['diffuse'] == pytest.approx(original['color'])
            assert material['opacity'] == pytest.approx(original.get('opacity', 1))
            # ECMA-363 third ed. 9.8.3: bits 1/2/4 are lighting/alpha test/vertex colors.
            assert shader['attributes'] == 1
            assert shader['blend_function'] == 0x606


def test_u3d_writer_preserves_indices_above_u16_range():
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene
    from tests.u3d_reader import read_u3d

    face_count = 21846
    origins = np.zeros((face_count, 3))
    origins[:, 0] = 2 * (np.arange(face_count) % 150)
    origins[:, 1] = 2 * (np.arange(face_count) // 150)
    positions = (origins[:, None, :] + np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0)])).reshape(-1, 3)
    faces = np.arange(len(positions), dtype=np.uint32).reshape(-1, 3)
    normals = np.tile((0, 0, 1), (len(positions), 1))
    original = dict(name='Large mesh', positions=positions, faces=faces, normals=normals,
                    normal_indices=faces, color=(0.8, 0.4, 0.2))
    scene = read_u3d(write_scene([original]))
    model = scene['nodes']['Large mesh']
    mesh = scene['meshes'][model['resource']]
    assert scene['header']['profile'] == 4
    assert np.array_equal(mesh['positions'], positions)
    assert np.array_equal(mesh['normals'], normals)
    assert np.array_equal(mesh['faces'], faces)
    assert np.array_equal(mesh['normal_indices'], faces)
    assert np.max(mesh['faces']) == np.max(mesh['normal_indices']) == 65537


@pytest.mark.parametrize('damage', ['nonfinite', 'overflow', 'index', 'fractional', 'degenerate',
                                   'normals', 'color', 'duplicate', 'empty', 'opacity_nan',
                                   'opacity_inf', 'opacity_negative_inf', 'opacity_below_zero',
                                   'opacity_above_one', 'opacity_list', 'opacity_array'])
def test_u3d_writer_rejects_invalid_scene(damage):
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene

    mesh = _u3d_cube()
    meshes = [mesh]
    if damage in ('nonfinite', 'overflow'):
        mesh['positions'][0, 0] = np.nan if damage == 'nonfinite' else 1e100
    elif damage in ('index', 'fractional', 'degenerate'):
        mesh['faces'] = np.array(mesh['faces'], dtype=float if damage == 'fractional' else int)
        mesh['faces'][0, 0] = {'index': 100, 'fractional': 0.5, 'degenerate': 2}[damage]
    elif damage == 'normals':
        mesh.update(normals=[(0, 0, 0)], normal_indices=np.zeros((12, 3), dtype=int))
    elif damage == 'color':
        mesh['color'] = (1, -0.1, 0)
    elif damage == 'duplicate':
        meshes.append(_u3d_cube())
    elif damage.startswith('opacity_'):
        mesh['opacity'] = {'opacity_nan': np.nan, 'opacity_inf': np.inf,
                           'opacity_negative_inf': -np.inf, 'opacity_below_zero': -0.01,
                           'opacity_above_one': 1.01, 'opacity_list': [0.5],
                           'opacity_array': np.array([0.25, 0.75])}[damage]
    else:
        meshes = []
    with pytest.raises(ValueError):
        write_scene(meshes)


def _pdf3d_objects(data):
    import re
    # Follow the actual startxref and xref, not a scan for object-looking bytes in streams.
    xref = int(re.search(rb'startxref\s+(\d+)\s+%%EOF\s*$', data)[1])
    lines = data[xref:].splitlines()
    assert lines[0] == b'xref'
    first, count = map(int, lines[1].split())
    assert first == 0 and lines[2] == b'0000000000 65535 f '
    offsets = [int(line[:10]) for line in lines[3:count + 2]]
    objects = {}
    for number, (start, end) in enumerate(zip(offsets, offsets[1:] + [xref]), 1):
        prefix = ('%d 0 obj\n' % number).encode()
        assert data[start:start + len(prefix)] == prefix
        objects[number] = data[start + len(prefix):end].removesuffix(b'\nendobj\n')
    return objects


@pytest.mark.parametrize('bounds,page_size', [(((-1, -1, 0), (1, 1, 0)), (612, 792)),
                                             (((1000, -20, 2), (1200, -19, 3)), (792, 612))])
def test_pdf3d_streams_annotation_and_iso_camera_fit(tmp_path, bounds, page_size):
    import itertools
    import re
    import zlib
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene
    from appPlugins.ToolPCBViewer.PDF3DWriter import write
    from tests.u3d_reader import read_u3d
    from vispy.util.quaternion import Quaternion
    from vispy.util import transforms

    low, high = bounds
    u3d = write_scene([dict(name='Triangle', positions=[low, (high[0], low[1], low[2]),
                                                       (low[0], high[1], high[2])],
                            faces=[(0, 1, 2)], color=(1, 0, 0))])
    path = tmp_path / 'scene.pdf'
    write(path, u3d, bounds, page_size=page_size)
    data = path.read_bytes()
    assert data.startswith(b'%PDF-1.7')
    objects = _pdf3d_objects(data)
    streams = {}
    for number, obj in objects.items():
        if b'\nstream\n' not in obj:
            continue
        dictionary, stream = obj.split(b'\nstream\n', 1)
        length = int(re.search(rb'/Length\s+(\d+)', dictionary)[1])
        assert stream[length:] == b'\nendstream'
        streams[number] = (dictionary, stream[:length])
        if b'/FlateDecode' in dictionary:
            assert zlib.decompress(stream[:length]) == u3d
            assert read_u3d(zlib.decompress(stream[:length]))['meshes']
            assert b'/Subtype /U3D' in dictionary
    page, = (obj for obj in objects.values() if re.search(rb'/Type /Page\b', obj))
    media = list(map(float, re.search(rb'/MediaBox\s*\[([^]]+)\]', page)[1].split()))
    assert media == [0, 0, *page_size]
    annotation, = (obj for obj in objects.values() if b'/Subtype /3D' in obj)
    assert b'/A /PO' in annotation
    artwork = int(re.search(rb'/3DD\s+(\d+) 0 R', annotation)[1])
    assert zlib.decompress(streams[artwork][1]) == u3d
    view_number = int(re.search(rb'/3DV\s+(\d+) 0 R', annotation)[1])
    assert ('/DV %d 0 R' % view_number).encode() in streams[artwork][0]
    appearance = int(re.search(rb'/AP\s*<<\s*/N\s+(\d+) 0 R', annotation)[1])
    poster_dict, poster = streams[appearance]
    assert b'/Subtype /Form' in poster_dict and b'/Resources <<' in poster_dict
    assert b' re f' in poster
    bbox = list(map(float, re.search(rb'/BBox\s*\[([^]]+)\]', poster_dict)[1].split()))
    assert bbox == media
    grey = re.fullmatch(rb'\s*q\s+([\d.]+)\s+g\s+([\d.]+)\s+([\d.]+)\s+'
                        rb'([\d.]+)\s+([\d.]+)\s+re\s+f\s+Q\s*', poster)
    assert grey is not None
    assert list(map(float, grey.groups())) == [0.75, 0, 0, *page_size]
    contents = re.search(rb'/Contents\s+(\d+) 0 R', page)
    assert contents is not None, 'Missing page content: fallback must paint the grey poster without annotations'
    content_dict, content = streams[int(contents[1])]
    if b'/FlateDecode' in content_dict:
        content = zlib.decompress(content)
    paint = re.fullmatch(rb'\s*q\s+/([^\s/]+)\s+Do\s+Q\s*', content)
    assert paint is not None
    resources = re.search(rb'/Resources\s*<<\s*/XObject\s*<<([^>]+)>>\s*>>', page)
    assert resources is not None
    resource = re.search(rb'/' + re.escape(paint[1]) + rb'\s+(\d+) 0 R', resources[1])
    assert resource is not None
    assert int(resource[1]) == appearance
    view, = (obj for obj in objects.values() if b'/Type /3DView' in obj)
    assert objects[view_number] == view
    assert re.search(rb'/LS\s*<<\s*/Type /3DLightingScheme\s*/Subtype /Headlamp\s*>>', view)
    scene = read_u3d(zlib.decompress(streams[artwork][1]))
    model = scene['nodes']['Triangle']
    assert model['visibility'] == 3
    mesh = scene['meshes'][model['resource']]
    assert np.array_equal(mesh['faces'], [(0, 1, 2)])
    assert np.allclose(mesh['positions'], [low, (high[0], low[1], low[2]), (low[0], high[1], high[2])])
    shading, = scene['chains'][(0, 'Triangle')]['shading']
    assert scene['shaders'][shading['shader_lists'][0][0]]['attributes'] == 1
    matrix = np.array(list(map(float, re.search(rb'/C2W\s*\[([^]]+)\]', view)[1].split()))).reshape(4, 3)
    rotation, eye = matrix[:3].T, matrix[3]
    co = float(re.search(rb'/CO\s+([\d.eE+-]+)', view)[1])
    scale = float(re.search(rb'/OS\s+([\d.eE+-]+)', view)[1])
    assert b'/Subtype /O' in view
    assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-7)
    assert np.linalg.det(rotation) == pytest.approx(-1)
    # Independent reconstruction of Camera_3D.set_view('iso') and Arcball's axis mapping.
    q = Quaternion.create_from_axis_angle(-np.degrees(np.arctan(1 / np.sqrt(2))), 1, 0, 0, degrees=True)
    q *= Quaternion.create_from_axis_angle(45, 0, 1, 0, degrees=True)
    angle, x, y, z = q.get_axis_angle()
    rotated = transforms.rotate(np.degrees(angle), (x, z, y))[:3, :3].T
    expected = rotated @ np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]])
    assert np.allclose(rotation, expected, atol=1e-7)
    center = np.mean(bounds, axis=0)
    assert eye + co * rotation[:, 2] == pytest.approx(center)
    corners = np.array(list(itertools.product(*zip(*bounds))))
    camera = (corners - eye) @ rotation
    # ISO 32000-1 13.6.5: +z goes into the page; OB /Absolute makes OS page units/world unit.
    assert b'/OB /Absolute' in view
    assert np.max(np.abs(camera[:, 0])) * scale < page_size[0] / 2
    assert np.max(np.abs(camera[:, 1])) * scale < page_size[1] / 2
    assert np.all(camera[:, 2] > 0)


def test_u3d_sdk_reference_cube():
    from pathlib import Path
    from tests.u3d_reader import read_u3d

    data = (Path(__file__).parent / 'fixtures/u3d_no_compression_cube.u3d').read_bytes()
    scene = read_u3d(data)
    assert scene['header']['profile'] == 4
    assert scene['header']['file_size'] == len(data) == 1132
    mesh, = scene['meshes'].values()
    positions = np.array(mesh['positions'])
    assert positions.shape == (8, 3)
    assert len(set(map(tuple, positions))) == 8
    assert set(positions.ravel()) == {0, 1}
    faces = np.array(mesh['faces'])
    assert faces.shape == (12, 3)
    triangles = positions[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    assert np.all(np.einsum('ij,ij->i', cross, triangles.mean(axis=1) - 0.5) > 0)
    assert np.einsum('ij,ij->i', triangles[:, 0], cross).sum() / 6 == pytest.approx(1)
    normals = np.array(mesh['normals'])
    assert set(map(tuple, normals)) == {(1, 0, 0), (-1, 0, 0), (0, 1, 0),
                                       (0, -1, 0), (0, 0, 1), (0, 0, -1)}
    corner_normals = normals[np.array(mesh['normal_indices'])]
    assert np.allclose(corner_normals, (cross / np.linalg.norm(cross, axis=1)[:, None])[:, None, :])
    model, = (node for node in scene['nodes'].values() if node['kind'] == 'model')
    assert model['resource'] == mesh['name']
    shading, = scene['chains'][(0, model['name'])]['shading']
    assert set(mesh['shading_ids']) == {0}
    shader_name, = shading['shader_lists'][0]
    shader = scene['shaders'][shader_name]
    material = scene['materials'][shader['material']]
    assert material['diffuse'] == pytest.approx((0.8, 0.4, 0.2))
    assert material['opacity'] == pytest.approx(1)


def _u3d_test_block(kind, payload):
    import struct
    return struct.pack('<III', kind, len(payload), 0) + payload + bytes(-len(payload) % 4)


def _u3d_test_string(value):
    import struct
    encoded = value.encode('utf-8')
    return struct.pack('<H', len(encoded)) + encoded


def _u3d_test_file(*blocks, units=None):
    import struct
    size = 36 if units is None else 44
    header = struct.pack('<hhIIQI', 0, 7, 4 if units is None else 12,
                         size, size + sum(map(len, blocks)), 106)
    if units is not None:
        header += struct.pack('<d', units)
    return _u3d_test_block(0x00443355, header) + b''.join(blocks)


@pytest.mark.parametrize('light_type', [0, 1])
def test_u3d_group_and_light_scene_links(light_type):
    import struct
    from tests.u3d_reader import read_u3d

    matrix = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 3, 4, 1)
    group = _u3d_test_block(0xFFFFFF21, _u3d_test_string('Board') + struct.pack('<I', 0))
    parents = struct.pack('<I', 1) + _u3d_test_string('Board') + struct.pack('<16f', *matrix)
    name = _u3d_test_string('Lumière')
    node = _u3d_test_block(0xFFFFFF23, name + parents + _u3d_test_string('Lamp'))
    # Section 9.4.3 bounding fields precede padding and the embedded block count.
    chain = name + struct.pack('<II10f', 0, 3, 0, 0, 0, 10, -1, -1, -1, 1, 1, 1)
    chain += bytes(-len(chain) % 4) + struct.pack('<I', 1) + node
    light = _u3d_test_block(0xFFFFFF51, _u3d_test_string('Lamp') + struct.pack(
        '<IB9f', 3, light_type, 0.8, 0.7, 0.6, 1, 1, 0, 0, 45, 2.5))
    scene = read_u3d(_u3d_test_file(group, _u3d_test_block(0xFFFFFF14, chain), light, units=0.001))
    assert scene['header']['units_scale'] == pytest.approx(0.001)
    assert scene['nodes']['Board']['kind'] == 'group'
    assert scene['nodes']['Board']['parents'] == []
    node = scene['nodes']['Lumière']
    assert node['parents'] == [{'name': 'Board', 'matrix': matrix}]
    resource = scene['lights'][node['resource']]
    assert resource['type'] == light_type
    assert resource['color'] == pytest.approx((0.8, 0.7, 0.6))
    assert resource['intensity'] == pytest.approx(2.5)
    assert scene['chains'][(0, 'Lumière')]['bounding_box'] == (-1, -1, -1, 1, 1, 1)


@pytest.mark.parametrize('damage', ['file_length', 'block_size', 'padding', 'compression',
                                   'position_index', 'normal_index', 'shading_index',
                                   'declaration_boundary', 'mesh_count', 'flush_word'])
def test_u3d_rejects_corrupt_reference(damage):
    import struct
    from pathlib import Path
    from tests.u3d_reader import U3DError, read_u3d

    data = bytearray((Path(__file__).parent / 'fixtures/u3d_no_compression_cube.u3d').read_bytes())
    base = data.index(struct.pack('<I', 0xFFFFFF3B))
    size = struct.unpack_from('<I', data, base + 4)[0]
    payload = base + 12
    name_size = struct.unpack_from('<H', data, payload)[0]
    counts = payload + 2 + name_size + 4
    face_count, positions, normals = struct.unpack_from('<III', data, counts)
    faces = counts + 24 + 12 * (positions + normals)
    if damage == 'file_length':
        struct.pack_into('<Q', data, 24, len(data) + 4)
    elif damage == 'block_size':
        struct.pack_into('<I', data, base + 4, size + 100)
    elif damage == 'padding':
        data[payload + size] = 1
    elif damage == 'compression':
        struct.pack_into('<I', data, 16, 0)
    elif damage == 'declaration_boundary':
        struct.pack_into('<I', data, 20, 40)
    elif damage == 'mesh_count':
        struct.pack_into('<I', data, counts, face_count + 1)
    elif damage == 'flush_word':
        data[payload + size - 1] = 1
    else:
        offset, value = {'position_index': (4, positions), 'normal_index': (8, normals),
                         'shading_index': (0, 1)}[damage]
        struct.pack_into('<I', data, faces + offset, value)
    with pytest.raises(U3DError):
        read_u3d(data)


def test_u3d_rejects_truncated_reference_even_with_adjusted_file_length():
    import struct
    from pathlib import Path
    from tests.u3d_reader import U3DError, read_u3d

    data = (Path(__file__).parent / 'fixtures/u3d_no_compression_cube.u3d').read_bytes()
    for end in range(len(data)):
        truncated = bytearray(data[:end])
        if end >= 36:
            struct.pack_into('<Q', truncated, 24, end)
        # Whole-block prefixes can be legal partial scenes (undefined links are allowed).
        if end in (36, 52, 236):
            continue
        with pytest.raises(U3DError):
            read_u3d(truncated)


def test_u3d_rejects_cycles_unknown_blocks_and_unconsumed_fields():
    import struct
    from tests.u3d_reader import U3DError, UnsupportedU3D, read_u3d

    matrix = struct.pack('<16f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    parent = struct.pack('<I', 1) + _u3d_test_string('Self') + matrix
    with pytest.raises(U3DError, match='[Cc]ycl'):
        read_u3d(_u3d_test_file(_u3d_test_block(0xFFFFFF21, _u3d_test_string('Self') + parent)))
    with pytest.raises(UnsupportedU3D, match='[Uu]nsupported'):
        read_u3d(_u3d_test_file(_u3d_test_block(0xFFFFFF99, _u3d_test_string('Unknown'))))
    payload = _u3d_test_string('Group') + struct.pack('<II', 0, 123)
    with pytest.raises(U3DError, match='[Uu]nconsumed'):
        read_u3d(_u3d_test_file(_u3d_test_block(0xFFFFFF21, payload)))


def test_outline_region_fixture_preserves_gerber_clear_region():
    from pathlib import Path
    from tests.helpers_objects import make_app
    from appParsers.ParseGerber import Gerber
    grb = Gerber(make_app())
    path = Path(__file__).parent / 'fixtures/cutout/outline_region_cutout.gbr'
    assert grb.parse_file(str(path)) is None
    layer = build({'outline': source(grb.solid_geometry, grb.follow_geometry)})['board']
    assert not layer.geometry.covers(Point(25, 15))
    assert layer.geometry.area == pytest.approx(shapely.union_all(grb.solid_geometry).area)


def test_pcb_export_outline_mask_caps_preserve_area_and_rectangular_openings():
    from pathlib import Path
    from tests.helpers_objects import make_app
    from tests.u3d_reader import read_u3d
    from appParsers.ParseGerber import Gerber
    from appPlugins.ToolPCBViewer.PCBViewerExport import export_meshes
    from appPlugins.ToolPCBViewer.U3DWriter import write_scene

    grb = Gerber(make_app())
    path = Path(__file__).parent / 'fixtures/cutout/outline_region_cutout.gbr'
    assert grb.parse_file(str(path)) is None
    bounds = shapely.union_all(grb.solid_geometry).bounds
    xmin, ymin, xmax, ymax = bounds
    width, height = xmax - xmin, ymax - ymin
    # Fractional bounds leave sub-F32 edges where a mask opening meets the cutout.
    openings = [box(xmin + width * .15, ymin + height * .25, xmin + width * .25, ymin + height * .35),
                box(xmin + width * .45, ymin + height * .45, xmin + width * .55, ymin + height * .55)]
    layers = build({'outline': source(grb.solid_geometry, grb.follow_geometry),
                    'drills_pth_1': source([Point(12.5, 7.5).buffer(.3), Point(37.5, 22.5).buffer(.3)]),
                    'soldermask_top': source(openings),
                    'soldermask_bottom': source(openings)})
    scene = read_u3d(write_scene(export_meshes(tuple(layers.values()), _export_material(), 'MM')))
    for name in ('soldermask_top', 'soldermask_bottom'):
        layer = layers[name]
        decoded = scene['meshes'][scene['nodes'][name]['resource']]
        positions = np.asarray(decoded['positions'], dtype=float)
        triangles = positions[np.asarray(decoded['faces'])]
        assert np.ptp(positions[:, 2]) == pytest.approx(0.1)
        for z in (positions[:, 2].min(), positions[:, 2].max()):
            cap = triangles[np.all(triangles[:, :, 2] == z, axis=1), :, :2]
            a, b = cap[:, 1] - cap[:, 0], cap[:, 2] - cap[:, 0]
            area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2
            assert area == pytest.approx(layer.geometry.area, rel=1e-6)
            geometry = shapely.union_all([Polygon(points + layers['board'].origin) for points in cap])
            assert geometry.symmetric_difference(layer.geometry).area < 1e-5
            for opening in openings:
                assert not geometry.covers(opening.centroid)
                assert geometry.intersection(opening).area < 1e-5


def test_dense_panel_mesh_keeps_every_board_and_hole():
    from appPlugins.ToolPCBViewer.PCBViewerGen import extrude_mesh
    panels = []
    for offset in (0, 30, 60, 90):
        points = shapely.points([(offset + x, y) for x in range(2, 18, 2) for y in range(2, 18, 2)])
        holes = shapely.union_all(shapely.buffer(points, 0.4, quad_segs=32))
        panels.append(box(offset, 0, offset + 20, 20).difference(holes))
    geometry = shapely.MultiPolygon(panels)
    vertices, faces, _origin = extrude_mesh(geometry)
    assert cap_area(SimpleNamespace(vertices=vertices, faces=faces)) == pytest.approx(geometry.area, rel=1e-6)


def test_changed_object_supersedes_inflight_preview_and_close_is_safe(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    app, qapp = viewer_app
    tasks = []
    app.worker_task.disconnect()
    app.worker_task.connect(tasks.append)
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    obj = app.collection.objects[0]
    tool.run(source=obj)
    obj.solid_geometry = [box(1100, 2100, 1120, 2110)]
    app.app_obj.object_changed.emit(obj)
    # Finish the old request after the new geometry was selected.
    old = tasks.pop(0)
    old['fcn'](*old['params'])
    qapp.processEvents()
    tool._timer.stop()
    tool.on_redraw()
    latest = tasks.pop(0)
    latest['fcn'](*latest['params'])
    qapp.processEvents()
    assert tool.view.layers['copper_top'][0].geometry.bounds == (1100, 2100, 1120, 2110)
    tool.on_selection_changed()
    tool._timer.stop()
    tool.on_redraw()
    tool.on_plugin_cleanup()
    closing = tasks.pop(0)
    closing['fcn'](*closing['params'])
    qapp.processEvents()
    assert tool.view is None


def test_physical_layer_stack_and_fit_with_existing_sender(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewerView import PCBView
    app, _qapp = viewer_app
    canvas = app.plotcanvas3d
    view = PCBView(canvas)
    shape = source([box(1000, 2000, 1020, 2010)])
    for layer in build({key: shape for key in ('outline', 'copper_top', 'copper_bottom',
                                              'soldermask_top', 'soldermask_bottom',
                                              'overlay_top', 'overlay_bottom')}).values():
        view.set_layer(layer)
    # Mask openings cover the board in this example, but their physical stack positions remain defined.
    values = {'board_thickness': 0.8, 'copper_thickness': 18, 'mask_thickness': 100, 'board_alpha': 255}
    values.update({key + '_color': '#FF0000FF' for key in ('board', 'copper', 'soldermask', 'overlay')})
    view.update_style(values, 'MM')
    top = view.layers['overlay_top'][1][0].transform.map([(0, 0, 0), (0, 0, 1)])[:, 2]
    bottom = view.layers['overlay_bottom'][1][0].transform.map([(0, 0, 0), (0, 0, 1)])[:, 2]
    assert top == pytest.approx((0.918, 0.919))
    assert bottom == pytest.approx((-0.119, -0.118))

    def fit_hidden_sender():
        canvas.view.camera.center = (0, 0, 0)

    app.grbl_sender_tool = SimpleNamespace(view3d=SimpleNamespace(canvas=canvas), on_fit_view=fit_hidden_sender)
    canvas.on_fit_view()
    assert canvas.view.camera.center[0] > 1000
    view.clear()


def test_transparent_offcenter_overlay_stays_above_opaque_board(viewer_app):
    from appPlugins.ToolPCBViewer.PCBViewerView import PCBView
    app, _qapp = viewer_app
    canvas = app.plotcanvas3d
    view = PCBView(canvas)
    for layer in build({'outline': source([box(0, 0, 20, 10)]),
                        'overlay_top': source([box(1, 1, 3, 3)])}).values():
        view.set_layer(layer)
    view.update_style({'board_thickness': 0.8, 'copper_thickness': 18, 'mask_thickness': 100,
                       'board_alpha': 255, 'board_color': '#0000FFFF', 'overlay_color': '#FF000080'}, 'MM')
    canvas.fit_view()
    image = canvas.render()[:, :, :3].astype(int)
    blended = (image[:, :, 0] > 40) & (image[:, :, 2] > 40) & (image[:, :, 1] < 30)
    assert np.count_nonzero(blended) > 100
    view.clear()


def test_filename_aliases_select_the_board_stack(viewer_app, subtests):
    from PyQt6 import QtGui
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    app, qapp = viewer_app
    aliases = {
        'outline': ('GKO', 'edge.cuts', 'keep out', 'KEEP_-OUT'),
        'copper_top': ('GTL', 'top copper', 'top layer', 'f.cu'),
        'copper_bottom': ('GBL', 'bottom copper', 'bottom layer', 'b.cu'),
        'overlay_top': ('GTO', 'top overlay', 'f.silks', 'F.Silkscreen'),
        'overlay_bottom': ('GBO', 'bottom overlay', 'b.silks', 'B.Silkscreen'),
        'soldermask_top': ('GTS', 'top soldermask', 'f.mask', 'TOP SOLDER'),
        'soldermask_bottom': ('GBS', 'bottom soldermask', 'b.mask', 'Bottom Solder'),
    }
    original = app.collection.objects[:]
    root = app.collection.item(0)
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    for variant in range(4):
        root.removeRows(1, root.rowCount() - 1)
        app.collection.objects = original[:]
        expected = {}
        for role, names in aliases.items():
            name = 'board-' + names[variant] + '.gbr'
            expected[role] = name
            obj = SimpleNamespace(kind='gerber', obj_options={'name': name}, units='MM',
                                  solid_geometry=[box(1000, 2000, 1020, 2010)], follow_geometry=[])
            app.collection.objects.append(obj)
            root.appendRow(QtGui.QStandardItem(name))
        tool.run(source=original[0])
        qapp.processEvents()
        for role, name in expected.items():
            with subtests.test(role=role, name=name):
                assert tool.ui.combos[role].currentText() == name


def test_all_drill_files_cut_board_and_pth_walls_use_copper_color(viewer_app):
    from PyQt6 import QtGui
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    from vispy.color import Color
    app, qapp = viewer_app
    for name, x in (('board-pTh', 1010), ('mounting', 1015)):
        app.collection.objects.append(SimpleNamespace(
            kind='excellon', obj_options={'name': name}, units='MM',
            solid_geometry=[Point(x, 2005).buffer(0.5)], tools={}))
        app.collection.item(1).appendRow(QtGui.QStandardItem(name))
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    tool.run(source=app.collection.objects[0])
    qapp.processEvents()
    assert [tool.ui.combos[key].currentText() for key in ('drills', 'drills_2', 'drills_3')] == [
        'drill', 'board-pTh', 'mounting']
    for key in ('board', 'copper_top', 'copper_bottom', 'overlay_top', 'overlay_bottom'):
        for x in (1005, 1010, 1015):
            assert not tool.view.layers[key][0].geometry.covers(Point(x, 2005))
    barrel, meshes = tool.view.layers['plating']
    assert barrel.geometry.bounds == pytest.approx((1009.5, 2004.5, 1010.5, 2005.5))
    triangles = barrel.vertices[barrel.faces]
    assert np.all(np.ptp(triangles[:, :, 2], axis=1) > 0)
    tool.ui.colors['copper'].set_value('#AA5500FF')
    tool._timer.stop()
    tool.on_redraw()
    for mesh in meshes:
        assert mesh.color.rgba == pytest.approx(Color('#AA5500FF').rgba)
        z = mesh.transform.map([(0, 0, 0), (0, 0, 1)])[:, 2]
        assert z == pytest.approx((-0.018, 0.818))
    # With artwork hidden, only the plated bore should contribute red pixels.
    for key in ('copper_top', 'copper_bottom', 'overlay_top', 'overlay_bottom'):
        tool.ui.combos[key].setCurrentIndex(-1)
    tool.ui.colors['copper'].set_value('#FF0000FF')
    tool.ui.colors['board'].set_value('#0000FFFF')
    tool._timer.stop()
    tool.on_redraw()
    qapp.processEvents()
    canvas = app.plotcanvas3d
    canvas.fit_view()
    image = canvas.render()[:, :, :3].astype(int)
    centers = canvas.view.camera._scene_transform.map([(x, 2005, 0.4, 1) for x in (1005, 1010, 1015)])
    for index, point in enumerate(centers):
        pixels = np.array(image.shape[1::-1]) / canvas.size
        x, y = np.rint(point[:2] / point[3] * pixels).astype(int)
        radius = int(np.ceil(15 * max(pixels)))
        region = image[y - radius:y + radius + 1, x - radius:x + radius + 1]
        red = (region[:, :, 0] - region[:, :, 1:].max(axis=2)) > 80
        assert (np.count_nonzero(red) > 5 if index == 1 else not np.any(red)), (
            index, np.count_nonzero(red), image.shape, canvas.size, point)
    tool.ui.combos['drills_2'].setCurrentIndex(-1)
    tool._timer.stop()
    tool.on_redraw()
    qapp.processEvents()
    assert 'plating' not in tool.view.layers
    assert tool.view.layers['board'][0].geometry.covers(Point(1010, 2005))


@pytest.mark.parametrize('closed', ['plugin', 'plot'])
def test_closing_either_pcb_tab_closes_its_partner_and_can_reopen(viewer_app, closed):
    from PyQt6 import QtWidgets
    from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
    app, qapp = viewer_app
    tasks = []
    app.worker_task.disconnect()
    app.worker_task.connect(tasks.append)
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    app.app_plugins = [tool]
    tool.run(source=app.collection.objects[0])
    canvas, plot_tab, plugin_tab = app.plotcanvas3d, app.area_3d_tab, app.ui.plugin_tab
    unrelated = QtWidgets.QWidget(app.ui.plot_tab_area)
    unrelated.setObjectName('other_preview')
    index = app.ui.plot_tab_area.addTab(unrelated, 'Other preview')
    app.ui.plot_tab_area.closeTab(index)
    assert app.plotcanvas3d is canvas
    assert app.ui.notebook.indexOf(plugin_tab) >= 0
    if closed == 'plugin':
        app.on_notebook_closed()
    else:
        app.ui.plot_tab_area.closeTab(app.ui.plot_tab_area.indexOf(plot_tab))
    assert app.ui.plot_tab_area.indexOf(plot_tab) == -1
    assert app.ui.notebook.indexOf(plugin_tab) == -1
    assert app.plotcanvas3d is None
    assert tool.view is None
    pending = tasks.pop(0)
    pending['fcn'](*pending['params'])
    qapp.processEvents()
    assert tool.view is None
    tool.run(source=app.collection.objects[0])
    assert app.plotcanvas3d is not None and app.plotcanvas3d is not canvas
    pending = tasks.pop(0)
    pending['fcn'](*pending['params'])
    qapp.processEvents()
    assert tool.view.layers['board'][1]
