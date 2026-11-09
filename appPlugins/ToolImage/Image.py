# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore, QtWidgets
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolImage.ImageImportMixin import ImageImportMixin

from copy import deepcopy
import os

from shapely import LineString, MultiLineString, Polygon, MultiPolygon

import gettext
import appTranslation as fcTranslate
import builtins

from defaults import AppDefaults

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolImage(ImageImportMixin, AppTool):

    image_processing_finished = QtCore.pyqtSignal(int, str)

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app
        self.decimals = self.app.decimals
        self.filename = None
        self.image_processing_finished.connect(self.finish_image_processing)
        self.app.cleanup.connect(self.cancel_image_processing)
        self.app.cleanup.connect(self.stop_image_preview)

        self.build_image_import_ui()
        self.add_object_creation_controls(has_source=False)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolImage()")

        if toggle:
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

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()
        self.app.ui.notebook.setTabText(2, _("Image Import"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, **kwargs)

    def set_tool_ui(self):
        self.cancel_image_processing()
        self.filename = None
        self.clear_ui(self.layout)
        self.build_image_import_ui()
        self.add_object_creation_controls(has_source=False)

    def on_image_import_action(self):
        self.on_file_importimage()

    def on_file_importimage(self, threaded=True):
        """Create an object from the loaded image using the current parameters."""
        self.app.log.debug("on_file_importimage()")
        params = self.image_import_params()
        filename = params['filename']
        if not filename:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Load an image first."))
            return

        if getattr(self, '_image_progress', None) is not None:
            return
        import_mode = params['import_mode']
        type_obj = self.ui.tf_type_obj_combo.get_value()
        dpi = params['dpi']
        mode = params['mode']
        min_area = params['min_area']
        mask = params['mask']

        token = self.start_image_processing()
        units = self.app.app_units
        adjustments = self.image_adjustments(params)

        def job():
            try:
                if token != self._image_process_token:
                    return
                svg_text = None
                if import_mode == 'trace':
                    svg_text = self.trace_image(filename, mode, params['threshold'], params['trace_options'],
                                                **adjustments)
                geometry = self.image_geometry(filename, import_mode, dpi, mode, mask, svg_text, min_area,
                                               units, 'gerber' if type_obj == _("Gerber") else 'geometry',
                                               **adjustments)
                if token != self._image_process_token:
                    return
                if geometry == 'fail' or not geometry:
                    raise ValueError(_("No geometry found in the image."))
                self.import_image(filename, import_mode, type_obj, dpi, mode, mask, svg_text, min_area,
                                  prepared_geometry=geometry)
            except Exception as err:
                self.app.log.error("ToolImage.on_file_importimage() -> %s" % str(err))
                self.image_processing_finished.emit(token, str(err))
                return
            self.image_processing_finished.emit(token, '')

        if threaded is True:
            self.app.worker_task.emit({'fcn': job, 'params': []})
        else:
            job()

    def import_image(self, filename, import_mode='raster', o_type=_("Geometry"), dpi=96, mode='black',
                     mask=None, svg_text=None, min_area=0.0, outname=None, silent=False, prepared_geometry=None):
        """Import image geometry, register the object, and record the recent file."""
        self.app.defaults.report_usage("import_image()")
        if not os.path.exists(filename):
            if silent:
                self.app.log.debug("File no longer available.")
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return

        if mask is None:
            mask = [250, 250, 250, 250]
        if o_type is None or o_type == _("Geometry"):
            obj_type = "geometry"
        elif o_type == _("Gerber"):
            obj_type = "gerber"
        else:
            if silent is False:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Only Geometry and Gerber objects are supported"))
            return

        def obj_init(geo_obj, app_obj):
            app_obj.log.debug("ToolImage.import_image() -> importing image as: %s" % obj_type.capitalize())
            image_geo = prepared_geometry if prepared_geometry is not None else self.image_geometry(
                filename, import_mode, dpi, mode, mask, svg_text, min_area, units, obj_type)

            if image_geo == 'fail':
                if silent is False:
                    app_obj.inform.emit("[ERROR_NOTCL] %s" % _("Failed."))
                return "fail"
            if not image_geo:
                app_obj.log.debug("ToolImage.import_image() -> empty geometry.")
                return 'fail'

            geo_obj.multigeo = False
            geo_obj.multitool = False
            geo_obj.solid_geometry = image_geo

            if obj_type == 'geometry':
                app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
                tooldia = float(self.app.options.get(
                    "tools_mill_tooldia", app_defaults.get("tools_mill_tooldia", AppDefaults.factory_defaults.get("tools_mill_tooldia"))))
                tooldia = float('%.*f' % (self.decimals, tooldia))
                new_data = {k: v for k, v in self.app.options.items()}
                geo_obj.tools.update({
                    1: {'tooldia': tooldia, 'data': deepcopy(new_data),
                        'solid_geometry': deepcopy(geo_obj.solid_geometry)}
                })
                geo_obj.tools[1]['data']['name'] = name
                if svg_text is not None:
                    geo_obj.source_file = svg_text
                else:
                    geo_obj.regenerate_source()
            else:
                if 0 not in geo_obj.tools:
                    geo_obj.tools[0] = {'type': 'REG', 'size': 0.0, 'geometry': []}
                try:
                    w_geo = geo_obj.solid_geometry.geoms if \
                        isinstance(geo_obj.solid_geometry, (MultiLineString, MultiPolygon)) else geo_obj.solid_geometry
                    for pol in w_geo:
                        new_el = {'solid': pol, 'follow': LineString(pol.exterior.coords)}
                        geo_obj.tools[0]['geometry'].append(new_el)
                except TypeError:
                    new_el = {
                        'solid': geo_obj.solid_geometry,
                        'follow': LineString(geo_obj.solid_geometry.exterior.coords) if
                        isinstance(geo_obj.solid_geometry, Polygon) else geo_obj.solid_geometry
                    }
                    geo_obj.tools[0]['geometry'].append(new_el)
                geo_obj.regenerate_source()

        with self.app.proc_container.new('%s ...' % _("Importing")):
            name = outname or filename.split('/')[-1].split('\\')[-1]
            units = self.app.app_units
            res = self.new_object(obj_type, name, obj_init)
            if res == 'fail':
                self.app.inform.emit("[ERROR_NOTCL] %s" % _("Failed."))
                return
            self.app.file_opened.emit("image", filename)
            if silent is False:
                self.app.inform.emit('[success] %s: %s' % (_("Opened"), filename))
