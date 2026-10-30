# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolReport.ReportUI import ReportUI
from appPlugins.ToolReport.ReportGen import calculate_dimensions, gerber_attribute_rows

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ObjectReport(AppTool):
    pluginName = _("Object Report")

    calculations_finished = QtCore.pyqtSignal(float, float, float, float, float, object)
    attributes_found = QtCore.pyqtSignal(object)

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.decimals = self.app.decimals
        self.ui = ReportUI(layout=self.layout, app=self.app)
        # Preserve the public widget attributes used by callers of the original tool.
        self.info_frame = self.ui.info_frame
        self.info_box = self.ui.info_box
        self.treeWidget = self.ui.treeWidget
        self.calculations_finished.connect(self.show_area_chull)
        self.attributes_found.connect(self.show_attributes)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolReport()")
        if self.app.plugin_tab_locked is True:
            return
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
        self.properties()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='P', **kwargs)

    def set_tool_ui(self):
        self.ui.set_tool_ui()

    def properties(self):
        obj_list = self.app.collection.get_selected()
        if not obj_list:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            self.app.ui.notebook.removeTab(2)
            self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)
            return
        try:
            self.app.delete_selection_shape()
        except Exception as e:
            self.app.log.error("ToolReport.Properties.properties() --> %s" % str(e))
        for obj in obj_list:
            self.addItems(obj)
            self.app.inform.emit('[success] %s' % _("Object Properties are displayed."))
        self.treeWidget.resize_sig.emit()
        self.app.ui.notebook.setTabText(2, _("Object Report"))

    def addItems(self, obj):
        self.ui.decimals = self.decimals
        self.ui.addItems(obj, self._start_calculations)

    def _start_calculations(self, obj, dims):
        def job_thread(obj_prop):
            self.app.proc_container.new('%s...' % _("Working"))
            geo = obj_prop.solid_geometry
            bounds = None
            if geo:
                try:
                    bounds = obj_prop.bounds()
                except Exception as ee:
                    self.app.log.error("PropertiesTool.addItems() -> calculate dimensions --> %s" % str(ee))
            values, errors = calculate_dimensions(geo, obj_prop.tools, obj_prop.kind, self.app.app_units, bounds)
            for error in errors:
                self.app.log.error(error)
            self.calculations_finished.emit(*values, dims)
            if str(obj_prop.kind).lower() == 'gerber':
                rows = gerber_attribute_rows(obj_prop)
                if rows:
                    self.attributes_found.emit(rows)

        self.app.worker_task.emit({'fcn': job_thread, 'params': [obj]})

    def show_area_chull(self, area, length, width, chull_area, copper_area, location):
        self.ui.decimals = self.decimals
        self.ui.show_area_chull(area, length, width, chull_area, copper_area, location)

    def show_attributes(self, rows):
        self.ui.show_attributes(rows)
