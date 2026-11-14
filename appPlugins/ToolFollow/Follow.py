# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/12/2020                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolFollow.FollowUI import FollowUI
from appPlugins.ToolFollow import FollowGen
from appCommon.Common import GracefulException
from appHandlers.appHistory import history_of
from appObjects.ProjectContext import unique_name
from defaults import AppDefaults

import logging
import traceback

import numpy as np

from shapely import Polygon

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def _base_name(name):
    """The object name without the file extension."""
    base = name.rpartition('.')[0]
    return base if base != '' else name


class ToolFollow(AppTool):
    """
    The values are read in the GUI thread (collect). The paths are computed by one job on the app worker; the job
    ends with one job_done signal and the Geometry objects are created in the GUI thread (finish).
    The Gerber source objects are never changed.
    """

    # (kind, payload): 'preview' list of lines | 'result' (params, [(source, groups)]) | 'cancelled' | 'error' text
    job_done = QtCore.pyqtSignal(str, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)

        self._busy = False
        self._preview_drawn = False
        self.job_done.connect(self.on_job_done)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = FollowUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # the area selection
        self.selecting = False
        self.pending_params = None
        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False
        self.sel_rect = []
        self.cursor_width = 1
        self.cursor_size = 20

        # store here the points for the "Polygon" area selection shape
        self.points = []
        # set this as True when in middle of drawing a "Polygon" area selection shape
        # it is made False by first click to signify that the shape is complete
        self.poly_drawn = False

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+Y', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolFollow()")

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            # if the Tool Tab is hidden display it, else hide it but only if the objectName is the same
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            # show the Tab
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)

                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                # focus on Tool Tab
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    # if the Tool Tab is not focused, focus on it
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        # focus on Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        # else remove the Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Follow"))

    def connect_signals_at_init(self):
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.select_method_radio.activated_custom.connect(self.ui.on_selection)
        self.ui.object_combo.currentIndexChanged.connect(self.on_source_changed)
        self.ui.generate_geometry_button.clicked.connect(self.on_generate_geometry_click)
        self.ui.preview_button.clicked.connect(self.on_preview)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def _opt(self, key):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

    def on_plugin_cleanup(self):
        self.end_area_selection()
        self.clear_preview()

    def set_tool_ui(self):
        self.end_area_selection()
        self.clear_preview()
        self.clear_ui(self.layout)
        self.ui = FollowUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.units = self.app.app_units.upper()
        self.ui.tol_units_label.setText(self.app.app_units.lower())

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.object_combo.set_value(obj_name)
        self.on_source_changed()

        # Set UI
        self.ui.simplify_cb.set_value(self._opt("tools_follow_simplification"))
        self.ui.tol_entry.set_value(self._opt("tools_follow_tolerance"))
        self.ui.union_cb.set_value(self._opt("tools_follow_union"))
        self.ui.merge_cb.set_value(self._opt("tools_follow_merge"))
        self.ui.dia_mode_radio.set_value(self._opt("tools_follow_dia_mode"))
        self.ui.select_method_radio.set_value(self._opt("tools_follow_select"))
        self.ui.area_shape_radio.set_value(self._opt("tools_follow_area_shape"))
        self.ui.on_selection(self.ui.select_method_radio.get_value())

        # Show/Hide Advanced Options
        self.change_level(self._opt("global_app_level"))
        self._set_busy(self._busy)

    def on_source_changed(self, *args):
        obj = self._combo_object()
        try:
            self.ui.populate_apertures(obj.tools if obj is not None else {})
        except RuntimeError:
            pass

    def change_level(self, level):
        """
        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """

        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        self.ui.set_level(checked)

    # ------------------------------------------------------------------------------------------------------------
    # the values (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def _combo_object(self):
        obj_name = self.ui.object_combo.currentText()
        try:
            obj = self.app.collection.get_by_name(obj_name)
        except Exception as e:
            log.debug("ToolFollow._combo_object() --> %s" % str(e))
            return None
        return obj if obj is not None and getattr(obj, 'kind', None) == 'gerber' else None

    def collect(self):
        """Read the form and look the Gerber objects up. Returns a dict, or None after a message."""
        try:
            tooldia = FollowGen.tool_dias(self._opt("tools_mill_tooldia"))[0]
        except (ValueError, TypeError) as e:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (
                _("Invalid tool diameter in the Milling preferences"), str(e)))
            return None

        combo_obj = self._combo_object()
        sources = [o for o in self.app.collection.get_selected() if getattr(o, 'kind', None) == 'gerber']
        if not sources and combo_obj is not None:
            sources = [combo_obj]
        if not sources:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No Gerber object is selected."))
            return None

        ui = self.ui
        apertures = None
        if self.ui.level.isChecked():
            apertures = ui.selected_apertures()
            if apertures is not None and not (len(sources) == 1 and sources[0] is combo_obj):
                apertures = None
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "The aperture selection is used only for the single object in the list. All the apertures "
                    "are followed."))

        return {
            'sources': sources,
            # the job reads these copies, never the live objects
            'snapshots': [FollowGen.snapshot(src) for src in sources],
            'simplify': bool(ui.simplify_cb.get_value()),
            'tolerance': float(ui.tol_entry.get_value()),
            'union': bool(ui.union_cb.get_value()),
            'merge': bool(ui.merge_cb.get_value()),
            'dia_mode': ui.dia_mode_radio.get_value(),
            'select': ui.select_method_radio.get_value(),
            'area_shape': ui.area_shape_radio.get_value(),
            'apertures': apertures,
            'selection': None,
            'tooldia': tooldia,
            'decimals': self.decimals,
            'suffix': str(self._opt("tools_follow_suffix") or ''),
        }

    def _set_busy(self, busy):
        self._busy = busy
        try:
            self.ui.generate_geometry_button.setEnabled(not busy)
            self.ui.preview_button.setEnabled(not busy)
        except RuntimeError:
            pass

    def _start(self, params, mode):
        if self._busy:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Another Follow run is still working."))
            return
        self._set_busy(True)
        self.app.inform.emit('%s' % _("Working..."))
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [params, mode]})

    def on_generate_geometry_click(self):
        if self._busy:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Another Follow run is still working."))
            return
        params = self.collect()
        if params is None:
            return
        self.clear_preview()

        if params['select'] == 'all':
            self._start(params, 'apply')
        else:
            self.start_area_selection(params)

    def on_preview(self):
        if self._busy:
            return
        params = self.collect()
        if params is None:
            return
        # the preview shows the first source, the whole object
        params['sources'] = params['sources'][:1]
        params['snapshots'] = params['snapshots'][:1]
        self.clear_preview()
        self._start(params, 'preview')

    # ------------------------------------------------------------------------------------------------------------
    # the job (worker thread): geometry only, no Qt widgets, no collection changes, no area selection state
    # ------------------------------------------------------------------------------------------------------------
    def check_abort(self):
        if getattr(self.app, 'abort_flag', False):
            raise GracefulException()

    def worker_job(self, params, mode):
        kind, payload = 'error', ''
        gen_params = {key: params[key] for key in
                      ('union', 'simplify', 'tolerance', 'merge', 'selection', 'apertures', 'dia_mode', 'tooldia')}
        try:
            with self.app.proc_container.new('%s...' % _("Working")):
                results = []
                for src, snap in zip(params['sources'], params['snapshots']):
                    self.check_abort()
                    groups = FollowGen.follow_groups(snap, gen_params, check_abort=self.check_abort)
                    results.append((src, groups))
                if mode == 'preview':
                    kind, payload = 'preview', [line for _dia, lines in results[0][1] for line in lines]
                else:
                    kind, payload = 'result', (params, results)
        except GracefulException:
            kind, payload = 'cancelled', None
        except Exception as e:
            log.error("ToolFollow.worker_job() --> %s\n%s" % (str(e), traceback.format_exc()))
            kind, payload = 'error', str(e)
        self.job_done.emit(kind, payload)

    # ------------------------------------------------------------------------------------------------------------
    # the end of the job (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def on_job_done(self, kind, payload):
        self._set_busy(False)
        try:
            if kind == 'preview':
                self.draw_preview(payload)
            elif kind == 'result':
                self.finish(*payload)
            elif kind == 'cancelled':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Follow failed"), payload))
        except Exception as e:
            log.error("ToolFollow.on_job_done() --> %s\n%s" % (str(e), traceback.format_exc()))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Follow failed"), str(e)))

    def finish(self, params, results):
        """Create one Geometry object per source that has paths, as ONE Undo step."""
        kept = []
        for src, groups in results:
            if not groups:
                if not params['selection']:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % (
                        _("No paths to follow in %(name)s.") % {'name': src.obj_options['name']}))
                continue
            kept.append((src, groups))
        if not kept:
            if params['selection']:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The selected area holds no paths."))
            return

        collection = self.app.collection
        history = history_of(self.app)
        created = []
        failed = []
        with history.group(self.pluginName):
            for src, groups in kept:
                names = list(collection.get_names()) + created + failed
                outname = unique_name(_base_name(src.obj_options['name']) + params['suffix'], names)
                data = FollowGen.geometry_data(self.app.options, outname)
                tools = FollowGen.follow_tools(groups, data, params['decimals'])

                def init_func(new_obj, app_obj, tools=tools, tooldia=groups[0][0]):
                    new_obj.obj_options["tools_mill_tooldia"] = tooldia
                    new_obj.tools = tools

                ret = self.new_object('geometry', outname, init_func, source=src)
                if ret is None or (isinstance(ret, str) and ret == 'fail'):
                    failed.append(outname)
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to create Follow Geometry"), outname))
                    continue
                created.append(outname)

        if not created:
            return
        self.store_values(params)
        if failed:
            self.app.inform.emit('[WARNING_NOTCL] %s' % (
                _("Created %(count)d of %(total)d Follow Geometry objects: %(names)s. Failed: %(failed)s.") %
                {'count': len(created), 'total': len(kept), 'names': ', '.join(created),
                 'failed': ', '.join(failed)}))
            return
        self.app.inform.emit('[success] %s' % (
            _("Created %(count)d Follow Geometry object(s): %(names)s.") %
            {'count': len(created), 'names': ', '.join(created)}))

    def store_values(self, params):
        """Keep the values used for the session."""
        self.app.options["tools_follow_simplification"] = params['simplify']
        self.app.options["tools_follow_tolerance"] = params['tolerance']
        self.app.options["tools_follow_union"] = params['union']
        self.app.options["tools_follow_merge"] = params['merge']
        self.app.options["tools_follow_dia_mode"] = params['dia_mode']
        self.app.options["tools_follow_select"] = params['select']
        self.app.options["tools_follow_area_shape"] = params['area_shape']

    # ------------------------------------------------------------------------------------------------------------
    # the preview (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def draw_preview(self, lines):
        self.clear_preview()
        shapes = self.app.tool_shapes
        if shapes is None:
            return
        if not lines:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There are no paths to follow."))
            return
        for line in lines:
            shapes.add(line, color='#00AA00FF', face_color='#00AA0000', update=False, layer=0, tolerance=None)
        shapes.redraw()
        self._preview_drawn = True

    def clear_preview(self):
        if self._preview_drawn and self.app.tool_shapes is not None:
            self.app.tool_shapes.clear(update=True)
        self._preview_drawn = False

    # ------------------------------------------------------------------------------------------------------------
    # the area selection (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def start_area_selection(self, params):
        self.end_area_selection()
        self.pending_params = params
        self.cursor_width = self._opt("global_cursor_width")
        self.cursor_size = self._opt("global_cursor_size")

        # disable the "notebook UI" until finished
        self.app.ui.notebook.setDisabled(True)

        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the start point of the area."))

        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

        self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_release)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)
        self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)
        self.selecting = True

    def end_area_selection(self):
        """Leave the area selection: reset its state, delete its shapes, restore the app handlers."""
        if not self.selecting:
            return
        self.selecting = False
        self.first_click = False
        self.cursor_pos = None
        self.points = []
        self.poly_drawn = False
        self.sel_rect = []
        self.pending_params = None

        try:
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
        except Exception as e:
            log.error("ToolFollow.end_area_selection() --> %s" % str(e))

        self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

        self.app.delete_selection_shape()
        self.delete_moving_selection_shape()
        self.delete_tool_selection_shape()

        self.app.ui.notebook.setDisabled(False)

    def finish_area_selection(self):
        """Right click without a pending zone: run the job on the zones, or say that there are none."""
        params = self.pending_params
        zones = list(self.sel_rect)
        self.end_area_selection()
        if not zones or params is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No zone was drawn. Nothing was created."))
            return
        params['selection'] = zones
        self._start(params, 'apply')

    # To be called after clicking on the plot.
    def on_mouse_release(self, event):
        event_pos = event.pos
        right_button = 2

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        event_pos = (x, y)

        shape_type = self.ui.area_shape_radio.get_value()

        curr_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])

        x1, y1 = curr_pos[0], curr_pos[1]

        if event.button == 1:
            if shape_type == "square":
                if not self.first_click:
                    self.first_click = True
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the end point of the area."))

                    self.cursor_pos = (x1, y1)
                else:
                    self.app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    self.app.delete_selection_shape()

                    x0, y0 = self.cursor_pos[0], self.cursor_pos[1]
                    self.sel_rect.append(Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))

                    # add a temporary shape on canvas
                    self.draw_tool_selection_shape(old_coords=(x0, y0), coords=(x1, y1))

                    self.first_click = False
                    return
            else:
                self.points.append((x1, y1))

                if len(self.points) > 1:
                    self.poly_drawn = True
                    self.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))

                return ""
        elif event.button == right_button and self.mouse_is_dragging is False:
            if shape_type == "square":
                # a right click between the 2 corners drops the half drawn zone
                if self.first_click:
                    self.first_click = False
                    self.cursor_pos = None
                    self.delete_moving_selection_shape()
            else:
                # if we finish to add a polygon
                if self.poly_drawn is True:
                    try:
                        # try to add the point where we last clicked if it is not already in the self.points
                        last_pt = (x1, y1)
                        if last_pt != self.points[-1]:
                            self.points.append(last_pt)
                    except IndexError:
                        pass

                    # we need to add a Polygon and a Polygon can be made only from at least 3 points
                    if len(self.points) > 2:
                        self.delete_moving_selection_shape()
                        pol = Polygon(self.points)
                        # do not add invalid polygons even if they are drawn by utility geometry
                        if pol.is_valid:
                            self.sel_rect.append(pol)
                            self.draw_selection_shape_polygon(points=self.points)
                            self.app.inform.emit(
                                _("Zone added. Click to start adding next zone or right click to finish."))

                    self.points = []
                    self.poly_drawn = False
                    return

            self.finish_area_selection()

    # called on mouse move
    def on_mouse_move(self, event):
        shape_type = self.ui.area_shape_radio.get_value()

        event_pos = event.pos
        event_is_dragging = event.is_dragging

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        curr_pos = self.app.plotcanvas.translate_coords((x, y))

        # detect mouse dragging motion
        self.mouse_is_dragging = bool(event_is_dragging == 1)

        # update the cursor position
        if self.app.grid_status():
            # Update cursor
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])

            self.app.app_cursor.set_data(np.asarray([(curr_pos[0], curr_pos[1])]),
                                         symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                         edge_width=self.cursor_width, size=self.cursor_size)

        if self.cursor_pos is None:
            self.cursor_pos = (0, 0)

        self.app.dx = curr_pos[0] - float(self.cursor_pos[0])
        self.app.dy = curr_pos[1] - float(self.cursor_pos[1])

        self.app.ui.update_location_labels(self.app.dx, self.app.dy, curr_pos[0], curr_pos[1])
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, curr_pos[0], curr_pos[1])

        # draw the utility geometry
        if shape_type == "square":
            if self.first_click:
                self.app.draw_moving_selection_shape(old_coords=(self.cursor_pos[0], self.cursor_pos[1]),
                                                     coords=(curr_pos[0], curr_pos[1]))
        else:
            self.draw_moving_selection_shape_poly(points=self.points, data=(curr_pos[0], curr_pos[1]))

    def on_key_press(self, event):
        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if type(event) is int:
            key = event
        # events from the GUI are of type QKeyEvent
        elif type(event) == QtGui.QKeyEvent:
            key = event.key()

        # events from Vispy are of type KeyEvent
        else:
            key = getattr(event, 'key', None)
            # a Vispy Key object compares with its name
            key = getattr(key, 'name', key)

        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':
            if self.selecting:
                self.end_area_selection()
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Area selection cancelled."))
