"""Geometry preview session, owned by one selected project object."""

from threading import Event
from weakref import ref
import gettext

from PyQt6 import QtCore

from appTool import AppTool
from .GeometryViewerGen import board_mesh, footprints, has_geometry, shapes, snapshot, with_remaining_copper
from .GeometryViewerUI import GeometryViewerUI
from .GeometryViewerView import GeometryView

_ = gettext.gettext


def default_display_mode(source):
    operation = source.obj_options.get('source_operation')
    if operation in ('isolation', 'ncc'):
        return 'material'
    if not operation:
        # Isolation explicitly marks its generated centerlines for the Milling tool.
        path_tools = [tool for tool in source.tools.values() if next(shapes(tool.get('solid_geometry')), None)
                      is not None]
        if path_tools and all(tool.get('data', {}).get('tools_iso_centerline') is True for tool in path_tools):
            return 'material'
    return 'coverage'


class ToolGeometryViewer(AppTool):
    pluginName = _('Geometry 3D Viewer')
    ready = QtCore.pyqtSignal(object, object, object, object, str)

    def __init__(self, app):
        super().__init__(app)
        self.view = self.ui = self._owner = self._page = None
        self._board = self._cuts = self._display_key = self._failed_key = self._footprint_cache = None
        self._job = None
        self._signals = []
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.refresh)
        self.ready.connect(self.accept, QtCore.Qt.ConnectionType.QueuedConnection)

    def run(self, source=None):
        if (self.app.plugin_tab_locked or source is None or not has_geometry(source) or
                not any(obj is source for obj in self.app.collection.get_selected())):
            return
        self.close_preview()
        ui = self.app.ui
        super().run()
        if ui.plugin_scroll_area.widget() is not self:
            return
        if ui.notebook.indexOf(ui.plugin_tab) < 0:
            ui.notebook.addTab(ui.plugin_tab, self.pluginName)
        ui.notebook.setCurrentWidget(ui.plugin_tab)
        ui.notebook.setTabText(ui.notebook.indexOf(ui.plugin_tab), self.pluginName)
        if ui.splitter.sizes() and not ui.splitter.sizes()[0]:
            ui.splitter.setSizes([1, 1])
        self._created_canvas = self.app.plotcanvas3d is None
        self.app.on_3d_area()
        canvas = self.app.plotcanvas3d
        if canvas is None:
            self.close_preview()
            return
        self._owner = ref(source)
        self._page = self.app.area_3d_tab
        self.view = GeometryView(canvas)
        self._page.geometry_preview_release = self.release_canvas
        self.ui = GeometryViewerUI(self.layout, self.app, source.obj_options['name'],
                                   source.get_source_name() if hasattr(source, 'get_source_name') else '')
        self.ui.mode.set_value(default_display_mode(source))
        for signal, slot in (
                (self.app.collection.view.selectionModel().selectionChanged, self.selection_changed),
                (self.app.object_status_changed, self.object_status),
                (self.app.app_obj.object_changed, self.object_changed),
                (self.app.cleanup, self.close_preview),
                (ui.plot_tab_area.tab_closed_signal, self.plot_closed),
                (canvas.native.destroyed, self.canvas_destroyed)):
            signal.connect(slot)
            self._signals.append((signal, slot))
        for signal in (self.ui.thickness.valueChanged, self.ui.copper_thickness.valueChanged,
                       self.ui.color.value_changed, self.ui.opacity.valueChanged,
                       self.ui.centerlines.toggled, self.ui.camera_reference.activated_custom,
                       self.ui.mode.activated_custom, self.ui.copper_color.value_changed):
            signal.connect(self.render)
        self.ui.margin.valueChanged.connect(self.refresh)
        self._units = self.app.app_units
        self._timer.start()
        self.refresh()

    def selection_changed(self, *args):
        owner = self._owner() if self._owner is not None else None
        if owner is None or not any(obj is owner for obj in self.app.collection.get_selected()):
            self.close_preview()

    def object_status(self, obj, status, _name):
        if status == 'delete_all' or (status == 'delete' and self._owner is not None and obj is self._owner()):
            self.close_preview()

    def object_changed(self, obj):
        if self._owner is not None and obj is self._owner():
            self.refresh()

    def status_text(self):
        board = self._board
        if self.ui.mode.get_value() == 'material' and board is not None and not board.geometry.area:
            return _('Material preview needs a positive board margin or a nonzero board area '
                     'to show remaining copper.')
        return _('Geometry preview ready')

    def refresh(self, *args):
        owner = self._owner() if self._owner is not None else None
        if owner is None:
            self.close_preview()
            return
        units = self.app.app_units
        if units != self._units:
            factor = 25.4 if units.upper() == 'MM' else 1 / 25.4
            self._units = units
            self.ui.set_units(units)
            for spinner in (self.ui.margin, self.ui.thickness):
                with QtCore.QSignalBlocker(spinner):
                    spinner.set_value(spinner.get_value() * factor)
        try:
            bounds, paths = snapshot(owner, units)
        except (KeyError, TypeError, ValueError) as err:
            if self._job is not None:
                self._job[0].set()
                self._job = None
            self.view.root.visible = False
            self.ui.status.setText(str(err))
            return
        board_key = bounds, self.ui.margin.get_value()
        if self._job is not None:
            cancel, pending_paths = self._job
            if paths != pending_paths:
                cancel.set()
            if self._display_key is not None and paths != self._display_key[1]:
                self.view.root.visible = False
            return
        if (board_key, paths) == self._failed_key:
            return
        if (board_key, paths) == self._display_key:
            if not self.view.root.visible:
                self.view.root.visible = True
                self.render()
            self.ui.status.setText(self.status_text())
            return
        if self._display_key is None or paths != self._display_key[1]:
            self.view.root.visible = False
        cancelled = Event()
        self._job = cancelled, paths
        board = self._board if self._display_key is not None and board_key == self._display_key[0] else None
        cuts = self._footprint_cache[1] if self._footprint_cache and paths == self._footprint_cache[0] else None
        self.ui.status.setText(_('Building Geometry preview…'))
        self.app.worker_task.emit({'fcn': self.build, 'params': [cancelled, board_key, paths, board, cuts]})

    def build(self, cancelled, board_key, paths, board, cuts):
        error = ''
        try:
            if cuts is None and not cancelled.is_set():
                cuts = footprints(paths, cancelled.is_set)
            if board is None and not cancelled.is_set():
                board = board_mesh(*board_key)
            if cuts and not cancelled.is_set():
                board = with_remaining_copper(board, cuts, cancelled.is_set)
        except Exception as err:
            error = str(err)
        self.ready.emit(cancelled, board, cuts, (board_key, paths), error)

    def accept(self, job, board, cuts, keys, error):
        if self._job is None or job is not self._job[0]:
            return
        self._job = None
        if job.is_set():
            self.refresh()
            return
        # Recheck current inputs before displaying a result queued before a source edit.
        owner = self._owner() if self._owner is not None else None
        if owner is None:
            self.close_preview()
            return
        try:
            bounds, paths = snapshot(owner, self.app.app_units)
        except (KeyError, TypeError, ValueError):
            self.refresh()
            return
        if keys[1] != paths or self._units != self.app.app_units:
            self.refresh()
            return
        if cuts is not None:
            self._footprint_cache = paths, cuts
        if keys[0] != (bounds, self.ui.margin.get_value()):
            self.refresh()
            return
        if error or not cuts:
            self.view.root.visible = False
            self.ui.status.setText(error or _('No cutting footprints.'))
            self._display_key = None
            self._failed_key = keys
            return
        fit = self._display_key is None or keys[0] != self._display_key[0]
        self._board, self._cuts = board, cuts
        self._display_key = keys
        self._failed_key = None
        self.view.root.visible = True
        self.ui.status.setText(self.status_text())
        self.render(fit=fit)

    def render(self, *args, fit=False):
        if self.view is not None and self.view.alive and self.view.root.visible and self._display_key is not None:
            try:
                self.view.show_meshes(self._board, self._cuts, self.ui, fit)
            except ValueError as err:
                self.ui.status.setText(str(err))
            else:
                self.ui.status.setText(self.status_text())

    def plot_closed(self, _name, index):
        if self._page is not None and self.app.ui.plot_tab_area.widget(index) is self._page:
            self.close_preview(close_canvas=False)

    def canvas_destroyed(self, *args):
        self.close_preview(close_canvas=False)

    def release_canvas(self):
        self.close_preview(close_canvas=False)

    def on_panel_replaced(self):
        self.close_preview()

    def on_plugin_cleanup(self):
        # The notebook's close handler removes its page after invoking this hook.
        self.close_preview(remove_panel_tab=False)

    def close_preview(self, *, close_canvas=True, remove_panel_tab=True):
        self._timer.stop()
        if self._job is not None:
            self._job[0].set()
            self._job = None
        for signal, slot in self._signals:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._signals.clear()
        view, page = self.view, self._page
        self._owner = self.view = self.ui = self._page = None
        self._board = self._cuts = self._display_key = self._failed_key = self._footprint_cache = None
        if view is not None:
            canvas = view.canvas
            owns_view = getattr(page, 'geometry_preview_release', None) == self.release_canvas
            if owns_view:
                del page.geometry_preview_release
            view.clear()
            if (close_canvas and owns_view and self._created_canvas and self.app.plotcanvas3d is canvas
                    and self.app.area_3d_tab is page):
                tabs = self.app.ui.plot_tab_area
                index = tabs.indexOf(page)
                if index < 0:
                    for window in tuple(tabs.detachedTabs.values()):
                        if window.contentWidget is page:
                            window.close()  # reattach for the normal Plot 3D close handler
                            index = tabs.indexOf(page)
                            break
                if index >= 0:
                    tabs.closeTab(index)
        ui = self.app.ui
        if ui.plugin_scroll_area.widget() is self:
            ui.plugin_scroll_area.takeWidget()
            if remove_panel_tab:
                notebook, panel = ui.notebook, ui.plugin_tab
                index = notebook.indexOf(panel)
                if index < 0:
                    for window in tuple(notebook.detachedTabs.values()):
                        if window.contentWidget is panel:
                            window.close()  # reattach before removing the Tool page
                            index = notebook.indexOf(panel)
                            break
                if index >= 0 and ui.plugin_scroll_area.widget() is None:
                    notebook.removeTab(index)
        self.clear_ui(self.layout)
