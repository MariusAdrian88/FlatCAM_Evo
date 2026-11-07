"""PCB preview controller: snapshots on the GUI thread, serial cached builds on the worker."""

import os
import re
import tempfile
from threading import Event, Lock

from PyQt6 import QtCore, QtWidgets

from appTool import AppTool
from appGUI.GUIElements import FCFileSaveDialog, VerticalScrollArea
from . import PDF3DWriter
from .PCBViewerExport import export_bounds, export_meshes, render_poster
from .PCBViewerGen import PCBGeometry, Source
from .PCBViewerUI import PCBViewerUI
from .PCBViewerView import PCBView
from .U3DWriter import write_scene

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolPCBViewer(AppTool):
    pluginName = _("PCB 3D Viewer")
    layer_ready = QtCore.pyqtSignal(int, object)
    build_done = QtCore.pyqtSignal(int, str)
    export_done = QtCore.pyqtSignal(int, str, str)

    def __init__(self, app):
        super().__init__(app)
        self.ui = None
        self.view = None
        self._engine = PCBGeometry()
        self._sources = {}
        self._token = 0
        self._running = False
        self._pending = False
        self._active = False
        self._fit = False
        self._ready = False
        self._completed_layers = ()
        self._completed_units = None
        self._exporting = False
        self._export_job = 0
        self._export_cancel = Event()
        self._export_lock = Lock()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.on_redraw)
        self.layer_ready.connect(self.on_layer_ready, QtCore.Qt.ConnectionType.QueuedConnection)
        self.build_done.connect(self.on_build_done, QtCore.Qt.ConnectionType.QueuedConnection)
        self.export_done.connect(self.on_export_done, QtCore.Qt.ConnectionType.QueuedConnection)
        app.cleanup.connect(self.on_plugin_cleanup)
        app.app_obj.object_changed.connect(self.on_object_changed)
        app.object_status_changed.connect(self.on_object_changed)
        app.ui.plot_tab_area.tab_closed_signal.connect(self.on_plot_tab_closed)

    def event(self, event):
        if (event.type() == QtCore.QEvent.Type.ParentChange and self.parent() is None and
                getattr(self, '_active', False)):
            self.on_plugin_cleanup()
        return super().event(event)

    def run(self, toggle=True, source=None):
        if self.app.plugin_tab_locked:
            return
        self.app.defaults.report_usage('ToolPCBViewer()')
        # Running again refreshes the preview without closing its canvas.
        self._active = False
        self.on_plugin_cleanup()
        notebook = self.app.ui.notebook
        if notebook.indexOf(self.app.ui.plugin_tab) < 0:
            try:
                notebook.addTab(self.app.ui.plugin_tab, self.pluginName)
            except RuntimeError:
                self.app.ui.plugin_tab = QtWidgets.QWidget()
                self.app.ui.plugin_tab.setObjectName('plugin_tab')
                self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)
                self.app.ui.plugin_scroll_area = VerticalScrollArea()
                self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                notebook.addTab(self.app.ui.plugin_tab, self.pluginName)
        if self.app.ui.splitter.sizes() and self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])
        super().run()
        self.clear_ui(self.layout)
        self.ui = PCBViewerUI(self.layout, self.app)
        self.ui.export_pdf_button.clicked.connect(self.on_export_pdf)
        source = source or self.app.collection.get_active()
        if source is not None and source.kind == 'gerber':
            for name in ('copper_top', 'copper_bottom', 'overlay_top', 'overlay_bottom'):
                self.ui.combos[name].set_value(source.obj_options['name'])
        gerbers = [(obj.obj_options['name'], re.sub('[^a-z0-9]', '', obj.obj_options['name'].lower()))
                   for obj in self.app.collection.get_list() if obj.kind == 'gerber']
        for role, aliases in (
                ('outline', ('gko', 'edgecuts', 'keepout')),
                ('copper_top', ('gtl', 'topcopper', 'toplayer', 'fcu')),
                ('copper_bottom', ('gbl', 'bottomcopper', 'bottomlayer', 'bcu')),
                ('overlay_top', ('gto', 'topoverlay', 'fsilks')),
                ('overlay_bottom', ('gbo', 'bottomoverlay', 'bsilks')),
                ('soldermask_top', ('gts', 'topsolder', 'fmask')),
                ('soldermask_bottom', ('gbs', 'bottomsolder', 'bmask'))):
            match = next((name for name, normalized in gerbers if any(alias in normalized for alias in aliases)), None)
            if match is not None:
                self.ui.combos[role].set_value(match)
        for combo in self.ui.combos.values():
            combo.currentIndexChanged.connect(self.on_selection_changed)
        for widget in (self.ui.board_thickness, self.ui.copper_thickness, self.ui.mask_thickness, self.ui.board_alpha):
            widget.valueChanged.connect(self.on_material_changed)
        for entry in self.ui.colors.values():
            entry.value_changed.connect(self.on_material_changed)
        self.ui.board_alpha.valueChanged.connect(self.on_alpha_changed)
        self.ui.colors['board'].value_changed.connect(self.on_board_color_changed)
        self._active = True
        self._fit = True
        notebook.setTabText(notebook.indexOf(self.app.ui.plugin_tab), self.pluginName)
        self.app.on_3d_area()
        canvas = getattr(self.app, 'plotcanvas3d', None)
        if canvas is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The Plot 3D tab is not available."))
            return
        self.view = PCBView(canvas)
        self.ui.camera_reference.activated_custom.connect(self.on_camera_reference_changed)
        self.on_camera_reference_changed(self.ui.camera_reference.get_value())
        canvas.native.destroyed.connect(lambda: self.on_canvas_destroyed(canvas))
        self.on_selection_changed()
        self._timer.stop()
        self.on_redraw()

    def on_camera_reference_changed(self, value):
        if self.view is not None and self.view.alive:
            self.view.canvas.camera_reference = 'center_rotate' if value == 'center' else value

    def on_alpha_changed(self, value):
        entry = self.ui.colors['board']
        if len(entry.get_value()) >= 7:
            blocker = QtCore.QSignalBlocker(entry)
            entry.set_value(entry.get_value()[:7] + '%02X' % value)
            del blocker

    def on_board_color_changed(self, value):
        try:
            alpha = int(value[7:9], 16) if len(value) == 9 else 255
        except ValueError:
            return
        self.ui.board_alpha.set_value(alpha)

    def on_selection_changed(self, *args):
        if not self._active:
            return
        self._token += 1
        self._pending = True
        self._completed_layers = ()
        self._ready = False
        self.update_export_button()
        self._fit = True
        self._timer.start()

    def on_object_changed(self, obj=None, *args):
        if not self._active:
            return
        self._sources.clear()
        self.on_selection_changed()

    def on_material_changed(self, *args):
        if not self._active or self.view is None or not self.view.alive:
            return
        self._timer.start()

    def on_redraw(self):
        if not self._active or self.view is None or not self.view.alive:
            return
        if not self._pending:
            try:
                self.view.update_style(self.ui.values(), self.app.app_units)
                if not self.view.canvas.view.camera._view_modified:
                    self.view.canvas.fit_view()
                self._ready = (any(layer.name == 'board' and len(layer.faces) for layer in self._completed_layers)
                               and not self._running)
            except ValueError as err:
                self._ready = False
                self.ui.status.setText(str(err))
            self.update_export_button()
            return
        if self._running:
            return
        snapshots = {}
        for name, combo in self.ui.combos.items():
            obj = self.app.collection.get_by_name(combo.currentText()) if combo.currentIndex() >= 0 else None
            if obj is None:
                continue
            key = (id(obj), id(obj.solid_geometry), id(getattr(obj, 'follow_geometry', None)), obj.units)
            if key not in self._sources:
                self._sources[key] = Source(obj.solid_geometry, getattr(obj, 'follow_geometry', ()), obj.units)
            if name.startswith('drills') and 'pth' in obj.obj_options['name'].lower():
                name = 'drills_pth_' + name
            snapshots[name] = self._sources[key]
        active_sources = set(snapshots.values())
        self._sources = {key: value for key, value in self._sources.items() if value in active_sources}
        self._pending = False
        self._running = True
        self._completed_units = self.app.app_units
        self._ready = False
        self.update_export_button()
        names = set(snapshots) | {'board'}
        if any(name.startswith('drills_pth_') for name in snapshots):
            names.add('plating')
        self.view.begin_build(names)
        self.ui.status.setText(_("Building PCB preview…"))
        self.app.worker_task.emit({'fcn': self.build, 'params': [self._token, snapshots, self.app.app_units]})

    def build(self, token, snapshots, units):
        error = ''
        try:
            for layer in self._engine.build(snapshots, units, cancelled=lambda: token != self._token):
                if token != self._token:
                    break
                self.layer_ready.emit(token, layer)
        except Exception as err:
            error = str(err)
        finally:
            self.build_done.emit(token, error)

    def on_layer_ready(self, token, layer):
        if token != self._token or not self._active or self.view is None or not self.view.alive:
            return
        self.view.set_layer(layer)
        try:
            self.view.update_style(self.ui.values(), self.app.app_units)
        except ValueError as err:
            self.ui.status.setText(str(err))
            return
        if layer.name == 'board' and self._fit:
            self.view.canvas.fit_view()
            self._fit = False

    def on_build_done(self, token, error):
        self._running = False
        if not self._active:
            self._engine = PCBGeometry()
            return
        if token == self._token:
            self._ready = False
            self._completed_layers = ()
            if error:
                self.ui.status.setText(error)
                self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            else:
                if self.view is not None and self.view.alive:
                    self._completed_layers = tuple(layer for layer, _meshes in self.view.layers.values())
                    try:
                        self.view.update_style(self.ui.values(), self._completed_units)
                        self._ready = any(layer.name == 'board' and len(layer.faces)
                                          for layer in self._completed_layers)
                        self.ui.status.setText(_("PCB preview ready"))
                        if not self.view.canvas.view.camera._view_modified:
                            self.view.canvas.fit_view()
                    except ValueError as err:
                        self.ui.status.setText(str(err))
            self.update_export_button()
        if self._pending:
            self._timer.start()

    def update_export_button(self):
        if self.ui is not None:
            self.ui.export_pdf_button.setEnabled(self._active and self._ready and not self._pending
                                                 and not self._running and not self._exporting)

    def on_export_pdf(self):
        if (not self._active or not self._ready or self._pending or self._running or self._exporting
                or self.view is None or not self.view.alive):
            return
        request = self._export_job
        filename, _filter = FCFileSaveDialog.get_saved_filename(
            caption=_("Export 3D PDF"), directory=self.app.get_last_save_folder() + '/PCB.pdf',
            ext_filter='PDF Files (*.pdf);;All Files (*.*)')
        if not filename:
            return
        # A modal dialog can process selection changes or cleanup before returning.
        if (request != self._export_job or not self._active or not self._ready
                or self._pending or self._running or self._exporting):
            return
        values = self.ui.values()
        layers = self._completed_layers
        units = self._completed_units
        self._export_job += 1
        job = self._export_job
        cancelled = self._export_cancel = Event()
        self._exporting = True
        self.update_export_button()
        self.ui.status.setText(_("Exporting 3D PDF…"))
        try:
            frame = PDF3DWriter.export_frame(export_bounds(layers, values, units))
        except ValueError as err:
            self._ready = False
            self.on_export_done(job, '', str(err))
            return
        try:
            poster = render_poster(layers, values, units, frame)
        except Exception as err:
            self.on_export_done(job, '', str(err))
            return
        if cancelled.is_set():
            return
        self.app.worker_task.emit({'fcn': self.export_pdf,
                                   'params': [job, cancelled, str(filename), layers, values, units, frame, poster]})

    def export_pdf(self, job, cancelled, filename, layers, values, units, frame, poster):
        temporary = None
        error = ''
        saved = False
        try:
            if cancelled.is_set():
                return
            u3d = write_scene(export_meshes(layers, values, units))
            if cancelled.is_set():
                return
            with tempfile.NamedTemporaryFile(delete=False, dir=os.path.dirname(os.path.abspath(filename)),
                                             suffix='.pdf') as output:
                temporary = output.name
            PDF3DWriter.write(temporary, u3d, frame['bounds'], page_size=frame['page_size'], poster=poster)
            # Cleanup and commit share this lock so a closed session cannot replace the destination.
            with self._export_lock:
                if cancelled.is_set():
                    return
                os.replace(temporary, filename)
                saved = True
        except Exception as err:
            error = str(err)
        finally:
            try:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError as err:
                error = str(err) if not error else error + '\n' + str(err)
            self.export_done.emit(job, filename if saved else '', error)

    def on_export_done(self, job, filename, error):
        if job != self._export_job or not self._active:
            return
        self._exporting = False
        self.update_export_button()
        if error:
            self.ui.status.setText(error)
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
        elif filename:
            self.ui.status.setText(_("3D PDF exported"))
            if getattr(self.app, 'file_saved', None) is not None:
                self.app.file_saved.emit('PDF', filename)
            self.app.inform.emit('[success] %s: %s' % (_("3D PDF exported"), filename))

    def on_canvas_destroyed(self, canvas):
        if self.view is None or self.view.canvas is not canvas:
            return
        self.on_plot_tab_closed('3D_area_tab')

    def on_plot_tab_closed(self, name, index=None):
        if name != '3D_area_tab' or not self._active or self.view is None:
            return
        # The plot's close handler already owns tab removal; don't close it recursively.
        self._active = False
        self.on_plugin_cleanup()
        if self.app.ui.plugin_scroll_area.widget() is self:
            self.app.on_notebook_closed()

    def on_plugin_cleanup(self):
        active = self._active
        canvas = self.view.canvas if self.view is not None else None
        self._active = False
        self._token += 1
        self._pending = False
        with self._export_lock:
            self._export_cancel.set()
        self._export_job += 1
        self._exporting = False
        self._ready = False
        self._completed_layers = ()
        self.update_export_button()
        self._timer.stop()
        if self.view is not None:
            self.view.clear()
            self.view = None
        self._sources.clear()
        if not self._running:
            self._engine = PCBGeometry()
        if active and canvas is not None and self.app.plotcanvas3d is canvas:
            index = self.app.ui.plot_tab_area.indexOf(self.app.area_3d_tab)
            if index >= 0:
                self.app.ui.plot_tab_area.closeTab(index)
