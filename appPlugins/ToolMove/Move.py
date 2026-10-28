# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore
from appTool import AppTool
from appHandlers.appHistory import history_of, supports_matrix, transform_metadata
from camlib import affine_translate_matrix
from appPlugins.ToolMove.MoveGen import selection_corners, translated_box, selection_shape
from appPlugins.ToolMove.MoveUI import MoveUI

import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import copy

from shapely import Polygon

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolMove(AppTool):

    pluginName = _("Move")
    replot_signal = QtCore.pyqtSignal(list)
    move_finished = QtCore.pyqtSignal(object)

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app
        self.decimals = self.app.decimals

        self.ui = MoveUI(self)

        self.clicked_move = 0
        self._move_in_progress = False

        self.point1 = None
        self.point2 = None

        # the default state is disabled for the Move command
        self.setVisible(False)

        self.sel_rect = None
        self.old_coords = []

        self.sel_shapes = self.app.sel_shapes

        self.replot_signal[list].connect(self.replot)
        self.move_finished.connect(self._on_move_finished)

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='M', **kwargs)

    def run(self, toggle):
        self.app.defaults.report_usage("ToolMove()")

        if self.app.plugin_tab_locked is True:
            return
        self.toggle()

    def toggle(self, toggle=False):
        if self.isVisible():
            self.setVisible(False)

            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_move)
            self.app.plotcanvas.graph_event_disconnect('mouse_press', self.on_left_click)
            self.app.plotcanvas.graph_event_disconnect('key_release', self.on_key_press)
            self.app.plotcanvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)

            self.clicked_move = 0

            # signal that there is no command active
            self.app.command_active = None

            # delete the selection box
            self.delete_shape()
            return
        else:
            self.setVisible(True)
            # signal that there is a command active, and it is 'Move'
            self.app.command_active = "Move"

            sel_obj_list = [obj for obj in self.app.collection.get_selected()
                            if obj.obj_options['plot'] and obj.visible is True]
            if sel_obj_list:
                self.app.inform.emit(_("MOVE: Click on the Start point ..."))

                # if we have an object selected then we can safely activate the mouse events
                self.app.plotcanvas.graph_event_connect('mouse_move', self.on_move)
                self.app.plotcanvas.graph_event_connect('mouse_press', self.on_left_click)
                self.app.plotcanvas.graph_event_connect('key_release', self.on_key_press)

                # draw the selection box
                self.draw_sel_bbox()
            else:
                self.toggle()
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. No object(s) to move."))

    def on_left_click(self, event):
        # mouse click will be accepted only if the left button is clicked
        # this is necessary because right mouse click and middle mouse click
        # are used for panning on the canvas

        event_pos = event.pos
        if event.button == 1:
            if self.clicked_move == 0:
                pos_canvas = self.app.plotcanvas.translate_coords(event_pos)

                # if GRID is active we need to get the snapped positions
                if self.app.grid_status():
                    pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
                else:
                    pos = pos_canvas

                if self.point1 is None:
                    self.point1 = pos
                else:
                    self.point2 = copy(self.point1)
                    self.point1 = pos
                self.app.inform.emit(_("Click on the DESTINATION point ..."))

            if self.clicked_move == 1:
                try:
                    pos_canvas = self.app.plotcanvas.translate_coords(event_pos)

                    # delete the selection bounding box
                    self.delete_shape()

                    # if GRID is active we need to get the snapped positions
                    if self.app.grid_status():
                        pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
                    else:
                        pos = pos_canvas

                    dx = pos[0] - self.point1[0]
                    dy = pos[1] - self.point1[1]

                    self.move_handler(offset=(dx, dy))

                    self.clicked_move = 0
                    self.toggle()
                    return

                except TypeError as e:
                    self.app.log.debug("ToolMove.on_left_click() --> %s" % str(e))
                    self.app.inform.emit('[ERROR_NOTCL] ToolMove. %s' % _('Error when mouse left click.'))
                    return

            self.clicked_move = 1

    def replot(self, obj_list):

        def worker_task():
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                for sel_obj in obj_list:
                    sel_obj.plot()

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def move_handler(self, offset, objects=None):
        """
        Actual move is done here.

        :param offset:  How much to move objects on both directions
        :type offset:   tuple
        :param objects: objects to move
        :type objects:  [list, None]
        :return:
        :rtype:
        """

        try:
            dx, dy = offset
            dx = float(dx)
            dy = float(dy)
        except (TypeError, ValueError, OverflowError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Invalid move offset."))
            return "fail"
        if not (math.isfinite(dx) and math.isfinite(dy)):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Invalid move offset."))
            return "fail"
        if dx == 0 and dy == 0:
            return

        if self._move_in_progress:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("A move is already in progress. Wait for it to finish, then retry."))
            return "fail"

        if objects is None:
            # move only the objects selected and plotted and visible
            obj_list = [
                obj for obj in self.app.collection.get_selected() if obj.obj_options['plot'] and obj.visible is True
            ]
        else:
            obj_list = list(objects)

        if not obj_list:
            self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))
            return "fail"

        # Qt widgets belong to the GUI thread, not the movement worker.
        for sel_obj in obj_list:
            if sel_obj.kind == 'gerber':
                sel_obj.ui.aperture_table_visibility_cb.setChecked(False)

        result = {'updates': [], 'error': None}

        def offset_one(sel_obj):
            error = None
            try:
                if sel_obj.offset((dx, dy)) == 'fail':
                    error = 'Object movement failed'
                elif sel_obj.kind == 'cncjob' and sel_obj.regenerate_source() is False:
                    # 'Export G-Code' writes source_file; it is in the Undo snapshot of a CNCJob, so make it here
                    error = 'Source regeneration failed'
            except Exception as err:
                self.app.log.exception('ToolMove.move_handler()')
                error = str(err)
            try:
                bounds = sel_obj.bounds()
            except Exception as err:
                self.app.log.exception('ToolMove.move_handler() bounds')
                return None, error or str(err)
            return bounds, error

        def job_move(app_obj):
            try:
                with self.app.proc_container.new('%s...' % _("Moving")):
                    # The vectorized Shapely calls of offset() release the GIL, so the objects move in parallel.
                    # Each object owns its geometry; plot() stays sequential because the canvas is not thread-safe.
                    # The move is 1 Undo / Redo step: a matrix for the objects that have a matrix path, a snapshot
                    # for the others (CNCJob).
                    history = history_of(self.app)
                    label = _("Move")
                    no_matrix = [obj for obj in obj_list if not supports_matrix(obj)]
                    before = {id(obj): transform_metadata(obj) if history.is_recording() else {}
                              for obj in obj_list if supports_matrix(obj)}
                    t_start = time.perf_counter()
                    with history.group(label), history.snapshot(label, no_matrix):
                        if len(obj_list) == 1:
                            offset_results = [offset_one(obj_list[0])]
                        else:
                            workers = min(len(obj_list), os.cpu_count() or 1)
                            with ThreadPoolExecutor(max_workers=workers) as executor:
                                offset_results = list(executor.map(offset_one, obj_list))
                        # an object that failed is left out: its state is not known
                        history.record_matrix(label, [
                            (obj, affine_translate_matrix(dx, dy), before[id(obj)])
                            for obj, (_bounds, error) in zip(obj_list, offset_results)
                            if error is None and supports_matrix(obj)])
                    t_offset = time.perf_counter() - t_start

                    for sel_obj, (bounds, error) in zip(obj_list, offset_results):
                        if bounds is not None:
                            result['updates'].append((sel_obj, bounds))
                        if error is not None and result['error'] is None:
                            result['error'] = error

                    # Plot before exporting, and refresh even a partially moved object on failure.
                    t_plot = t_source = 0.0
                    for sel_obj in obj_list:
                        try:
                            t_start = time.perf_counter()
                            sel_obj.plot()
                            t_plot += time.perf_counter() - t_start
                            if sel_obj.kind in ('gerber', 'excellon'):
                                t_start = time.perf_counter()
                                regenerated = sel_obj.regenerate_source()
                                t_source += time.perf_counter() - t_start
                                if regenerated is False:
                                    raise ValueError('Source regeneration failed')
                        except Exception as err:
                            app_obj.app.log.exception('ToolMove.move_handler() refresh')
                            result['error'] = str(err)

                    app_obj.app.log.debug("ToolMove.move_handler() %d objects: offset %.3fs, plot %.3fs, source %.3fs" %
                                          (len(obj_list), t_offset, t_plot, t_source))
            except Exception as err:
                app_obj.app.log.exception('ToolMove.move_handler()')
                result['error'] = str(err)
            finally:
                # always reach the GUI: the in-flight state is released there
                app_obj.move_finished.emit(result)

        self.app.should_we_save = True
        self._move_in_progress = True
        try:
            self.app.worker_task.emit({'fcn': job_move, 'params': [self]})
        except Exception:
            self._move_in_progress = False
            raise

    def _on_move_finished(self, result):
        self._move_in_progress = False
        try:
            for obj, bounds in result['updates']:
                for key, value in zip(('xmin', 'ymin', 'xmax', 'ymax'), bounds):
                    obj.obj_options[key] = value
                try:
                    obj.set_offset_values(bounds=bounds)
                except AttributeError:
                    pass
            self.app.delete_selection_shape()
        except Exception as err:
            self.app.log.exception('ToolMove GUI refresh failed')
            result['error'] = str(err)
        if result['error'] is not None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed."), result['error']))
        else:
            self.app.inform.emit('[success] %s' % _("Done."))

    def on_move(self, event):
        event_pos = event.pos
        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        pos_canvas = self.app.plotcanvas.translate_coords((x, y))

        # if GRID is active we need to get the snapped positions
        if self.app.grid_status():
            pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
        else:
            pos = pos_canvas

        if self.point1 is None:
            dx = pos[0]
            dy = pos[1]
        else:
            dx = pos[0] - self.point1[0]
            dy = pos[1] - self.point1[1]

        if self.clicked_move == 1:
            self.update_sel_bbox((dx, dy))

    def on_key_press(self, event):
        if event.key == 'escape':
            # abort the move action
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            self.toggle()
        return

    def draw_sel_bbox(self):
        obj_list = self.app.collection.get_selected()
        self.old_coords = selection_corners([
            obj.bounds() for obj in obj_list if obj.obj_options['plot'] and obj.visible is True
        ])
        self.draw_shape(Polygon(self.old_coords))

        self.sel_shapes.redraw()

    def update_sel_bbox(self, pos):
        self.sel_shapes.clear()

        self.draw_shape(translated_box(self.old_coords, pos))

        self.sel_shapes.redraw()

    def delete_shape(self):
        self.sel_shapes.clear()
        self.sel_shapes.redraw()

    def draw_shape(self, shp):
        as_line = self.app.options.get(
                'global_selection_shape_as_line',
                self.app.defaults.get(
                    'global_selection_shape_as_line',
                    self.app.defaults.factory_defaults.get('global_selection_shape_as_line'))) is True
        proc_shape = selection_shape(shp, self.app.app_units, as_line)
        MoveUI.draw_shape(self.sel_shapes, proc_shape)

# end of file
