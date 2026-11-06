"""End-to-end PCB preview timings, including geometry, GPU upload, shaders, drawing and pixel readback.

Run: .venv/Scripts/python.exe tests/benchmark_pcb_viewer.py
Parsing is reported separately: the viewer starts with already-loaded project objects.
"""

import json
import sys
from copy import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: E402,F401 -- isolate user settings and select the native OpenGL backend
sys.excepthook = sys.__excepthook__
import numpy as np
import shapely
from PyQt6 import QtGui
from vispy import gloo

from tests.test_pcb_viewer import viewer_app
from tests.helpers_objects import make_app
from appParsers.ParseGerber import Gerber
from appParsers.ParseExcellon import Excellon
from appPlugins.ToolPCBViewer.PCBViewer import ToolPCBViewer
from appPlugins.ToolPCBViewer.PCBViewerGen import PCBGeometry

FIXTURES = ROOT / 'tests/fixtures'
KICAD = 'kicad/Gerbers/Easyduino_RP2040-'
PROTEUS = 'Proteus/TP1_G1 - PCB3-Wireless - CADCAM '
CASES = {
    'KiCad RP2040': {
        'outline': KICAD + 'Edge_Cuts.gm1', 'copper_top': KICAD + 'F_Cu.gtl',
        'copper_bottom': KICAD + 'B_Cu.gbl', 'soldermask_top': KICAD + 'F_Mask.gts',
        'soldermask_bottom': KICAD + 'B_Mask.gbs', 'overlay_top': KICAD + 'F_Silkscreen.gto',
        'overlay_bottom': KICAD + 'B_Silkscreen.gbo', 'drills': 'kicad/Gerbers/Easyduino_RP2040.drl'},
    'Proteus wireless': {
        'outline': PROTEUS + 'Profile.GBR', 'copper_top': PROTEUS + 'Top Copper.GBR',
        'copper_bottom': PROTEUS + 'Bottom Copper.GBR', 'soldermask_top': PROTEUS + 'Top Solder Resist.GBR',
        'soldermask_bottom': PROTEUS + 'Bottom Solder Resist.GBR', 'overlay_top': PROTEUS + 'Top Silk Screen.GBR',
        'drills': PROTEUS + 'Drill.DRL'},
    'Cutout region with hole': {
        'outline': 'cutout/outline_region_cutout.gbr', 'copper_top': 'cutout/copper_clearance.gbr'},
}


def load_case(paths):
    app = make_app()
    objects = {}
    for role, path in paths.items():
        obj = Excellon(app) if role == 'drills' else Gerber(app)
        assert obj.parse_file(str(FIXTURES / path)) is None, path
        if role == 'drills':
            assert obj.create_geometry() is None
        obj.kind = 'excellon' if role == 'drills' else 'gerber'
        obj.obj_options = {'name': role}
        objects[role] = obj
    return objects


def complete_frame(app, qapp, tool):
    deadline = perf_counter() + 60
    while tool._running or tool._pending or tool._timer.isActive():
        qapp.processEvents()
        if perf_counter() >= deadline:
            raise RuntimeError('PCB viewer benchmark timed out.')
        sleep(0.001)
    qapp.processEvents()
    assert not app.messages, app.messages
    image = app.plotcanvas3d.render()
    assert image.shape[0] > 0
    return image


def benchmark(name, objects):
    fixture = viewer_app.__wrapped__()
    app, qapp = next(fixture)
    # Include opening a new Plot 3D canvas in the initial-button measurement.
    from appGUI.PlotCanvas3d import PlotCanvas3d
    old_canvas = app.plotcanvas3d
    app.area_3d_tab.layout().removeWidget(old_canvas.native)
    old_canvas.close()
    old_canvas.native.deleteLater()
    qapp.processEvents()
    app.plotcanvas3d = None

    def open_canvas():
        if app.plotcanvas3d is None:
            app.plotcanvas3d = PlotCanvas3d(app.area_3d_tab.layout(), app)
            app.plotcanvas3d.size = (800, 600)
            app.plotcanvas3d.central_widget.size = (800, 600)
            app.plotcanvas3d.view.size = (800, 600)

    app.on_3d_area = open_canvas
    tool = app.pcb_viewer_tool = ToolPCBViewer(app)
    app.collection.clear()
    app.collection.objects = list(objects.values())
    for kind in ('gerber', 'excellon'):
        item = QtGui.QStandardItem(kind.title())
        app.collection.appendRow(item)
        for obj in objects.values():
            if obj.kind == kind:
                item.appendRow(QtGui.QStandardItem(obj.obj_options['name']))
    app.worker_task.disconnect()
    with ThreadPoolExecutor(max_workers=1) as executor:
        app.worker_task.connect(lambda task: executor.submit(task['fcn'], *task['params']))
        start = perf_counter()
        tool.run(source=objects['copper_top'])
        initial = complete_frame(app, qapp, tool)
        initial_seconds = perf_counter() - start
        assert np.ptp(initial[::16, ::16, :3]) > 20
        tool._engine = PCBGeometry()
        start = perf_counter()
        for role, combo in tool.ui.combos.items():
            combo.setCurrentIndex(combo.findText(role))
        complete_frame(app, qapp, tool)
        selected_seconds = perf_counter() - start
        triangles = sum(len(layer.faces) for layer, _meshes in tool.view.layers.values())
        timings = []
        for thickness in (1.2, 0.6, 0.8):
            start = perf_counter()
            tool.ui.board_thickness.set_value(thickness)
            complete_frame(app, qapp, tool)
            timings.append(perf_counter() - start)
        start = perf_counter()
        tool.on_selection_changed()
        complete_frame(app, qapp, tool)
        cached_seconds = perf_counter() - start
        app.plotcanvas3d.set_current()
        renderer = gloo.gl.glGetParameter(gloo.gl.GL_RENDERER)
        result = {'case': name, 'initial_button_s': round(initial_seconds, 4),
                  'full_stack_s': round(selected_seconds, 4), 'cached_rebuild_s': round(cached_seconds, 4),
                  'thickness_frame_s': [round(t, 4) for t in timings], 'triangles': triangles,
                  'frame_pixels': list(initial.shape[:2]), 'renderer': str(renderer)}
        tool.on_plugin_cleanup()
        print(json.dumps(result, separators=(',', ':')), flush=True)
    fixture.close()


def panel(objects):
    outline = objects['outline']
    xmin, ymin, xmax, ymax = shapely.total_bounds(outline.solid_geometry)
    offsets = ((0, 0), (xmax - xmin + 3, 0), (0, ymax - ymin + 3), (xmax - xmin + 3, ymax - ymin + 3))
    result = {}
    for role, obj in objects.items():
        tiled = copy(obj)
        tiled.solid_geometry = [shapely.transform(obj.solid_geometry, lambda xy, delta=offset: xy + delta)
                                for offset in offsets]
        tiled.follow_geometry = [shapely.transform(getattr(obj, 'follow_geometry', ()),
                                                   lambda xy, delta=offset: xy + delta) for offset in offsets]
        result[role] = tiled
    return result


if __name__ == '__main__':
    # Initialize Qt before constructing parsers that use app GUI services.
    from PyQt6 import QtWidgets
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    for name, paths in CASES.items():
        start = perf_counter()
        objects = load_case(paths)
        print(json.dumps({'case': name, 'parse_s': round(perf_counter() - start, 4)}, separators=(',', ':')), flush=True)
        benchmark(name, objects)
        if name == 'KiCad RP2040':
            benchmark('KiCad RP2040 2×2 panel', panel(objects))
