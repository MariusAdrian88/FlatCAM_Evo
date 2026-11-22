"""VisPy substrate, copper cap, tool sweeps and centerlines on the shared canvas."""

from gettext import gettext as _
from html import escape
from types import SimpleNamespace

import numpy as np
from vispy import scene
from vispy.color import Color

from appPlugins.ToolPCBViewer.PCBViewerView import PCBView


PALETTE = ('#FBBD45', '#36C5F0', '#FF667D', '#BD8CFF', '#70E0A1', '#FF9859')


class GeometryView(PCBView):
    def __init__(self, canvas):
        self._camera_reference = canvas.camera_reference
        self._content_bounds = canvas.content_bounds
        self._bounds_callback = canvas.geometry_center
        self._grid_enabled = canvas.grid_lines_enabled
        self._board = None
        self._cuts = None
        self._cut_visuals = []
        self._coverage_legend = ''
        super().__init__(canvas)
        canvas.geometry_center = self.center

    def center(self):
        bounds = self.canvas.content_bounds
        if bounds is None:
            return self._bounds_callback()
        return tuple((a + b) / 2 for a, b in zip(*bounds))

    def show_meshes(self, board, cuts, ui, fit):
        if board is not self._board:
            self.set_layer_from_board(board)
        if cuts is not self._cuts:
            for _layer, mesh, line in self._cut_visuals:
                mesh.parent = line.parent = None
            self._cut_visuals.clear()
            legend = []
            for index, layer in enumerate(cuts):
                color = PALETTE[index % len(PALETTE)]
                mesh = scene.visuals.Mesh(vertices=layer.vertices, faces=layer.faces, color=color, parent=self.root)
                mesh.set_gl_state('opaque', depth_test=True, depth_func='lequal', cull_face=False)
                line = scene.visuals.Line(pos=np.column_stack((layer.lines, np.zeros(len(layer.lines)))),
                                          connect='segments', color='#25232A', parent=self.root)
                line.set_gl_state('opaque', depth_test=True, depth_func='lequal')
                if len(layer.lines):
                    line.order = 200
                mesh.order = 100 + index
                self._cut_visuals.append((layer, mesh, line))
                legend.append(f'<span style="color:{color}">●</span> {escape(str(layer.tool))}')
            self._coverage_legend = ' &nbsp; '.join(legend)
            self._cuts = cuts
        material = ui.mode.get_value() == 'material'
        thickness = ui.thickness.get_value()
        color = Color(ui.color.get_value()[:7]).rgba
        color[3] = ui.opacity.get_value() / 255
        unit_factor = 1 / 25.4 if ui.units.upper() == 'IN' else 1
        copper_thickness = ui.copper_thickness.get_value() / 1000 * unit_factor
        for mesh in self.layers['board'][1]:
            mesh.color = color
            mesh.transform = scene.STTransform(scale=(1, 1, thickness), translate=(*board.origin, -thickness))
            mesh.visible = color[3] > 0
            # The convex board is drawn back faces first; depth writes are safe
            # even with alpha and leave depth clearing enabled for the next draw.
            mesh.update_gl_state(depth_mask=True)
        bounds = np.asarray([layer.geometry.bounds for layer in (board, *cuts)])
        xmin, ymin = bounds[:, :2].min(axis=0)
        xmax, ymax = bounds[:, 2:].max(axis=0)
        copper_top = copper_thickness if material else 0
        span = max(xmax - xmin, ymax - ymin, thickness + copper_top)
        epsilon = span * 1e-6
        copper_color = Color(ui.copper_color.get_value()).rgba
        for mesh in self.layers['copper'][1]:
            mesh.visible = material and copper_color[3] > 0
            mesh.color = copper_color
            # The copper cap sits on the substrate; its walls show the channel depth.
            mesh.transform = scene.STTransform(scale=(1, 1, copper_thickness),
                                              translate=(*board.copper.origin, 0))
        if material:
            ui.legend.setText(' &nbsp; '.join((
                f'<span style="color:{ui.copper_color.get_value()[:7]}">●</span> {_("Remaining copper")}',
                f'<span style="color:{ui.color.get_value()[:7]}">●</span> {_("Exposed substrate")}')))
        else:
            ui.legend.setText(self._coverage_legend)
        for index, (layer, mesh, line) in enumerate(self._cut_visuals):
            mesh.visible = not material
            mesh.transform = scene.STTransform(scale=(1, 1, 0), translate=(*layer.origin, epsilon * (index + 1)))
            line.transform = scene.STTransform(translate=(*layer.origin, epsilon * (len(cuts) + 1)))
            line.visible = bool(len(layer.lines) and ui.centerlines.get_value())
        self.canvas.content_bounds = ((xmin, ymin, -thickness), (xmax, ymax, copper_top))
        self.canvas.view.camera.depth_value = span * 4
        self.canvas.camera_reference = ui.camera_reference.get_value()
        self._invalidate()
        self.sort_layers()
        if fit:
            self.canvas.fit_view()
        self.canvas.update()

    def set_layer_from_board(self, board):
        self.set_layer(SimpleNamespace(name='board', vertices=board.vertices, faces=board.faces, origin=board.origin))
        copper = board.copper
        previous = self.layers.get('copper')
        if previous is not None:
            for mesh in previous[1]:
                mesh.parent = None
        meshes = []
        if len(copper.faces):
            mesh = scene.visuals.Mesh(vertices=copper.vertices, faces=copper.faces, shading='flat', parent=self.root)
            mesh.set_gl_state('translucent', depth_test=True, depth_func='lequal', cull_face=False, depth_mask=True)
            # Flat lighting lets the cap and the extruded channel walls read as different tones.
            mesh.shading_filter.ambient_light = (0.65, 0.65, 0.65, 1)
            meshes.append(mesh)
        self.layers['copper'] = copper, meshes
        self._board = board

    def clear(self):
        super().clear()
        self._cut_visuals.clear()
        self._board = self._cuts = None
        if self.alive:
            self.canvas.geometry_center = self._bounds_callback
            self.canvas.camera_reference = self._camera_reference
            self.canvas.content_bounds = self._content_bounds
            self.canvas.grid_lines_enabled = self._grid_enabled
