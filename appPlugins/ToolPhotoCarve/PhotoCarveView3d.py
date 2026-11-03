"""GUI-thread-only Photo Carve visuals in the existing PlotCanvas3d scene."""

import math

import numpy as np
from vispy import scene
from vispy.visuals.transforms import STTransform


class PhotoCarveView3d:
    def __init__(self, canvas):
        self.canvas = canvas
        parent = canvas.view.scene
        self.path = scene.visuals.Line(parent=parent, connect='segments', width=1.5, method='gl')
        self.marker = scene.visuals.Markers(parent=parent)
        self.label = scene.visuals.Text(parent=parent, color='black', font_size=12, anchor_x='left')
        self.mesh = None
        self.cutter = None
        self._tool = None

    def set_stock(self, stock):
        self._grid(stock.frame, stock.height)

    def _grid(self, frame, height):
        self.frame = frame
        step = math.ceil(max(frame.rows, frame.cols) / 600)
        rows = np.append(np.arange(0, frame.rows, step), frame.rows)
        cols = np.append(np.arange(0, frame.cols, step), frame.cols)
        xx, yy = np.meshgrid(frame.x0 + cols * frame.pitch_x,
                             frame.y0 + frame.height - rows * frame.pitch_y)
        self._rows, self._cols = np.minimum(rows, frame.rows - 1), np.minimum(cols, frame.cols - 1)
        self.vertices = np.column_stack((xx.ravel(), yy.ravel(), np.zeros(xx.size))).astype(np.float32)
        nr, nc = len(rows), len(cols)
        base = (np.arange(nr - 1)[:, None] * nc + np.arange(nc - 1)).ravel()
        self.faces = np.concatenate((np.column_stack((base, base + nc, base + 1)),
                                     np.column_stack((base + 1, base + nc, base + nc + 1)))).astype(np.uint32)
        if self.mesh is not None:
            self.mesh.parent = None
        self.mesh = scene.visuals.Mesh(parent=self.canvas.view.scene, shading='smooth', color=(0.72, 0.49, 0.27, 1))
        self.update_stock(height)

    def update_stock(self, height):
        self.vertices[:, 2] = height[np.ix_(self._rows, self._cols)].ravel()
        self.mesh.set_data(vertices=self.vertices, faces=self.faces, color=(0.72, 0.49, 0.27, 1))

    def set_burn(self, frame, image):
        if self.mesh is None:
            self._grid(frame, np.zeros_like(image))
        dose = image[np.ix_(self._rows, self._cols)].ravel()
        shade = np.exp(-dose / min(frame.pitch_x, frame.pitch_y))
        colors = np.column_stack((0.15 + 0.57 * shade, 0.08 + 0.41 * shade,
                                  0.03 + 0.24 * shade, np.ones_like(shade)))
        self.mesh.set_data(vertices=self.vertices, faces=self.faces, vertex_colors=colors)

    def set_paths(self, passes, machine_coords=False):
        positions = []
        for part in passes:
            for path in part.paths:
                xyz = path.copy()
                if part.z_is_power:
                    xyz[:, 2] = 0
                positions.extend(np.stack((xyz[:-1], xyz[1:]), axis=1).reshape(-1, 3))
        self.path.set_data(pos=np.asarray(positions, dtype=np.float32).reshape(-1, 3), color=(0.1, 0.45, 0.9, 0.5))
        self.label.text = 'CNCJob — machine coordinates' if machine_coords else 'Photo Carve — model coordinates'
        self.label.pos = (self.frame.x0, self.frame.y0 + self.frame.height, 0)

    def set_tool(self, tool, xyz):
        if tool != self._tool:
            if self.cutter is not None:
                self.cutter.parent = None
            radius = tool.diameter / 2
            if tool.kind == 'ball':
                theta = np.linspace(0, math.pi / 2, 12)
                radii, zs = radius * np.sin(theta), radius * (1 - np.cos(theta))
            elif tool.kind == 'vbit':
                radii = np.array([tool.tip_diameter / 2, radius])
                zs = (radii - radii[0]) / math.tan(math.radians(tool.angle_deg) / 2)
            else:
                radii, zs = np.array([radius]), np.array([0.0])
            if radii[0] > 0:
                radii, zs = np.r_[0, radii], np.r_[0, zs]
            radii, zs = np.append(radii, [radius, 0]), np.append(zs, [zs[-1] + tool.diameter * 2] * 2)
            angles = np.linspace(0, 2 * math.pi, 33)
            vertices = np.stack((radii[:, None] * np.cos(angles), radii[:, None] * np.sin(angles),
                                 np.broadcast_to(zs[:, None], (len(zs), len(angles)))), axis=-1).reshape(-1, 3)
            base = (np.arange(len(zs) - 1)[:, None] * len(angles) + np.arange(len(angles) - 1)).ravel()
            faces = np.concatenate((np.column_stack((base, base + 1, base + len(angles))),
                                    np.column_stack((base + 1, base + len(angles) + 1, base + len(angles)))))
            self.cutter = scene.visuals.Mesh(vertices=vertices, faces=faces.astype(np.uint32),
                                             color=(0.7, 0.7, 0.75, 0.7), shading='smooth', parent=self.canvas.view.scene)
            self.cutter.transform = STTransform()
            self._tool = tool
        self.cutter.transform.translate = xyz
        self.set_marker(xyz)

    def set_marker(self, xyz):
        self.marker.set_data(pos=np.asarray([xyz], dtype=np.float32), face_color='red', size=8)

    def clear(self):
        for visual in (self.path, self.marker, self.label, self.mesh, self.cutter):
            if visual is not None:
                visual.parent = None
        self.mesh = self.cutter = None
