from PyQt6 import QtCore, QtWidgets
from appTool import AppToolEditor
from appPlugins.ToolImage.ImageImportMixin import ImageImportMixin
from appGUI.GUIElements import VerticalScrollArea
from shapely.geometry import box
import numpy as np
import shapely

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def image_paths(geometries):
    """The editable paths of the converted image: polygon rings, paths and supported multipart geometry."""
    polygons = np.asarray(geometries, dtype=object)
    if np.all(shapely.get_type_id(polygons) == 3):
        polygons = polygons[shapely.is_valid(polygons) & ~shapely.is_empty(polygons)]
        return list(shapely.get_rings(polygons))
    paths = []

    def collect(geometry):
        if geometry is None or geometry.is_empty or not geometry.is_valid:
            return
        if geometry.geom_type == 'Polygon':
            collect(geometry.exterior)
            for ring in geometry.interiors:
                collect(ring)
        elif geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
            for part in geometry.geoms:
                collect(part)
        elif geometry.geom_type != 'Point':
            paths.append(geometry)

    for geometry in geometries:
        collect(geometry)
    return paths


class GeoImportImageTool(ImageImportMixin, AppToolEditor):
    """Load an image with the Image Import settings and prepare its paths for the canvas."""

    image_finished = QtCore.pyqtSignal(int, object, str)

    def __init__(self, app, draw_app):
        AppToolEditor.__init__(self, app)

        self.app = app
        self.draw_app = draw_app
        self.filename = None
        self.paths = []
        self.preview_paths = []
        self.anchor = None
        self.token = 0
        self._busy = False
        self._cleanup_connected = False

        self.build_image_import_ui()
        # the editor inserts its own paths: it never creates a Gerber object
        self.ui.tf_type_obj_combo_label.hide()
        self.ui.tf_type_obj_combo.hide()
        self.ui.import_button.setText(_("Place Image"))
        self.ui.import_button.setToolTip(_("Convert the loaded image and place its paths on the canvas."))
        self.pluginName = _("Import Image")

        self.image_finished.connect(self.on_image_finished)
        cleanup = getattr(self.app, 'cleanup', None)
        if cleanup is not None:
            cleanup.connect(self.cancel)
            self._cleanup_connected = True

    def run(self):
        self.app.defaults.report_usage("Geo Editor ImportImageTool()")
        super().run()

        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        found_idx = None
        for idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                found_idx = idx
                break
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
            self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

        self.app.ui.notebook.setTabText(2, self.pluginName)

    def on_image_import_action(self):
        params = self.image_import_params()
        if not params['filename']:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Load an image first."))
            return
        if self._busy:
            return

        self._invalidate_prepared()
        token = self.token
        self.start_image_processing()
        self._busy = True
        self.ui.import_button.setEnabled(False)
        self.app.inform.emit(_("Converting the image..."))
        signal = self.image_finished
        units = self.app.app_units
        adjustments = self.image_adjustments(params)

        def job():
            try:
                if token != self.token:
                    return
                svg_text = None
                if params['import_mode'] == 'trace':
                    svg_text = self.trace_image(params['filename'], params['mode'], params['threshold'],
                                                params['trace_options'], **adjustments)
                geos = self.image_geometry(params['filename'], params['import_mode'], params['dpi'],
                                           params['mode'], params['mask'], svg_text, params['min_area'],
                                           units, keep_paths=True, **adjustments)
                if geos == 'fail':
                    signal.emit(token, None, _("Failed."))
                    return
                if token != self.token:
                    return
                paths = image_paths(geos or [])
                if paths:
                    bounds = tuple(shapely.total_bounds(paths))
                    preview = paths if len(paths) <= 1000 else [box(*bounds).exterior]
                    signal.emit(token, (paths, bounds[:2], preview), '')
                else:
                    signal.emit(token, ([], None, []), '')
            except ImportError as err:
                signal.emit(token, None, str(err))
            except Exception as err:
                self.app.log.error("GeoImportImageTool.on_image_import_action() -> %s" % str(err))
                signal.emit(token, None, str(err))

        self.app.worker_task.emit({'fcn': job, 'params': []})

    def on_image_loaded(self, filename):
        """A replacement image invalidates the conversion made for the previous one."""
        self._invalidate_prepared()

    def on_image_finished(self, token, geometries, error):
        if token != self.token:
            return
        active_tool = getattr(self.draw_app, 'active_tool', None)
        if getattr(active_tool, 'image_tool', None) is not self:
            self._invalidate_prepared()
            return

        ImageImportMixin.cancel_image_processing(self)

        if error:
            self._invalidate_prepared()
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            return
        paths, anchor, preview = geometries
        if not paths:
            self._invalidate_prepared()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No geometry found in the image."))
            return

        self._busy = False
        self.ui.import_button.setEnabled(True)
        self.paths = paths
        self.preview_paths = preview
        self.anchor = anchor
        self.app.inform.emit(_("Click on the canvas to place the image."))
        if preview is not paths:
            self.app.inform.emit(_("Large image: the moving preview shows its extent; placement keeps all paths."))

        data = (getattr(self.draw_app, 'snap_x', None), getattr(self.draw_app, 'snap_y', None))
        if data[0] is None or data[1] is None:
            data = (0.0, 0.0)
        self.draw_app.update_utility_geometry(data=data)

    def _invalidate_prepared(self):
        ImageImportMixin.cancel_image_processing(self)
        self.token += 1
        self._busy = False
        self.paths = []
        self.preview_paths = []
        self.anchor = None
        try:
            self.ui.import_button.setEnabled(True)
        except (AttributeError, RuntimeError):
            pass
        self._clear_preview()

    def _clear_preview(self):
        delete_utility = getattr(self.draw_app, 'delete_utility_geometry', None)
        if delete_utility is not None:
            try:
                delete_utility()
            except (AttributeError, RuntimeError):
                pass
            return

        tool_shape = getattr(self.draw_app, 'tool_shape', None)
        if tool_shape is not None:
            try:
                tool_shape.clear(update=True)
                tool_shape.redraw()
            except (AttributeError, RuntimeError):
                pass

    def cancel(self):
        if getattr(self, '_preview_timer', None) is not None:
            self.stop_image_preview()
        self._invalidate_prepared()
        if getattr(self, '_cleanup_connected', False):
            try:
                self.app.cleanup.disconnect(self.cancel)
            except (TypeError, AttributeError):
                pass
            self._cleanup_connected = False

    def cancel_image_processing(self):
        ImageImportMixin.cancel_image_processing(self)
        self._invalidate_prepared()
