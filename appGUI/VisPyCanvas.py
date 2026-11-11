# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Dennis Hayrullin                            #
# Date: 2/5/2016                                           #
# MIT Licence                                              #
# ##########################################################

from PyQt6.QtGui import QFont, QFontMetricsF, QPalette
from PyQt6.QtCore import QSettings

import numpy as np

import vispy.scene as scene
from vispy.scene.cameras.base_camera import BaseCamera
# from vispy.scene.widgets import Widget as VisPyWidget
from vispy.color import Color
from appGUI.VisPyVisuals import GridLines

import os
import time

white = Color("#ffffff")
black = Color("#000000")


class VisPyCanvas(scene.SceneCanvas):

    def __init__(self, config=None):
        # scene.SceneCanvas.__init__(self, keys=None, config=config)
        # Qt presents the QOpenGLWidget itself; the VisPy swap in paintGL only waits for vertical blanks.
        super().__init__(config=config, keys=None, autoswap=os.environ.get('FLATCAM_VISPY_PATCHES') == '0')

        self.unfreeze()

        # Text uses logical pixels; Qt already handles the device pixel ratio.
        self.dpi = self.native.logicalDpiY()

        settings = QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("axis_font_size"):
            a_fsize = settings.value('axis_font_size', type=int)
        else:
            a_fsize = 6

        if settings.contains("theme"):
            theme = settings.value('theme', type=str)
        else:
            theme = 'default'

        if settings.contains("dark_canvas"):
            dark_canvas = settings.value('dark_canvas', type=bool)
        else:
            dark_canvas = False

        if (theme == 'default' or theme == 'light') and not dark_canvas:
            theme_color = Color('#FFFFFF')
            tick_color = Color('#000000')
            window_color = QPalette().color(QPalette.ColorRole.Window)
            back_color = str(window_color.name())
            if window_color.lightness() < 128:
                # a dark window (Qt could not follow the theme): black ticks and numbers would not be readable
                tick_color = Color('gray')
        else:
            if theme not in ['default', 'light']:
                theme_color = Color('#202124')
                back_color = Color('#202124')
            else:
                theme_color = Color('#000000')
                back_color = Color('#000000')
            tick_color = Color('gray')
            # back_color = Color('#272822') # darker
            # back_color = Color('#3c3f41') # lighter

        self.central_widget.bgcolor = back_color
        self.central_widget.border_color = back_color

        self.grid_widget = self.central_widget.add_grid(margin=0)
        self.grid_widget.spacing = 0

        top_padding = self.grid_widget.add_widget(row=0, col=0, col_span=2)
        top_padding.height_max = 0

        self.yaxis = scene.AxisWidget(
            orientation='left', axis_color=tick_color, text_color=tick_color, font_size=a_fsize, axis_width=1
        )
        # The tick labels sit beside their major tick, past the minor ticks (VisPyPatches.beside_tick_labels): the
        # vertical ones turned 90 degrees CCW. Each axis is as thick as its labels need, from the font metrics.
        self.yaxis.axis._text.rotation = -90
        self.xaxis = scene.AxisWidget(
            orientation='bottom', axis_color=tick_color, text_color=tick_color, font_size=a_fsize, axis_width=1)
        font = QFont(self.xaxis.axis._text.face)
        font.setPointSizeF(self.xaxis.axis.tick_font_size)
        metrics = QFontMetricsF(font, self.native)
        gap_out = 2
        for axis, side in ((self.yaxis, 'left'), (self.xaxis, 'bottom')):
            axis.axis.unfreeze()
            axis.axis.flatcam_beside_labels = {'side': side, 'metrics': metrics, 'gap_along': 3, 'gap_out': gap_out}
            axis.axis.freeze()
        # the labels run from past the minor ticks (still beside the major tick) to their ascent, plus a small gap
        thickness = np.ceil(max(self.xaxis.axis.major_tick_length,
                                self.xaxis.axis.minor_tick_length + gap_out + metrics.ascent()) + 3)
        self.yaxis.width_min = self.yaxis.width_max = thickness
        self.grid_widget.add_widget(self.yaxis, row=1, col=0)
        self.xaxis.height_min = self.xaxis.height_max = thickness
        self.grid_widget.add_widget(self.xaxis, row=2, col=1)

        right_padding = self.grid_widget.add_widget(row=0, col=2, row_span=2)
        right_padding.width_min = 8
        right_padding.width_max = 8

        view = self.grid_widget.add_view(row=1, col=1, border_color=tick_color, bgcolor=theme_color)
        view.camera = Camera(aspect=1, rect=(-25, -25, 150, 150))

        # Following function was removed from 'prepare_draw()' of 'Grid' class by patch,
        # it is necessary to call manually
        self.grid_widget._update_child_widget_dim()

        self.xaxis.link_view(view)
        self.yaxis.link_view(view)

        # grid1 = scene.GridLines(parent=view.scene, color='dimgray')
        # grid1.set_gl_state(depth_test=False)

        settings = QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("theme"):
            theme = settings.value('theme', type=str)
        else:
            theme = 'default'

        if settings.contains("dark_canvas"):
            dark_canvas = settings.value('dark_canvas', type=bool)
        else:
            dark_canvas = False

        self.view = view
        if (theme == 'default' or theme == 'light') and not dark_canvas:
            self.grid = GridLines(parent=self.view.scene, color='dimgray')
        else:
            self.grid = GridLines(parent=self.view.scene, color='#dededeff')
            # the major lines were too bright on the dark canvas: dimmer, but still brighter than the minor lines
            self.grid.set_major_scale(0.5)

        self.grid.set_gl_state(depth_test=False)

        # The pending pan of a drag is applied before the scene is drawn. 'first': before SceneCanvas.on_draw, so
        # that the update request of the new view merges into the draw that is running.
        self.events.draw.connect(self._flush_pan_on_draw, position='first')

        from appGUI.VisPyPatches import hold_gc_during_drag
        hold_gc_during_drag(self)

        self.freeze()

        # self.measure_fps()

    def _flush_pan_on_draw(self, event=None):
        self.view.camera.flush_pan()

    def render(self, *args, **kwargs):
        self.view.camera.flush_pan()
        return super().render(*args, **kwargs)

    def translate_coords(self, pos):
        """
        Translate pixels to FlatCAM units.

        """
        self.view.camera.flush_pan()
        tr = self.grid.get_transform('canvas', 'visual')
        return tr.map(pos)

    def pixel_size(self):
        """
        Plot units per screen pixel.
        """
        a = self.translate_coords((0, 0))
        b = self.translate_coords((1, 0))
        return float(np.hypot(b[0] - a[0], b[1] - a[1]))

    def translate_coords_2(self, pos):
        """
        Translate FlatCAM units to logical canvas pixels (the inverse of translate_coords).
        """
        self.view.camera.flush_pan()
        tr = self.grid.get_transform('visual', 'canvas')
        return tr.map(pos)

    def align_jump(self, location):
        """Pan by a fraction of a pixel to put the target under the integer OS cursor."""
        pixel = np.round(self.translate_coords_2(location)[:2])
        actual = self.translate_coords(pixel)
        self.view.camera.pan((location[0] - actual[0], location[1] - actual[1]))
        return pixel


