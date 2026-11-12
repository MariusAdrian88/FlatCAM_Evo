# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# MIT Licence                                              #
# ##########################################################
"""
CAD style dimension lines for the 2D canvas: a thin line, an arrowhead at each end and the length text in a break of
the line, turned along the line.

dimension_layout() places one dimension (pure geometry, data units). DimensionLines draws any number of them with 3
VisPy visuals (one line visual with 'segments', one mesh for all arrowheads, one text visual with a position and a
rotation per item). Arrow, gap and text sizes are constant in screen pixels: the layout runs again when the zoom
changes; a pan alone keeps it.
"""

import math

import numpy as np

# Average advance of the glyphs of the VisPy text face (OpenSans), in em. The text width is estimated from these:
# the true measure needs the font atlas of the GL context.
_NARROW_CHARS = frozenset(".,:;'|!il1")
_WIDE_CHARS = frozenset('mwMW@%')
_EM_NARROW = 0.27
_EM_WIDE = 0.85
_EM_DEFAULT = 0.58
_EM_SPACE = 0.26


def dimension_layout(p0, p1, px_per_unit, text_width_px, arrow_len_px=10.0, arrow_half_width_px=3.5, gap_pad_px=4.0,
                     text_offset_px=10.0):
    """
    Place one dimension line. Data units in, data units out; the sizes given in pixels stay constant on screen.

    :param p0:                  (x, y) start point, data units
    :param p1:                  (x, y) end point, data units
    :param px_per_unit:         screen pixels per data unit (the current zoom)
    :param text_width_px:       width of the text on screen, pixels
    :param arrow_len_px:        length of an arrowhead, pixels
    :param arrow_half_width_px: half the width of an arrowhead base, pixels
    :param gap_pad_px:          free space between the text and the line on each side of the break, pixels
    :param text_offset_px:      distance of the text center from the line when the text does not fit in it, pixels
    :return:                    dict with 'lines', 'arrows', 'text_pos', 'text_angle', 'gap':
                                lines: list of ((x, y), (x, y)) pieces, 2 with a break for the text, else 1;
                                arrows: list of [tip, base_a, base_b] triangles, tips at p0 and p1, pointing outward,
                                empty when the segment is too short on screen for 2 arrows;
                                text_pos: the center of the text;
                                text_angle: the direction of the line in degrees, -90 < angle <= 90 (never upside
                                down);
                                gap: True when the line is broken for the text
    """
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    mid = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    if length == 0.0 or not px_per_unit or px_per_unit <= 0.0 or not math.isfinite(px_per_unit):
        return {'lines': [], 'arrows': [], 'text_pos': mid, 'text_angle': 0.0, 'gap': False}

    ux, uy = dx / length, dy / length
    angle = math.degrees(math.atan2(dy, dx))
    if angle > 90.0:
        angle -= 180.0
    elif angle <= -90.0:
        angle += 180.0

    unit = 1.0 / px_per_unit
    length_px = length * px_per_unit

    arrows = []
    if length_px >= 2.0 * arrow_len_px:
        a_len = arrow_len_px * unit
        hx, hy = -uy * arrow_half_width_px * unit, ux * arrow_half_width_px * unit
        bx, by = x0 + ux * a_len, y0 + uy * a_len
        arrows.append([(x0, y0), (bx + hx, by + hy), (bx - hx, by - hy)])
        bx, by = x1 - ux * a_len, y1 - uy * a_len
        arrows.append([(x1, y1), (bx + hx, by + hy), (bx - hx, by - hy)])

    text_w = max(float(text_width_px), 0.0)
    if arrows and length_px >= 2.0 * arrow_len_px + text_w + 2.0 * gap_pad_px:
        half = (text_w / 2.0 + gap_pad_px) * unit
        lines = [((x0, y0), (mid[0] - ux * half, mid[1] - uy * half)),
                 ((mid[0] + ux * half, mid[1] + uy * half), (x1, y1))]
        return {'lines': lines, 'arrows': arrows, 'text_pos': mid, 'text_angle': angle, 'gap': True}

    # the 'up' of the turned text: the text sits above the line as it is read
    rad = math.radians(angle)
    off = text_offset_px * unit
    text_pos = (mid[0] - math.sin(rad) * off, mid[1] + math.cos(rad) * off)
    return {'lines': [((x0, y0), (x1, y1))], 'arrows': arrows, 'text_pos': text_pos, 'text_angle': angle,
            'gap': False}


def estimate_text_width_px(text, font_px):
    """
    Width of a one line text on screen, estimated from the glyph classes of the VisPy face.

    :param text:    the text
    :param font_px: the font size in logical pixels (the em)
    :return:        width in pixels
    """
    em = 0.0
    for ch in text:
        if ch == ' ':
            em += _EM_SPACE
        elif ch in _NARROW_CHARS:
            em += _EM_NARROW
        elif ch in _WIDE_CHARS:
            em += _EM_WIDE
        else:
            em += _EM_DEFAULT
    return em * font_px


