# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

"""The visuals of the GRBL Sender in the Plot 3D tab: toolpath, accepted path, tool marker, height map."""

import numpy as np
from PyQt6 import QtCore, QtWidgets
from vispy import scene
from vispy.color import Colormap

RAPID_COLOR = (0.55, 0.55, 0.55, 0.6)
CUT_COLOR = (0.10, 0.45, 0.90, 1.0)
# an arc that the sender streams as an arc and the display could not split: drawn as its chord
CHORD_COLOR = (0.35, 0.35, 0.35, 1.0)
ACCEPTED_COLOR = (0.10, 0.70, 0.30, 1.0)
SELECTED_COLOR = (0.90, 0.10, 0.10, 1.0)
TOOL_COLOR = (0.90, 0.10, 0.10, 1.0)


class SenderView3d:
    def __init__(self, canvas):
        """:param canvas: the PlotCanvas3d of app.plotcanvas3d."""
        self.canvas = canvas
        parent = canvas.view.scene
        self.path = scene.visuals.Line(parent=parent, connect='segments', width=1.5, method='gl')
        self.accepted = scene.visuals.Line(parent=parent, connect='segments', width=2.5, method='gl')
        self.selected = scene.visuals.Line(parent=parent, connect='segments', width=3.5, method='gl')
        self.selected.set_gl_state('translucent', depth_test=False)
        self._selection_timer = QtCore.QTimer(getattr(canvas, 'native', None))
        self._selection_timer.setInterval(500)
        self._selection_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._selection_timer.timeout.connect(self._flash_selection)
        self.tool = scene.visuals.Markers(parent=parent)
        self.probing_shapes = canvas.new_shape_collection(layers=1)
        self.excellon_shapes = canvas.new_shape_collection(layers=1)
        self.mesh = None
        self.z_scale = 10.0
        self._segments = []
        native = getattr(canvas, 'native', None)
        self.coordinates = QtWidgets.QLabel(native) if isinstance(native, QtWidgets.QWidget) else None
        self._coordinate_timer = QtCore.QTimer(native)
        self._coordinate_timer.setSingleShot(True)
        self._coordinate_timer.setInterval(3000)
        self._coordinate_timer.timeout.connect(lambda: self.set_coordinates(None, None, None, stale=True))
        if self.coordinates is not None:
            self.coordinates.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.coordinates.setLayoutDirection(QtCore.Qt.LayoutDirection.LeftToRight)
            self.coordinates.setStyleSheet('QLabel {background: rgba(255,255,255,225); color: #202020; padding: 8px;}')
            self.coordinates.move(12, 12)
            self.set_coordinates(None, None, None)

    def set_coordinates(self, work, machine, units, stale=False):
        """Qt child of the viewport: independent of every scene/camera transform."""
        self._coordinate_timer.stop()
        if self.coordinates is None:
            return

        def position(xyz):
            if units not in ('MM', 'IN') or xyz is None or not np.isfinite(xyz).all():
                return 'unavailable'
            return 'X %.3f &nbsp; Y %.3f &nbsp; Z %.3f' % tuple(xyz)

        unit_text = units.lower() if units in ('MM', 'IN') else 'units unknown'
        self.coordinates.setText(
            '<span style="font-size:16pt; font-weight:bold">Work (%s): %s</span><br>'
            'Machine (%s): %s<br>WCS unverified · Machine reference unverified%s<br>'
            'Homing establishes the machine reference; not necessarily zero at switches.<br>'
            'Controller coordinates, not independent physical measurement.' % (
                unit_text, position(work), unit_text, position(machine), ' · stale' if stale else ''))
        self.coordinates.adjustSize()
        self.coordinates.show()
        self.coordinates.raise_()
        if work is not None or machine is not None:
            self._coordinate_timer.start()

    def set_toolpath(self, segments, chord_lines=()):
        """
        :param segments:    the list of gcode_segment.parse_toolpath(): (stream line index, start, end, rapid).
        :param chord_lines: the stream line indexes whose segment is the chord of an arc that was not split:
                            they are drawn in the grey CHORD_COLOR.
        """
        self.set_prepared_toolpath(self.prepare_toolpath(segments, chord_lines))

    @staticmethod
    def prepare_toolpath(segments, chord_lines=()):
        """Build the path buffers in a worker, without accessing the scene."""
        segments = list(segments)
        chord_lines = set(chord_lines)
        pos = np.array([p for _i, a, b, _r in segments for p in (a, b)], dtype=np.float32).reshape(-1, 3)
        colors = np.array(
            [c for i, _a, _b, r in segments
             for c in ((CHORD_COLOR if i in chord_lines else (RAPID_COLOR if r else CUT_COLOR)),) * 2],
            dtype=np.float32).reshape(-1, 4)
        return segments, pos, colors

    def set_prepared_toolpath(self, prepared):
        """Upload prepared buffers on the GUI thread."""
        self._segments, pos, colors = prepared
        # VisPy cannot draw an empty color array: an empty path gets 1 color
        self.path.set_data(pos=pos, color=colors if len(colors) else CUT_COLOR)
        self.accepted.set_data(pos=np.zeros((0, 3), dtype=np.float32))
        self.set_selected_line(None)

    def set_accepted(self, last_line_index):
        """
        Draw the segments of the lines up to and including `last_line_index` in the accepted color:
        the lines that GRBL accepted. The tool marker shows the real position of the tool.
        """
        accepted = [s for s in self._segments if s[0] <= last_line_index]
        if not accepted:
            self.accepted.set_data(pos=np.zeros((0, 3), dtype=np.float32))
            return
        pos = np.array([p for _i, a, b, _r in accepted for p in (a, b)], dtype=np.float32)
        self.accepted.set_data(pos=pos, color=ACCEPTED_COLOR)

    def set_selected_line(self, line_index):
        """Highlight one program line or a collection of lines, preserving accepted progress."""
        indexes = {line_index} if line_index is None or isinstance(line_index, int) else set(line_index)
        pos = np.array([p for i, a, b, _r in self._segments if i in indexes for p in (a, b)],
                       dtype=np.float32).reshape(-1, 3)
        self.selected.set_data(pos=pos, color=SELECTED_COLOR)
        self.selected.visible = True
        if len(pos):
            self._selection_timer.start()
        else:
            self._selection_timer.stop()

    def _flash_selection(self):
        self.selected.visible = not self.selected.visible

    def set_tool_position(self, xyz):
        self.tool.set_data(pos=np.array([xyz], dtype=np.float32), face_color=TOOL_COLOR, size=10)

    def set_height_map(self, grid_data, z_scale=None):
        """
        :param grid_data: (xs, ys, grid) of build_bilinear_grid(), or None to remove the mesh.
        :param z_scale:   the factor of the height for the display.
        """
        if z_scale is not None:
            self.z_scale = z_scale
        if self.mesh is not None:
            self.mesh.parent = None
            self.mesh = None
        if grid_data is None:
            return
        xs, ys, grid = grid_data
        vertices = np.array([(x, y, grid[j][i] * self.z_scale)
                             for j, y in enumerate(ys) for i, x in enumerate(xs)], dtype=np.float32)
        faces = []
        nx = len(xs)
        for j in range(len(ys) - 1):
            for i in range(nx - 1):
                a = j * nx + i
                faces.append((a, a + 1, a + nx))
                faces.append((a + 1, a + nx + 1, a + nx))
        z = vertices[:, 2]
        span = float(z.max() - z.min()) or 1.0
        cmap = Colormap([(0.13, 0.27, 0.65), (0.19, 0.72, 0.47), (0.84, 0.19, 0.15)])
        colors = cmap.map((z - z.min()) / span)
        self.mesh = scene.visuals.Mesh(vertices=vertices, faces=np.array(faces, dtype=np.uint32),
                                       vertex_colors=colors, parent=self.canvas.view.scene)
        self.mesh.opacity = 0.7

    def clear(self):
        """Remove the visuals from the scene. It is safe to call it more than 1 time."""
        self._selection_timer.stop()
        self._coordinate_timer.stop()
        if self.coordinates is not None:
            self.coordinates.hide()
            self.coordinates.deleteLater()
            self.coordinates = None
        self.probing_shapes.clear(update=True)
        self.probing_shapes.parent = None
        self.excellon_shapes.clear(update=True)
        self.excellon_shapes.parent = None
        for visual in (self.path, self.accepted, self.selected, self.tool):
            if visual.parent is not None:
                visual.parent = None
        self.set_height_map(None)
