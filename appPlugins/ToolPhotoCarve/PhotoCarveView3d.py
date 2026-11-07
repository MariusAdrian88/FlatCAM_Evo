"""GUI-thread-only Photo Carve visuals in the existing PlotCanvas3d scene.

The stock is a solid block: the top surface (``self.vertices`` / ``self.faces``) follows the simulated height, and
a second mesh closes the block with 4 walls and a bottom at ``-thickness``. The top is colored by the depth of the
cut, from the material color to a darker cut color, so the cuts are visible from any view angle.
"""

import math

import numpy as np
from PyQt6 import sip
from vispy import scene
from vispy.visuals.transforms import STTransform

from .PhotoCarveModel import MATERIALS, _


class PhotoCarveView3d:
    def __init__(self, canvas, material='wood', thickness=10.0, depth_ref=1.0, spot=0.0):
        self.canvas = canvas
        # laser: the height of the burned line (the line spacing of the job)
        self.spot = float(spot)
        parent = canvas.view.scene
        self.base, self.burnt = (np.asarray(c, dtype=np.float32) for c in MATERIALS.get(material, MATERIALS['wood']))
        self.thickness = float(thickness)
        # the depth that gets the full cut color: the deepest cut of the job (at least a small value)
        self.depth_ref = max(float(depth_ref), 1e-3)
        self.path = scene.visuals.Line(parent=parent, connect='segments', width=1.5, method='gl')
        self.marker = scene.visuals.Markers(parent=parent)
        self.label = scene.visuals.Text(parent=parent, color='black', font_size=12, anchor_x='left')
        self.mesh = None
        self.walls = None
        self.cutter = None
        self._tool = None

    # ------------------------------------------------------------------------------------------------ stock
    def set_stock(self, stock):
        self._grid(stock.frame, stock.height)

    def _grid(self, frame, height):
        self.frame = frame
        step = math.ceil(max(frame.rows, frame.cols) / 600)
        self._step = step
        rows = np.append(np.arange(0, frame.rows, step), frame.rows)
        cols = np.append(np.arange(0, frame.cols, step), frame.cols)
        xx, yy = np.meshgrid(frame.x0 + cols * frame.pitch_x,
                             frame.y0 + frame.height - rows * frame.pitch_y)
        self._rows, self._cols = np.minimum(rows, frame.rows - 1), np.minimum(cols, frame.cols - 1)
        self.vertices = np.column_stack((xx.ravel(), yy.ravel(), np.zeros(xx.size))).astype(np.float32)
        nr, nc = len(rows), len(cols)
        self._shape = (nr, nc)
        base = (np.arange(nr - 1)[:, None] * nc + np.arange(nc - 1)).ravel()
        self.faces = np.concatenate((np.column_stack((base, base + nc, base + 1)),
                                     np.column_stack((base + 1, base + nc, base + nc + 1)))).astype(np.uint32)
        self._build_walls()
        for visual in (self.mesh, self.walls):
            if visual is not None:
                visual.parent = None
        self.mesh = scene.visuals.Mesh(parent=self.canvas.view.scene, shading='smooth')
        self.walls = scene.visuals.Mesh(parent=self.canvas.view.scene, shading='flat')
        for visual in (self.mesh, self.walls):
            self._light(visual)
        self.update_stock(height)

    @staticmethod
    def _light(visual):
        """More ambient light than the vispy default: the material keeps its color, the relief keeps its shade."""
        shading = getattr(visual, 'shading_filter', None)
        if shading is None:
            return
        try:
            shading.ambient_light = (1, 1, 1, 0.55)
            shading.diffuse_light = (1, 1, 1, 0.6)
            shading.specular_light = (1, 1, 1, 0.15)
        except (AttributeError, TypeError, ValueError):
            pass

    def _build_walls(self):
        """Indices of the top edge (a closed loop) and the wall + bottom faces; built once per stock."""
        nr, nc = self._shape
        top = np.r_[np.arange(nc),                                   # top row, left -> right
                    np.arange(1, nr) * nc + nc - 1,                  # right column, down
                    (nr - 1) * nc + np.arange(nc - 2, -1, -1),       # bottom row, right -> left
                    np.arange(nr - 2, 0, -1) * nc]                   # left column, up
        self._edge = top
        n = len(top)
        a = np.arange(n)
        b = (a + 1) % n
        # wall vertices: [top edge copies (n), bottom copies (n)]
        walls = np.concatenate((np.column_stack((a, b, a + n)), np.column_stack((b, b + n, a + n))))
        corners = n + np.array([0, nc - 1, nc - 1 + nr - 1, nc - 1 + nr - 1 + nc - 1])
        bottom = np.array([[corners[0], corners[2], corners[1]], [corners[0], corners[3], corners[2]]])
        self.wall_faces = np.concatenate((walls, bottom)).astype(np.uint32)
        self.wall_vertices = np.concatenate((self.vertices[top], self.vertices[top])).astype(np.float32)
        self.wall_vertices[n:, 2] = -self.thickness
        side = self.base * 0.82
        self.wall_colors = np.tile(np.r_[side, 1.0].astype(np.float32), (2 * n, 1))
        self.wall_colors[n:, :3] = self.base * 0.68

    def _sample(self, height):
        """Height at the mesh vertices. A coarse mesh takes the minimum of the cells around each vertex, so a
        groove narrower than the mesh step stays visible (a point sample can miss it)."""
        s = self._step
        if s == 1:
            return height[np.ix_(self._rows, self._cols)]
        rows, cols = height.shape
        padded = np.pad(height, ((0, -rows % s), (0, -cols % s)), mode='edge')
        blocks = padded.reshape(padded.shape[0] // s, s, padded.shape[1] // s, s).min(axis=(1, 3))
        b = np.pad(blocks, 1, mode='edge')
        return np.minimum(np.minimum(b[:-1, :-1], b[1:, :-1]), np.minimum(b[:-1, 1:], b[1:, 1:]))

    def _cut_colors(self, z):
        """Material color at the top, much darker with the depth of the cut. The power < 1 makes shallow cuts
        clearly darker than the uncut top."""
        depth = np.clip(-z / self.depth_ref, 0, 1)[:, None] ** 0.6
        cut = self.base * 0.28 + 0.02
        colors = np.empty((len(z), 4), dtype=np.float32)
        colors[:, :3] = self.base * (1 - depth) + cut * depth
        colors[:, 3] = 1
        return colors

    def update_stock(self, height):
        self.vertices[:, 2] = self._sample(height).ravel()
        self.mesh.set_data(vertices=self.vertices, faces=self.faces, vertex_colors=self._cut_colors(self.vertices[:, 2]))
        n = len(self._edge)
        self.wall_vertices[:n, 2] = self.vertices[self._edge, 2]
        self.walls.set_data(vertices=self.wall_vertices, faces=self.wall_faces, vertex_colors=self.wall_colors)

    def burn_exposure(self, frame, image):
        """Exposure 0..1 of each cell, as the job burns it.

        ``image`` holds power × length per cell, and a line burns only the cell row under its center. The beam
        covers the whole line spacing (``self.spot``), so each row is spread over the rows of 1 spot height;
        otherwise a 1 mm spacing at a 0.1 mm cell shows thin lines on an unburned stock."""
        exposure = np.clip(image / frame.pitch_x, 0, 1)
        k = max(1, int(round(self.spot / frame.pitch_y)))
        if k > 1:
            above = (k - 1) // 2
            padded = np.pad(exposure, ((above, k - 1 - above), (0, 0)))
            spread = padded[:exposure.shape[0]].copy()
            for shift in range(1, k):
                np.maximum(spread, padded[shift:shift + exposure.shape[0]], out=spread)
            exposure = spread
        return exposure

    def _mean(self, grid):
        """The mean of the cells around each mesh vertex: a coarse mesh keeps the tone of the burn."""
        s = self._step
        if s == 1:
            return grid[np.ix_(self._rows, self._cols)]
        rows, cols = grid.shape
        padded = np.pad(grid, ((0, -rows % s), (0, -cols % s)), mode='edge')
        blocks = padded.reshape(padded.shape[0] // s, s, padded.shape[1] // s, s).mean(axis=(1, 3))
        b = np.pad(blocks, 1, mode='edge')
        return (b[:-1, :-1] + b[1:, :-1] + b[:-1, 1:] + b[1:, 1:]) / 4

    def set_burn(self, frame, image):
        if self.mesh is None:
            self._grid(frame, np.zeros_like(image))
        dose = self._mean(self.burn_exposure(frame, image)).ravel()
        # the darkness follows the power directly, like the preview
        shade = (1 - dose)[:, None]
        colors = np.empty((len(dose), 4), dtype=np.float32)
        colors[:, :3] = self.base * shade + self.burnt * (1 - shade)
        colors[:, 3] = 1
        self.mesh.set_data(vertices=self.vertices, faces=self.faces, vertex_colors=colors)

    # ------------------------------------------------------------------------------------------------ paths, tool
    def set_paths(self, passes, machine_coords=False):
        positions = []
        for part in passes:
            for path in part.paths:
                xyz = path.copy()
                if part.z_is_power:
                    xyz[:, 2] = 0
                positions.extend(np.stack((xyz[:-1], xyz[1:]), axis=1).reshape(-1, 3))
        self.path.set_data(pos=np.asarray(positions, dtype=np.float32).reshape(-1, 3), color=(0.1, 0.45, 0.9, 0.35))
        self.label.text = (_('CNCJob — machine coordinates') if machine_coords else
                           _('Photo Carve — model coordinates'))
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

    @property
    def alive(self):
        """False after the 3D tab is closed: its Qt widget is deleted and a scene change raises RuntimeError."""
        return not sip.isdeleted(self.canvas.native)

    def finish(self):
        """Show only the cut stock: the path lines, the tool and the marker cover the grooves."""
        if not self.alive:
            return
        for visual in (self.path, self.marker, self.cutter):
            if visual is not None:
                visual.visible = False

    def clear(self):
        if self.alive:
            for visual in (self.path, self.marker, self.label, self.mesh, self.walls, self.cutter):
                if visual is not None:
                    visual.parent = None
        self.mesh = self.walls = self.cutter = None
