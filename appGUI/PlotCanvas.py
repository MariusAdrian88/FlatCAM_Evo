# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# Author: Dennis Hayrullin (c)                             #
# Date: 2016                                               #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore, QtGui

import logging
import math
from appGUI.VisPyCanvas import VisPyCanvas, Color
from appGUI.VisPyVisuals import ShapeGroup, ShapeCollection, TextCollection, TextGroup, Cursor, CursorText
from appDatabase import _database_option
from vispy.scene.visuals import InfiniteLine, Line, Rectangle, Text

import gettext
import appTranslation as fcTranslate
import builtins

import numpy as np
from vispy.geometry import Rect

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class PlotCanvas(QtCore.QObject, VisPyCanvas):
    """
    Class handling the plotting area in the application.
    """

    def __init__(self, fcapp):
        """
        The constructor configures the VisPy figure that
        will contain all plots, creates the base axes and connects
        events to the plotting area.

        :rtype: PlotCanvas
        """

        # super(PlotCanvas, self).__init__()
        # QtCore.QObject.__init__(self)
        # VisPyCanvas.__init__(self)
        super().__init__()

        # VisPyCanvas does not allow new attributes. Override.
        self.unfreeze()

        self.fcapp = fcapp

        settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("theme"):
            theme = settings.value('theme', type=str)
        else:
            theme = 'default'

        if settings.contains("dark_canvas"):
            dark_canvas = settings.value('dark_canvas', type=bool)
        else:
            dark_canvas = False

        if (theme == 'default' or theme == 'light') and not dark_canvas:
            self.line_color = (0.3, 0.0, 0.0, 1.0)
            # self.rect_hud_color = Color('#0000FF10')
            self.rect_hud_color = Color('#80808040')
            self.text_hud_color = 'black'
        else:
            self.line_color = (0.4, 0.4, 0.4, 1.0)
            self.rect_hud_color = Color('#80808040')
            self.text_hud_color = 'white'

        # workspace lines; I didn't use the rectangle because I didn't want to add another VisPy Node,
        # which might decrease performance
        # self.b_line, self.r_line, self.t_line, self.l_line = None, None, None, None
        self.workspace_line = None

        self.pagesize_dict = {}
        self.pagesize_dict.update(
            {
                'A0': (841, 1189),
                'A1': (594, 841),
                'A2': (420, 594),
                'A3': (297, 420),
                'A4': (210, 297),
                'A5': (148, 210),
                'A6': (105, 148),
                'A7': (74, 105),
                'A8': (52, 74),
                'A9': (37, 52),
                'A10': (26, 37),

                'B0': (1000, 1414),
                'B1': (707, 1000),
                'B2': (500, 707),
                'B3': (353, 500),
                'B4': (250, 353),
                'B5': (176, 250),
                'B6': (125, 176),
                'B7': (88, 125),
                'B8': (62, 88),
                'B9': (44, 62),
                'B10': (31, 44),

                'C0': (917, 1297),
                'C1': (648, 917),
                'C2': (458, 648),
                'C3': (324, 458),
                'C4': (229, 324),
                'C5': (162, 229),
                'C6': (114, 162),
                'C7': (81, 114),
                'C8': (57, 81),
                'C9': (40, 57),
                'C10': (28, 40),

                # American paper sizes
                'LETTER': (8.5*25.4, 11*25.4),
                'LEGAL': (8.5*25.4, 14*25.4),
                'ELEVENSEVENTEEN': (11*25.4, 17*25.4),

                # From https://en.wikipedia.org/wiki/Paper_size
                'JUNIOR_LEGAL': (5*25.4, 8*25.4),
                'HALF_LETTER': (5.5*25.4, 8*25.4),
                'GOV_LETTER': (8*25.4, 10.5*25.4),
                'GOV_LEGAL': (8.5*25.4, 13*25.4),
                'LEDGER': (17*25.4, 11*25.4),
            }
        )

        # <VisPyCanvas>
        self.create_native()
        self.native.setParent(self.fcapp.ui)
        # the canvas is never mirrored: X grows to the right in a right-to-left application too
        self.native.setLayoutDirection(QtCore.Qt.LayoutDirection.LeftToRight)

        self.axis_transparency = 0.8
        axis_color = self.axis_rgba()

        # ## AXIS # ##
        # Draw the grid before axes and object collections without changing their GL state.
        self.grid.order = -1
        # self.v_line = InfiniteLine(pos=0, color=(0.70, 0.3, 0.3, 0.8), vertical=True,
        #                            parent=self.view.scene)

        axis_width = _database_option(self.fcapp, 'global_axis_width') or 1

        self.v_line = InfiniteLine(pos=0, color=axis_color, vertical=True, line_width=axis_width,
                                   parent=None)
        self.h_line = InfiniteLine(pos=0, color=axis_color, vertical=False, line_width=axis_width,
                                   parent=None)

        self.line_parent = None
        if _database_option(self.fcapp, "global_cursor_color_enabled"):
            c_color = Color(_database_option(self.fcapp, "global_cursor_color")).rgba
        else:
            c_color = self.line_color

        c_width = _database_option(self.fcapp, "global_cursor_width") or 1.0
        self.cursor_v_line = InfiniteLine(pos=None, color=c_color, vertical=True, line_width=c_width,
                                          parent=self.line_parent)

        self.cursor_h_line = InfiniteLine(pos=None, color=c_color, vertical=False, line_width=c_width,
                                          parent=self.line_parent)

        # setup HUD

        # TEXT HUD
        # Generate glyphs on the CPU; object culling state can corrupt GPU font atlases.
        self.text_hud = Text('', color=self.text_hud_color, method='cpu', anchor_x='left', parent=None)
        self.text_hud.order = 2
        # RECT HUD
        self.rect_hud = Rectangle(width=10, height=10, radius=[5, 5, 5, 5], center=(20, 20),
                                  border_color=self.rect_hud_color, color=self.rect_hud_color, parent=None)
        # set_gl_state() replaces the whole state; without 'translucent' the rect inherits the blend of the last visual
        self.rect_hud.set_gl_state('translucent', depth_test=False)
        self.rect_hud.order = 1

        self._hud_update_timer = QtCore.QTimer(self)
        self._hud_update_timer.setSingleShot(True)
        self._hud_update_timer.setInterval(33)
        self._hud_update_timer.timeout.connect(self._update_text_hud)
        self.on_update_text_hud()
        # the HUD sits against the canvas edges: re-place it after the view got its new size
        self.events.resize.connect(self._on_hud_resize)

        # cursor text t obe attached to mouse cursor in Editors
        self.text_cursor = CursorText('', color=self.text_hud_color, method='cpu', anchor_x='left', parent=None)

        # draw a rectangle made out of 4 lines on the canvas to serve as a hint for the work area
        # all CNC have a limited workspace
        if _database_option(self.fcapp, 'global_workspace') is True:
            self.draw_workspace(workspace_size=_database_option(self.fcapp, "global_workspaceT"))

        # HUD Display
        self.hud_enabled = False

        # enable the HUD if it is activated in FlatCAM Preferences
        if _database_option(self.fcapp, 'global_hud') is True:
            self.on_toggle_hud(state=True, silent=True)

        # Axis Display
        self.axis_enabled = False

        # enable Axis
        if _database_option(self.fcapp, 'global_axis') is True:
            self.on_toggle_axis(state=True, silent=True)

        # enable Grid lines
        self.grid_lines_enabled = True

        self.shape_collections = []

        # deferred: the redraws of a burst of object plots are joined into one rebuild
        self.shape_collection = self.new_shape_collection(
            deferred=_database_option(self.fcapp, 'global_joined_redraws') is not False)
        self.text_collection = self.new_text_collection()

        self.text_collection.enabled = True

        # Mouse Custom Cursor
        self.c = None
        self.big_cursor = None
        self._cursor_color = Color(self.fcapp.cursor_color_3D).rgba
        self._cursor_position = None
        self._cursor_line_color = None
        self._layout_anchor = None
        self._layout_origin = None
        self._layout_window = None
        self._layout_anchor_timer = QtCore.QTimer(self)
        self._layout_anchor_timer.setSingleShot(True)
        self._layout_anchor_timer.timeout.connect(self._restore_layout_anchor)
        self.native.installEventFilter(self)
        self.events.mouse_move.connect(self._show_cursor_on_move)

        # Parent container
        # self.container = container

        # Keep VisPy canvas happy by letting it be "frozen" again.
        self.freeze()

        # fit everything into view
        self.fit_view()

        self.graph_event_connect('mouse_wheel', self.on_mouse_scroll)

        # <QtCore.QObject>
        # self.container.addWidget(self.native)

    @staticmethod
    def color_hex2tuple(hex_color):
        # strip the # from the beginning
        color = hex_color[1:]

        # convert color RGB components from range 0...255 to 0...1
        r_color = int(color[:2], 16) / 255
        g_color = int(color[2:4], 16) / 255
        b_color = int(color[4:6], 16) / 255
        return r_color, g_color, b_color

    def axis_rgba(self):
        # an 8 digit color (#RRGGBBAA) gives its own alpha; an old 6 digit color keeps the default transparency
        hex_color = _database_option(self.fcapp, 'global_axis_color')
        r_color, g_color, b_color = self.color_hex2tuple(hex_color)
        alpha = int(hex_color[7:9], 16) / 255 if len(hex_color) >= 9 else self.axis_transparency
        return r_color, g_color, b_color, alpha

    def on_toggle_axis(self, signal=None, state=None, silent=None):
        if not state:
            state = not self.axis_enabled

        if state:
            self.axis_enabled = True
            self.fcapp.defaults['global_axis'] = True
            self.v_line.parent = self.view.scene
            self.h_line.parent = self.view.scene
            self.grid.set_hide_axes(True)
            self.fcapp.ui.axis_status_label.setStyleSheet("""
                                                          QLabel
                                                          {
                                                              color: black;
                                                              background-color: orange;
                                                          }
                                                          """)
            if silent is None:
                self.fcapp.inform[str, bool].emit(_("Axis enabled."), False)
        else:
            self.axis_enabled = False
            self.fcapp.defaults['global_axis'] = False
            self.v_line.parent = None
            self.h_line.parent = None
            self.grid.set_hide_axes(False)
            self.fcapp.ui.axis_status_label.setStyleSheet("")
            if silent is None:
                self.fcapp.inform[str, bool].emit(_("Axis disabled."), False)

    def apply_axis_color(self):
        self.fcapp.log.debug('PlotCanvas.apply_axis_color() -> axis color applied')

        axis_color = self.axis_rgba()

        if axis_color is not None:
            axis_color = np.array(axis_color, dtype=np.float32)
            if axis_color.ndim != 1 or axis_color.shape[0] != 4:
                self.fcapp.log.error('axis color must be a 4 element float rgba tuple,'
                                     ' list or array')
            self.v_line._color = axis_color
            self.v_line._changed['color'] = True

            self.h_line._color = axis_color
            self.h_line._changed['color'] = True

    def apply_axis_width(self):
        width = _database_option(self.fcapp, 'global_axis_width')
        if width and width != self.v_line.line_width:
            self.v_line.line_width = width
            self.h_line.line_width = width

    def _on_hud_resize(self, event=None):
        if getattr(self, 'hud_enabled', False):
            self._hud_update_timer.start()

    def apply_hud_options(self):
        """Redraw the HUD with the current HUD options (position, colors, offset)."""
        self._update_text_hud()

    def on_toggle_hud(self, signal=None, state=None, silent=None):
        if state is None:
            state = not self.hud_enabled

        if state:
            self.hud_enabled = True
            self._hud_update_timer.stop()
            self._update_text_hud()
            self.rect_hud.parent = self.view
            self.text_hud.parent = self.view
            self.fcapp.defaults['global_hud'] = True
            self.fcapp.ui.hud_label.setStyleSheet("""
                                                  QLabel
                                                  {
                                                      color: black;
                                                      background-color: mediumpurple;
                                                  }
                                                  """)
            if silent is None:
                self.fcapp.inform[str, bool].emit(_("HUD enabled."), False)

        else:
            self.hud_enabled = False
            self.rect_hud.parent = None
            self.text_hud.parent = None
            self.fcapp.defaults['global_hud'] = False
            self.fcapp.ui.hud_label.setStyleSheet("")
            if silent is None:
                self.fcapp.inform[str, bool].emit(_("HUD disabled."), False)

    def on_update_text_hud(self, dx=None, dy=None, x=None, y=None):
        """
        Update the text of the location labels from HUD

        :param x:   X location
        :type x:    float
        :param y:   Y location
        :type y:    float
        :param dx:  Delta X location
        :type dx:   float
        :param dy:  Delta Y location
        :type dy:   float
        :return:
        :rtype:     None
        """
        self._hud_values = (dx, dy, x, y)
        # Refresh numeric readouts at about 30 Hz without delaying cursor or model updates.
        if getattr(self, 'hud_enabled', False) and not self._hud_update_timer.isActive():
            self._hud_update_timer.start()

    def _update_text_hud(self):
        if not getattr(self, 'hud_enabled', False):
            return
        dx, dy, x, y = self._hud_values

        # units
        units = self.fcapp.app_units.lower()

        polar = self.fcapp.ui.polar_coords_action.isChecked()
        labels = ('R:', 'θ:', 'X:', 'Y:') if polar else ('Dx:', 'Dy:', 'X:', 'Y:')
        if polar:
            dx, dy = (math.hypot(dx or 0, dy or 0),
                      math.degrees(math.atan2(dy or 0, dx or 0)) % 360 if dx or dy else 0.0)
        dx_dec = str(self.fcapp.dec_format(dx, self.fcapp.decimals)) if dx else '0.0'
        dy_dec = str(self.fcapp.dec_format(dy, self.fcapp.decimals)) if dy else '0.0'
        x_dec = str(self.fcapp.dec_format(x, self.fcapp.decimals)) if x else '0.0'
        y_dec = str(self.fcapp.dec_format(y, self.fcapp.decimals)) if y else '0.0'
        values = (dx_dec, dy_dec, x_dec, y_dec)
        value_width = max(len(value) for value in values)
        hud_text = ['%s %s%s' % (label.ljust(3), value.rjust(value_width),
                                '°' if polar and label == 'θ:' else ' [%s]' % units)
                    for label, value in zip(labels, values)]

        # font size
        q_settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("hud_font_size"):
            fsize = q_settings.value('hud_font_size', type=int)
        else:
            fsize = 8

        c_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        c_font.setPointSize(max(1, fsize))
        c_font_metrics = QtGui.QFontMetricsF(c_font, self.native)
        padding = 6
        line_height = c_font_metrics.height()
        text_bounds = [c_font_metrics.tightBoundingRect(line) for line in hud_text]
        ascent = -min(rect.top() for rect in text_bounds)
        height = 4 * line_height + ascent + max(rect.bottom() for rect in text_bounds) + 2 * padding
        width = max(c_font_metrics.horizontalAdvance(line) for line in hud_text) + 2 * padding

        # placement: the HUD sits 'offset' pixels from the two canvas edges nearest to the chosen corner
        offset = _database_option(self.fcapp, 'global_hud_offset')
        offset = 10 if offset is None else offset
        position = _database_option(self.fcapp, 'global_hud_position') or 'TL'
        view_width, view_height = self.view.size
        left = offset if 'L' in position else view_width - offset - width
        top = offset if 'T' in position else view_height - offset - height
        center_x = left + width / 2
        center_y = top + height / 2

        # text
        if self.text_hud.font_size != fsize:
            self.text_hud.font_size = fsize
        if self.text_hud.face != c_font.family():
            self.text_hud.face = c_font.family()
        if _database_option(self.fcapp, 'global_hud_text_color_enabled'):
            text_color = Color(_database_option(self.fcapp, 'global_hud_text_color')).rgba
        else:
            gray = 1.0 if np.mean(self.view.bgcolor.rgb) < 0.5 else 0.0
            text_color = (gray, gray, gray, 1.0)
        if not np.allclose(self.text_hud.color.rgba, text_color):
            self.text_hud.color = text_color
        if self.text_hud.text != hud_text:
            self.text_hud.text = hud_text
        text_pos = [(left + padding, top + padding + ascent + row * line_height)
                    for row in (0, 1, 3, 4)]
        if not np.array_equal(self.text_hud.pos[:, :2], text_pos):
            self.text_hud.pos = text_pos
        if self.text_hud.anchors != ('left', 'baseline'):
            self.text_hud.anchors = 'left', 'baseline'

        # rectangle
        if tuple(self.rect_hud.center) != (center_x, center_y):
            self.rect_hud.center = center_x, center_y
        if self.rect_hud.width != width:
            self.rect_hud.width = width
        if self.rect_hud.height != height:
            self.rect_hud.height = height
        bg_color = Color(_database_option(self.fcapp, 'global_hud_bg_color') or '#80808040')
        if not np.allclose(self.rect_hud.color.rgba, bg_color.rgba):
            self.rect_hud.color = bg_color
        if not np.allclose(self.rect_hud.border_color.rgba, bg_color.rgba):
            self.rect_hud.border_color = bg_color

    def on_toggle_grid_lines(self, signal=None, silent=None):
        state = self.grid_lines_enabled

        settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("theme"):
            theme = settings.value('theme', type=str)
        else:
            theme = 'default'

        if settings.contains("dark_canvas"):
            dark_canvas = settings.value('dark_canvas', type=bool)
        else:
            dark_canvas = False

        if (theme == 'default' or theme == 'light') and not dark_canvas:
            color = 'dimgray'
        else:
            color = '#dededeff'

        if state:
            self.fcapp.options['global_grid_lines'] = True
            self.grid_lines_enabled = False
            # self.grid.parent = self.view.scene
            self.grid._grid_color_fn['color'] = Color(color).rgba
            self.grid.visible = True
            if silent is None:
                self.fcapp.inform[str, bool].emit(_("Grid enabled."), False)
        else:
            self.fcapp.options['global_grid_lines'] = False
            self.grid_lines_enabled = True
            # self.grid.parent = None
            self.grid.visible = False
            if silent is None:
                self.fcapp.inform[str, bool].emit(_("Grid disabled."), False)

        self.grid.update()

    def draw_workspace(self, workspace_size):
        """
        Draw a rectangular shape on canvas to specify our valid workspace.
        :param workspace_size: the workspace size; tuple
        :return:
        """
        self.delete_workspace()
        try:
            if self.fcapp.app_units.upper() == 'MM':
                dims = self.pagesize_dict[workspace_size]
            else:
                dims = (self.pagesize_dict[workspace_size][0]/25.4, self.pagesize_dict[workspace_size][1]/25.4)
        except Exception as e:
            self.app.log.error("PlotCanvas.draw_workspace() --> %s" % str(e))
            return

        if _database_option(self.fcapp, 'global_workspace_orientation') == 'l':
            dims = (dims[1], dims[0])

        a = np.array([(0, 0), (dims[0], 0), (dims[0], dims[1]), (0, dims[1])])

        # if not self.workspace_line:
        #     self.workspace_line = Line(pos=np.array((a[0], a[1], a[2], a[3], a[0])), color=(0.70, 0.3, 0.3, 0.7),
        #                                antialias=True, method='agg', parent=self.view.scene)
        # else:
        #     self.workspace_line.parent = self.view.scene
        self.workspace_line = Line(pos=np.array((a[0], a[1], a[2], a[3], a[0])), color=(0.70, 0.3, 0.3, 0.7),
                                   antialias=True, method='agg', parent=self.view.scene)

        self.fcapp.ui.wplace_label.set_value(workspace_size[:3])
        self.fcapp.ui.wplace_label.color = 'tomato'
        self.fcapp.ui.wplace_label.setToolTip(workspace_size)
        self.fcapp.ui.wplace_label.setStyleSheet("""
                        QLabel
                        {
                            color: black;
                            background-color: olivedrab;
                        }
                        """)
        self.fcapp.options['global_workspace'] = True

    def delete_workspace(self):
        try:
            self.workspace_line.parent = None
        except Exception:
            pass
        self.fcapp.ui.wplace_label.setStyleSheet("")
        self.fcapp.options['global_workspace'] = False

    # redraw the workspace lines on the plot by re adding them to the parent view.scene
    def restore_workspace(self):
        try:
            self.workspace_line.parent = self.view.scene
        except Exception:
            pass

    def graph_event_connect(self, event_name, callback):
        return getattr(self.events, event_name).connect(callback)

    def graph_event_disconnect(self, event_name, callback=None):
        if callback is None:
            getattr(self.events, event_name).disconnect()
        else:
            getattr(self.events, event_name).disconnect(callback)

    def zoom(self, factor, center=None):
        """
        Zooms the plot by factor around a given
        center point. Takes care of re-drawing.

        :param factor: Number by which to scale the plot.
        :type factor: float
        :param center: Coordinates [x, y] of the point around which to scale the plot.
        :type center: list
        :return: None
        """
        self.view.camera.zoom(factor, center)

    def new_shape_group(self, shape_collection=None):
        if shape_collection:
            return ShapeGroup(shape_collection)
        return ShapeGroup(self.shape_collection)

    def new_shape_collection(self, **kwargs):
        # sc = ShapeCollection(parent=self.view.scene, pool=self.app.pool, **kwargs)
        # self.shape_collections.append(sc)
        # return sc
        return ShapeCollection(parent=self.view.scene, pool=self.fcapp.pool, fcoptions=self.fcapp.options, **kwargs)

    def new_cursor(self, big=None):
        """
        Will create a mouse cursor pointer on canvas

        :param big: if True will create a mouse cursor made out of infinite lines
        :return: the mouse cursor object
        """
        if big is True:
            self.big_cursor = True
            self.c = CursorBig(app=self.fcapp)

            # in case there are multiple new_cursor calls, best to disconnect first the signals
            try:
                self.c.mouse_state_updated.disconnect(self.on_mouse_state)
            except (TypeError, AttributeError):
                pass
            try:
                self.c.mouse_position_updated.disconnect(self.on_mouse_position)
            except (TypeError, AttributeError):
                pass

            self.c.mouse_state_updated.connect(self.on_mouse_state)
            self.c.mouse_position_updated.connect(self.on_mouse_position)
        else:
            self.big_cursor = False
            self.c = Cursor(pos=np.empty((0, 2)), parent=self.view.scene)
            self.c.antialias = 0

        visuals = (self.cursor_h_line, self.cursor_v_line) if self.big_cursor else (self.c,)
        for visual in visuals:
            visual.visible = self.native.underMouse()
        return self.c

    def eventFilter(self, obj, event):
        if obj is self.native and event.type() == QtCore.QEvent.Type.Paint and self._layout_anchor is None:
            self._layout_window = self.native.window()
            self._layout_origin = self.native.mapTo(self._layout_window, QtCore.QPoint())
        if obj is self.native and event.type() in (QtCore.QEvent.Type.Move, QtCore.QEvent.Type.Resize):
            if (self._layout_anchor is None and self.native.isVisible() and self._layout_origin is not None
                    and self.native.window() is self._layout_window):
                # Parents can move without sending the canvas a Move event.
                origin = self._layout_window.mapToGlobal(self._layout_origin)
                rect = self.view.camera.rect
                self._layout_anchor = (origin, self.translate_coords((0, 0))[:2],
                                       self.pixel_size(), (rect.pos, rect.size))
            # Restore once Qt and VisPy have finished the splitter's move/resize events.
            self._layout_anchor_timer.start(0)
        if obj is self.native and self.c is not None and event.type() in (
                QtCore.QEvent.Type.Enter, QtCore.QEvent.Type.Leave):
            visuals = (self.cursor_h_line, self.cursor_v_line) if self.big_cursor else (self.c,)
            for visual in visuals:
                visual.visible = event.type() == QtCore.QEvent.Type.Enter
        return super().eventFilter(obj, event)

    def _restore_layout_anchor(self):
        anchor, self._layout_anchor = self._layout_anchor, None
        same_window = self.native.window() is self._layout_window
        self._layout_window = self.native.window()
        self._layout_origin = self.native.mapTo(self._layout_window, QtCore.QPoint())
        if anchor is None or not self.native.isVisible() or not same_window:
            return
        origin, point, scale, old_rect = anchor
        local = self.native.mapFromGlobal(origin)
        rect = self.view.camera.rect
        if (rect.pos, rect.size) != old_rect:
            return
        self.view.camera.zoom(scale / self.pixel_size())
        actual = self.translate_coords((local.x(), local.y()))
        self.view.camera.pan((point[0] - actual[0], point[1] - actual[1]))

    def _show_cursor_on_move(self, event=None):
        # a mouse move proves the mouse is over the canvas: a Leave with no Enter after it (a modal dialog, an editor
        # save) must not keep the cursor hidden
        if self.c is None:
            return
        visuals = (self.cursor_h_line, self.cursor_v_line) if self.big_cursor else (self.c,)
        for visual in visuals:
            if not visual.visible:
                visual.visible = True

    @property
    def cursor_color(self):
        return self._cursor_color

    @cursor_color.setter
    def cursor_color(self, color):
        self._cursor_color = Color(color).rgba
        self._cursor_line_color = None
        self.cursor_h_line.set_data(color=self._cursor_color)
        self.cursor_v_line.set_data(color=self._cursor_color)
        self.fcapp.cursor_color_3D = self._cursor_color

    def on_mouse_state(self, state):
        if state:
            self.cursor_h_line.parent = self.view.scene
            self.cursor_v_line.parent = self.view.scene
        else:
            self.cursor_h_line.parent = None
            self.cursor_v_line.parent = None

    def on_mouse_position(self, pos):
        # the Preferences handlers set cursor_color for both states of the 'Cursor Color' checkbox
        color = tuple(self.cursor_color)

        width = _database_option(self.fcapp, "global_cursor_width")
        if width and width != self.cursor_h_line.line_width:
            self.cursor_h_line.line_width = width
            self.cursor_v_line.line_width = width

        position = (pos[0], pos[1])
        if position == self._cursor_position and color == self._cursor_line_color:
            return

        new_color = color if color != self._cursor_line_color else None
        self.cursor_h_line.set_data(pos=pos[1], color=new_color)
        self.cursor_v_line.set_data(pos=pos[0], color=new_color)
        self._cursor_position = position
        self._cursor_line_color = color
        self.view.scene.update()

    def on_mouse_scroll(self, event):
        # key modifiers
        modifiers = event.modifiers

        pan_delta_x = _database_option(self.fcapp, "global_gridx")
        pan_delta_y = _database_option(self.fcapp, "global_gridy")
        curr_pos = event.pos

        # Controlled pan by mouse wheel
        if 'Shift' in modifiers:
            p1 = np.array(curr_pos)[:2]

            if event.delta[1] > 0:
                curr_pos[0] -= pan_delta_x
            else:
                curr_pos[0] += pan_delta_x
            p2 = np.array(curr_pos)[:2]
            self.view.camera.pan(p2 - p1)
        elif 'Control' in modifiers:
            p1 = np.array(curr_pos)[:2]

            if event.delta[1] > 0:
                curr_pos[1] += pan_delta_y
            else:
                curr_pos[1] -= pan_delta_y
            p2 = np.array(curr_pos)[:2]
            self.view.camera.pan(p2 - p1)

        if self.fcapp.grid_status():
            pos_canvas = self.translate_coords(curr_pos)
            pos = self.fcapp.geo_editor.snap(pos_canvas[0], pos_canvas[1])

            # Update cursor
            self.fcapp.app_cursor.set_data(np.asarray([(pos[0], pos[1])]),
                                           symbol='++', edge_color=self.cursor_color,
                                           edge_width=_database_option(self.fcapp, "global_cursor_width"),
                                           size=_database_option(self.fcapp, "global_cursor_size"))

    def new_text_group(self, collection=None):
        if collection:
            return TextGroup(collection)
        else:
            return TextGroup(self.text_collection)

    def new_text_collection(self, **kwargs):
        return TextCollection(parent=self.view.scene, **kwargs)

    def fit_view(self, rect=None, blocking=True):

        # the bounds need the buffers of the deferred redraws
        if not rect and blocking:
            self.shape_collection.flush()

        # Lock updates in other threads
        if blocking:
            self.shape_collection.lock_updates()
        elif not self.shape_collection.update_lock.acquire(False):
            return False

        if not rect:
            rect = Rect(-1, -1, 20, 20)
            try:
                rect.left, rect.right = self.shape_collection.bounds(axis=0)
                rect.bottom, rect.top = self.shape_collection.bounds(axis=1)
            except TypeError:
                pass

        # adjust the view camera to be slightly bigger than the bounds so the shape collection can be seen clearly
        # otherwise the shape collection boundary will have no border
        dx = rect.right - rect.left
        dy = rect.top - rect.bottom
        x_factor = dx * 0.02
        y_factor = dy * 0.02

        rect.left -= x_factor
        rect.bottom -= y_factor
        rect.right += x_factor
        rect.top += y_factor

        # rect.left *= 0.96
        # rect.bottom *= 0.96
        # rect.right *= 1.04
        # rect.top *= 1.04

        # units = self.fcapp.app_units.upper()
        # if units == 'MM':
        #     compensation = 0.5
        # else:
        #     compensation = 0.5 / 25.4
        # rect.left -= compensation
        # rect.bottom -= compensation
        # rect.right += compensation
        # rect.top += compensation

        self.view.camera.rect = rect

        self.shape_collection.unlock_updates()
        if not blocking:
            return True

    def fit_center(self, loc, rect=None):

        # Lock updates in other threads
        self.shape_collection.lock_updates()

        if not rect:
            try:
                rect = Rect(loc[0]-20, loc[1]-20, 40, 40)
            except TypeError:
                pass

        self.view.camera.rect = rect

        self.shape_collection.unlock_updates()

    def clear(self):
        pass

    def redraw(self):
        self.shape_collection.redraw([])
        self.shape_collection.flush()
        self.text_collection.redraw()


class CursorBig(QtCore.QObject):
    """
    Signals cursor visibility and position updates for the VisPy big crosshair cursor.
    """

    mouse_state_updated = QtCore.pyqtSignal(bool)
    mouse_position_updated = QtCore.pyqtSignal(list)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self._enabled = None

    @property
    def enabled(self):
        return True if self._enabled else False

    @enabled.setter
    def enabled(self, value):
        self._enabled = value
        self.mouse_state_updated.emit(value)

    def set_data(self, pos, **kwargs):
        """Internal event handler to draw the cursor when the mouse moves."""
        # if 'edge_color' in kwargs:
        #     color = kwargs['edge_color']
        # else:
        #     if self.app.options['global_theme'] == 'light':
        #         color = '#000000FF'
        #     else:
        #         color = '#FFFFFFFF'

        position = [pos[0][0], pos[0][1]]
        self.mouse_position_updated.emit(position)