class Camera(scene.PanZoomCamera):

    # A pan drag is applied one time per frame: the first and the newest cursor position (canvas pixels) of the
    # moves that were not applied yet. PanZoomCamera.__init__ sets the rect before our __init__ body runs.
    _pan_from = None
    _pan_to = None
    _defer_pan = False

    def __init__(self, **kwargs):
        super(Camera, self).__init__(**kwargs)

        from appGUI.VisPyPatches import new_patches_enabled
        self._defer_pan = new_patches_enabled()

        self.minimum_scene_size = 0.01
        self.maximum_scene_size = 10000

        self.last_event = None
        self.last_time = 0

        # Default mouse button for panning is RMB
        self.pan_button_setting = "2"

        self.zoom_callback = lambda *args: None

    def flush_pan(self):
        """Apply the pending pan of a drag: the point under the first cursor position moves to the newest one."""
        p1, p2 = self._pan_from, self._pan_to
        if p1 is None:
            return
        self._pan_from = self._pan_to = None
        self.pan(self._transform.imap(p1) - self._transform.imap(p2))

    # Every reader and writer of the view applies the pending pan first, so that none sees a stale view.
    @property
    def rect(self):
        self.flush_pan()
        return self._rect

    @rect.setter
    def rect(self, args):
        self.flush_pan()
        scene.PanZoomCamera.rect.fset(self, args)

    @property
    def center(self):
        self.flush_pan()
        return scene.PanZoomCamera.center.fget(self)

    @center.setter
    def center(self, center):
        self.flush_pan()
        scene.PanZoomCamera.center.fset(self, center)

    def zoom(self, factor, center=None):
        self.flush_pan()
        center = center if (center is not None) else self.center
        super(Camera, self).zoom(factor, center)

    def viewbox_resize_event(self, event):
        self.flush_pan()
        super(Camera, self).viewbox_resize_event(event)

    def viewbox_mouse_event(self, event):
        """
        The SubScene received a mouse event; update transform
        accordingly.

        Parameters
        ----------
        event : instance of Event
            The event.
        """
        if event.handled or not self.interactive:
            return

        # key modifiers
        modifiers = event.mouse_event.modifiers

        # Limit non-pan input events
        last_event = event.last_event
        t = time.time()
        is_pan_move = (event.type == 'mouse_move' and event.press_event is not None
                       and event.button == int(self.pan_button_setting) and not modifiers)
        if not is_pan_move:
            self.flush_pan()
        if is_pan_move:
            self.last_time = t
            # A pending throttled zoom delta must not become a pan delta or survive the pan.
            self.last_event = None
        elif t - self.last_time > 0.015:
            self.last_time = t
            if self.last_event:
                last_event = self.last_event
                self.last_event = None
        else:
            if not self.last_event:
                self.last_event = last_event
            event.handled = True
            return

        # ################### Scrolling ##########################
        BaseCamera.viewbox_mouse_event(self, event)

        if event.type == 'mouse_wheel':
            if not modifiers:
                center = self._scene_transform.imap(event.pos)
                scale = (1 + self.zoom_factor) ** (-event.delta[1] * 30)
                self.limited_zoom(scale, center)

            if self.zoom_callback:
                self.zoom_callback()
            event.handled = True

        elif event.type == 'mouse_move':
            if event.press_event is None:
                return

            # ################ Panning ############################
            # self.pan_button_setting is actually self.FlatCAM.APP.defaults['global_pan_button']
            if event.button == int(self.pan_button_setting) and not modifiers:
                # Translate
                p1 = np.array(last_event.pos)[:2]
                p2 = np.array(event.pos)[:2]
                if self._defer_pan:
                    if self._pan_from is None:
                        self._pan_from = p1
                    self._pan_to = p2
                    self._viewbox.update()
                else:
                    p1s = self._transform.imap(p1)
                    p2s = self._transform.imap(p2)
                    self.pan(p1s-p2s)
                event.handled = True
            elif event.button in [2, 3] and 'Shift' in modifiers:
                # Zoom
                p1c = np.array(last_event.pos)[:2]
                p2c = np.array(event.pos)[:2]
                scale = ((1 + self.zoom_factor) **
                         ((p1c-p2c) * np.array([1, -1])))
                center = self._transform.imap(event.press_event.pos[:2])
                self.limited_zoom(scale, center)
                event.handled = True
            else:
                event.handled = False
        elif event.type == 'mouse_press':
            # accept the event if it is button 1 or 2.
            # This is required in order to receive future events
            event.handled = event.button in [1, 2, 3]
        else:
            event.handled = False

    def limited_zoom(self, scale, center):

        try:
            zoom_in = scale[1] < 1
        except IndexError:
            zoom_in = scale < 1

        if (not zoom_in and self.rect.width < self.maximum_scene_size) \
                or (zoom_in and self.rect.width > self.minimum_scene_size):
            self.zoom(scale, center)
