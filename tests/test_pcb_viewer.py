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
