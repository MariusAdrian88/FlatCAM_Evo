"""Cached VisPy meshes. Material and thickness changes only update uniforms and transforms."""

import numpy as np
from PyQt6 import sip
from vispy import scene
from vispy.color import Color
from vispy.geometry import MeshData


class PCBView:
    def __init__(self, canvas):
        self.canvas = canvas
        self.layers = {}
        self._depth = canvas.view.camera.depth_value
        self._saved = [(node, node.visible) for node in canvas.view.scene.children
                       if node is not canvas.view.camera]
        for node, _visible in self._saved:
            node.visible = False
        canvas.grid_lines_enabled = False
        self.root = scene.Node(parent=canvas.view.scene)
        canvas.events.draw.connect(self.sort_layers, position='first')
        canvas.view.camera.events.transform_change.connect(self.sort_layers)
        canvas.view.camera.set_view('iso')

    @property
    def alive(self):
        return not sip.isdeleted(self.canvas.native)

    def set_layer(self, layer):
        previous = self.layers.get(layer.name)
        if previous is not None:
            if previous[0].vertices is layer.vertices:
                for mesh in previous[1]:
                    mesh.visible = True
                return
            for mesh in previous[1]:
                mesh.parent = None
        meshes = []
        if len(layer.faces):
            data = MeshData(vertices=layer.vertices, faces=layer.faces)
            # Back faces before front faces make the alpha of a closed material work from either side.
            for cull in ('front', 'back'):
                mesh = scene.visuals.Mesh(meshdata=data, shading='flat', parent=self.root)
                mesh.set_gl_state('translucent', depth_test=True, depth_func='lequal',
                                  cull_face=cull, depth_mask=False)
                mesh.shading_filter.ambient_light = (0.65, 0.65, 0.65, 1)
                meshes.append(mesh)
        self.layers[layer.name] = layer, meshes

    def begin_build(self, names):
        for name in list(self.layers):
            _layer, meshes = self.layers[name]
            for mesh in meshes:
                mesh.visible = False
                if name not in names:
                    mesh.parent = None
            if name not in names:
                del self.layers[name]

    def update_style(self, values, units):
        factor = 1 / 25.4 if units.upper() == 'IN' else 1
        board = values['board_thickness'] * factor
        copper = values['copper_thickness'] / 1000 * factor
        mask = values['mask_thickness'] / 1000 * factor
        ink = 0.001 * factor
        heights = {'board': (0, board)}
        if 'plating' in self.layers:
            bottom = copper if 'copper_bottom' in self.layers else 0
            top = copper if 'copper_top' in self.layers else 0
            heights['plating'] = (-bottom, board + bottom + top)
        for side in ('top', 'bottom'):
            top = side == 'top'
            z = board if top else 0
            for name, thickness in (('copper', copper), ('soldermask', mask), ('overlay', ink)):
                key = name + '_' + side
                if key not in self.layers:
                    continue
                heights[key] = (z if top else z - thickness, thickness)
                z += thickness if top else -thickness
        for name, (layer, meshes) in self.layers.items():
            color_key = 'copper' if name == 'plating' else name.split('_')[0]
            text = values[color_key + '_color']
            if len(text) not in (7, 9) or not text.startswith('#'):
                raise ValueError('Use a color in #RRGGBB or #RRGGBBAA format.')
            color = Color(text).rgba.copy()
            if name == 'board':
                color[3] = values['board_alpha'] / 255
            z, thickness = heights[name]
            for mesh in meshes:
                mesh.color = color
                mesh.transform = scene.STTransform(scale=(1, 1, thickness), translate=(*layer.origin, z))
                mesh.update_gl_state(depth_mask=bool(color[3] >= 1))
        if 'board' in self.layers:
            bounds = self.layers['board'][0].geometry.bounds
            if np.isfinite(bounds).all():
                low = min(z for z, _height in heights.values())
                high = max(z + height for z, height in heights.values())
                self.canvas.content_bounds = ((bounds[0], bounds[1], low), (bounds[2], bounds[3], high))
                # The machine camera's million-unit depth range loses micrometre layer separation.
                self.canvas.view.camera.depth_value = max(bounds[2] - bounds[0], bounds[3] - bounds[1], high - low) * 4
        self.sort_layers()
        self.canvas.update()

    def sort_layers(self, event=None):
        if not self.layers:
            return
        camera = self.canvas.view.camera
        reference = self.layers['board'][0].origin if 'board' in self.layers else (0, 0)
        layers = []
        for name, (_layer, meshes) in self.layers.items():
            if not meshes:
                continue
            z = meshes[0].transform.map((0, 0, 0.5))[2]
            # Parallel PCB layers share a depth reference, even when their artwork is off-center.
            depth = camera.transform.imap((*reference, z, 1))[2]
            layers.append((depth, name, meshes))
        changed = False
        for index, (_depth, _name, meshes) in enumerate(sorted(layers, key=lambda item: item[0])):
            for offset, mesh in enumerate(meshes):
                order = index * 2 + offset
                if mesh.order != order:
                    mesh.order = order
                    changed = True
        if changed:
            # VisPy's order setter redraws, but doesn't invalidate its cached traversal.
            self.canvas.scene.events.children_change()

    def clear(self):
        if not self.alive:
            self.layers.clear()
            return
        self.canvas.events.draw.disconnect(self.sort_layers)
        self.canvas.view.camera.events.transform_change.disconnect(self.sort_layers)
        self.root.parent = None
        self.layers.clear()
        self.canvas.content_bounds = None
        self.canvas.view.camera.depth_value = self._depth
        for node, visible in self._saved:
            node.visible = visible
        self.canvas.grid_lines_enabled = self.canvas.grid.visible
        self.canvas.update()