class DimensionLines:
    """
    Dimension lines drawn on the 2D VisPy canvas (a PanZoomCamera view). On any other canvas (the headless NullCanvas,
    a 3D canvas) the object keeps the segments and draws nothing.
    """

    # drawn after the plotted objects (order 0) of the view scene
    ORDER = 1

    def __init__(self, plotcanvas, color=None, font_size=9, line_width=1.0, bold=False):
        """
        :param plotcanvas:  the plot canvas (app.plotcanvas)
        :param color:       any VisPy color; None follows the canvas theme (white on a dark canvas, else black)
        :param font_size:   text size in points
        :param line_width:  line width in pixels; above 1 the lines are drawn as quads of the mesh (the GL line
                            width above 1 px is not supported by all drivers)
        :param bold:        bold text
        """
        self.canvas = plotcanvas
        self.font_size = float(font_size)
        self.line_width = max(float(line_width), 1.0)
        self.bold = bool(bold)
        self.color = color if color is not None else self._theme_color(plotcanvas)

        self._segments = []
        self._ppu = None
        self._last_text = None
        self._camera = None
        self.line = None
        self.mesh = None
        self.text = None

        camera = self._panzoom_camera(plotcanvas)
        if camera is None:
            return

        from vispy import scene

        parent = plotcanvas.view.scene
        self.line = scene.visuals.Line(pos=np.zeros((2, 2), dtype=np.float32), connect='segments',
                                       width=1.0, color=self.color, method='gl', antialias=False,
                                       parent=parent)
        self.mesh = scene.visuals.Mesh(vertices=np.zeros((3, 2), dtype=np.float32),
                                       faces=np.array([[0, 1, 2]], dtype=np.uint32), color=self.color, parent=parent)
        # glyphs made on the CPU like the HUD text: the GPU atlas can be corrupted by the culling state of the objects
        self.text = scene.visuals.Text('', color=self.color, font_size=self.font_size, bold=self.bold, method='cpu',
                                       anchor_x='center', anchor_y='center', parent=parent)
        for visual in (self.line, self.mesh, self.text):
            visual.set_gl_state('translucent', depth_test=False)
            visual.order = self.ORDER
            visual.visible = False

        self._camera = camera
        # the camera writes its scene matrix (the same MatrixTransform object) on each pan and zoom step
        camera.tf_mat.changed.connect(self._on_view_change)
        self._ppu = self._px_per_unit()

    # ------------------------------------------------------------------------------------------------------------
    # canvas helpers
    # ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _panzoom_camera(plotcanvas):
        try:
            from vispy.scene.cameras import PanZoomCamera
            camera = plotcanvas.view.camera
        except Exception:
            return None
        if not isinstance(camera, PanZoomCamera) or not hasattr(camera, 'tf_mat'):
            return None
        return camera

    @staticmethod
    def _theme_color(plotcanvas):
        hud_color = getattr(plotcanvas, 'text_hud_color', None)
        if isinstance(hud_color, str):
            return hud_color
        try:
            from PyQt6.QtCore import QSettings
            settings = QSettings("Open Source", "FlatCAM_EVO")
            theme = settings.value('theme', type=str) if settings.contains("theme") else 'default'
            dark_canvas = settings.value('dark_canvas', type=bool) if settings.contains("dark_canvas") else False
        except Exception:
            return 'black'
        return 'black' if (theme in ('default', 'light') and not dark_canvas) else 'white'

    def _px_per_unit(self):
        # scene -> view box pixels (logical pixels, as the text size); the view is square (aspect 1)
        try:
            m = self._camera.tf_mat.matrix
            ppu = float(math.hypot(m[0, 0], m[0, 1]))
        except Exception:
            return None
        return ppu if ppu > 0.0 and math.isfinite(ppu) else None

    def _font_px(self):
        dpi = getattr(self.canvas, 'dpi', None)
        try:
            dpi = float(dpi)
        except (TypeError, ValueError):
            dpi = 96.0
        if not dpi > 0.0:
            dpi = 96.0
        return self.font_size / 72.0 * dpi

    def _on_view_change(self, event=None):
        ppu = self._px_per_unit()
        if ppu is None:
            return
        old = self._ppu
        if old is not None and abs(ppu - old) <= 1e-9 * old:
            # a pan: the layout in data units stays valid
            return
        self._ppu = ppu
        if self._segments:
            self._rebuild()

    # ------------------------------------------------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------------------------------------------------
    @property
    def segments(self):
        """The list last given to set_data (a copy)."""
        return list(self._segments)

    def set_data(self, segments):
        """
        Replace all dimensions.

        :param segments:    list of (p0, p1, text); p0 and p1 are (x, y) in data units
        :return:            None
        """
        self._segments = [(tuple(p0), tuple(p1), str(text)) for p0, p1, text in (segments or [])]
        if self._camera is None:
            return
        self._ppu = self._px_per_unit()
        self._rebuild()

    def clear(self):
        """Remove all dimensions; the visual stays ready for set_data()."""
        self._segments = []
        if self._camera is None:
            return
        self._hide()

    def set_color(self, color):
        """
        :param color:   any VisPy color
        :return:        None
        """
        self.color = color
        if self._camera is None:
            return
        self.line.set_data(color=color)
        self.mesh.set_data(vertices=self.mesh.mesh_data.get_vertices(), faces=self.mesh.mesh_data.get_faces(),
                           color=color)
        self.text.color = color

    def set_style(self, line_width=None, font_size=None, bold=None):
        """
        :param line_width:  line width in pixels (None keeps it)
        :param font_size:   text size in points (None keeps it)
        :param bold:        bold text (None keeps it)
        :return:            None
        """
        if line_width is not None:
            self.line_width = max(float(line_width), 1.0)
        if font_size is not None:
            self.font_size = float(font_size)
        if bold is not None:
            self.bold = bool(bold)
        if self._camera is None:
            return
        self.text.font_size = self.font_size
        self.text.bold = self.bold
        # the glyphs are made again for the new font
        self._last_text = None
        self.refresh()

    def refresh(self):
        """Lay out the stored dimensions again for the current zoom."""
        if self._camera is None:
            return
        self._ppu = self._px_per_unit()
        self._rebuild()

    def delete(self):
        """Detach from the scene and stop following the view."""
        if self._camera is not None:
            try:
                self._camera.tf_mat.changed.disconnect(self._on_view_change)
            except Exception:
                pass
            for visual in (self.line, self.mesh, self.text):
                try:
                    visual.parent = None
                except Exception:
                    pass
        self._camera = None
        self._segments = []
        self.line = self.mesh = self.text = None

    # ------------------------------------------------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------------------------------------------------
    def _hide(self):
        for visual in (self.line, self.mesh, self.text):
            if visual.visible:
                visual.visible = False

    def _rebuild(self):
        ppu = self._ppu
        if not self._segments or ppu is None:
            self._hide()
            return

        font_px = self._font_px()
        width = self.line_width
        # the arrowheads grow with the line width, else a thick line hides them
        arrow_len = 10.0 + 2.0 * (width - 1.0)
        arrow_half = 3.5 + 0.75 * (width - 1.0)
        half_w = width / 2.0 / ppu
        line_pts = []
        tri_pts = []
        texts = []
        text_pos = []
        text_rot = []
        for p0, p1, label in self._segments:
            lay = dimension_layout(p0, p1, ppu, estimate_text_width_px(label, font_px), arrow_len_px=arrow_len,
                                   arrow_half_width_px=arrow_half)
            for a, b in lay['lines']:
                if width <= 1.0:
                    line_pts.append(a)
                    line_pts.append(b)
                    continue
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length == 0.0:
                    continue
                nx, ny = -(b[1] - a[1]) / length * half_w, (b[0] - a[0]) / length * half_w
                c0, c1 = (a[0] + nx, a[1] + ny), (a[0] - nx, a[1] - ny)
                c2, c3 = (b[0] - nx, b[1] - ny), (b[0] + nx, b[1] + ny)
                tri_pts.extend((c0, c1, c2, c0, c2, c3))
            for tri in lay['arrows']:
                tri_pts.extend(tri)
            if label and lay['lines']:
                texts.append(label)
                text_pos.append(lay['text_pos'])
                # TextVisual turns clockwise on screen; the data Y axis points up (no camera flip): minus the angle
                text_rot.append(-lay['text_angle'])

        if line_pts:
            self.line.set_data(pos=np.asarray(line_pts, dtype=np.float32), color=self.color)
            self.line.visible = True
        elif self.line.visible:
            self.line.visible = False

        if tri_pts:
            vertices = np.asarray(tri_pts, dtype=np.float32)
            faces = np.arange(len(tri_pts), dtype=np.uint32).reshape(-1, 3)
            self.mesh.set_data(vertices=vertices, faces=faces, color=self.color)
            self.mesh.visible = True
        elif self.mesh.visible:
            self.mesh.visible = False

        if texts:
            if texts != self._last_text:
                # a new text list rebuilds the glyph buffers; positions and angles alone do not
                self.text.text = texts
                self._last_text = texts
            self.text.pos = np.asarray(text_pos, dtype=np.float32)
            self.text.rotation = np.asarray(text_rot, dtype=np.float32)
            self.text.visible = True
        elif self.text.visible:
            self.text.visible = False
