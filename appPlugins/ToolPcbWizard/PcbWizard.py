# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 4/15/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolPcbWizard.PcbWizardUI import WizardUI
from appPlugins.ToolPcbWizard.PcbWizardGen import parse_inf, merge_excellon
import logging
from io import StringIO
import os

from datetime import datetime as dt

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class PcbWizard(AppTool):

    file_loaded = QtCore.pyqtSignal(str, str)
    # (integral digits, fractional digits, units) of the INF file: the form is set in the GUI thread
    inf_format_loaded = QtCore.pyqtSignal(int, int, str)

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.decimals = self.app.decimals

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = WizardUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls(has_source=False)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        # connected once: the slot sets the form of the current self.ui
        self.inf_format_loaded.connect(self.on_inf_format_loaded)

        self.excellon_loaded = False
        self.inf_loaded = False
        self.process_finished = False

        self.units = 'INCH'
        self.zeros = 'LZ'
        self.integral = 2
        self.fractional = 4

        self.outname = 'file'

        self.exc_file_content = None
        self.tools_from_inf = {}

    def run(self, toggle=False):
        self.app.defaults.report_usage("PcbWizard Tool()")

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

        self.app.ui.notebook.setTabText(2, _("PcbWizard Import"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, **kwargs)

    def connect_signals_at_init(self):
        # ## Signals
        self.ui.excellon_brn.clicked.connect(self.on_load_excellon_click)
        self.ui.inf_btn.clicked.connect(self.on_load_inf_click)
        self.ui.import_button.clicked.connect(lambda: self.on_import_excellon())

        self.file_loaded.connect(self.on_file_loaded)
        self.ui.units_radio.activated_custom.connect(self.ui.on_units_change)

    def set_tool_ui(self):
        self.units = 'INCH'
        self.zeros = 'LZ'
        self.integral = 2
        self.fractional = 4

        self.clear_ui(self.layout)
        self.ui = WizardUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls(has_source=False)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.outname = 'file'

        self.exc_file_content = None
        self.tools_from_inf = {}

        # ## Initialize form
        self.ui.int_entry.set_value(self.integral)
        self.ui.frac_entry.set_value(self.fractional)
        self.ui.zeros_radio.set_value(self.zeros)
        self.ui.units_radio.set_value(self.units)

        self.excellon_loaded = False
        self.inf_loaded = False
        self.process_finished = False

        self.build_ui()

    def build_ui(self):
        self.ui.build_tools_table(self.tools_from_inf)

    def update_params(self):
        self.units = self.ui.units_radio.get_value()
        self.zeros = self.ui.zeros_radio.get_value()
        self.integral = self.ui.int_entry.get_value()
        self.fractional = self.ui.frac_entry.get_value()

    def on_load_excellon_click(self):
        """

        :return: None
        """
        self.app.log.debug("on_load_excellon_click()")

        _filter = "Excellon Files(*.DRL *.DRD *.TXT);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load PcbWizard Excellon file"),
                                                                 directory=self.app.get_last_folder(),
                                                                 filter=_filter)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load PcbWizard Excellon file"),
                                                                 filter=_filter)

        filename = str(filename)

        if filename == "":
            self.app.inform.emit(_("Cancelled."))
        else:
            self.app.worker_task.emit({'fcn': self.load_excellon, 'params': [filename]})

    def on_load_inf_click(self):
        """

                :return: None
                """
        self.app.log.debug("on_load_inf_click()")

        _filter = "INF Files(*.INF);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load PcbWizard INF file"),
                                                                 directory=self.app.get_last_folder(),
                                                                 filter=_filter)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load PcbWizard INF file"),
                                                                 filter=_filter)

        filename = str(filename)

        if filename == "":
            self.app.inform.emit(_("Cancelled."))
        else:
            self.app.worker_task.emit({'fcn': self.load_inf, 'params': [filename]})

    def load_inf(self, filename):
        self.app.log.debug("ToolPcbWizard.load_inf()")

        with open(filename, 'r') as inf_f:
            inf_file_content = inf_f.readlines()

        tools, formats = parse_inf(inf_file_content)
        self.tools_from_inf.update(tools)
        for self.integral, self.fractional, self.units in formats:
            # this runs in a worker thread: the widgets are changed only in the GUI thread
            self.inf_format_loaded.emit(self.integral, self.fractional, self.units)

        if not self.tools_from_inf:
            self.app.inform.emit('[ERROR] %s' %
                                 _("The INF file does not contain the tool table.\n"
                                   "Try to open the Excellon file from File -> Open -> Excellon\n"
                                   "and edit the drill diameters manually."))
            return "fail"

        self.file_loaded.emit('inf', filename)

    def load_excellon(self, filename):
        with open(filename, 'r') as exc_f:
            self.exc_file_content = exc_f.readlines()

        self.file_loaded.emit("excellon", filename)

    def on_inf_format_loaded(self, integral, fractional, units):
        self.ui.units_radio.set_value(units)
        self.ui.int_entry.set_value(integral)
        self.ui.frac_entry.set_value(fractional)

    def on_file_loaded(self, signal, filename):
        self.build_ui()

        if signal == 'inf':
            self.inf_loaded = True
            self.ui.tools_table.setVisible(True)
            self.app.inform.emit('[success] %s' % _("PcbWizard .INF file loaded."))
        elif signal == 'excellon':
            self.excellon_loaded = True
            self.outname = os.path.split(str(filename))[1]
            self.app.inform.emit('[success] %s' % _("Main PcbWizard Excellon file loaded."))

        if self.excellon_loaded and self.inf_loaded:
            self.process_finished = True

        # Register recent file
        self.app.options["global_last_folder"] = os.path.split(str(filename))[0]

    def build_modified_excellon(self):
        """
        The Excellon file with the format, units, zeros and the INF tool table added after M48. Built at each Import
        from the form values of that moment: the user may correct them after the files are loaded.

        :return:    a new StringIO (one that was parsed already is at its end)
        """
        self.update_params()
        time_str = "{:%A, %d %B %Y at %H:%M}".format(dt.now())
        excellon_string = merge_excellon(
            self.exc_file_content, self.tools_from_inf, self.integral, self.fractional,
            self.units, self.zeros, self.app.version, self.app.version_date, time_str)
        return StringIO(excellon_string)

    def on_import_excellon(self, excellon_fileobj=None):
        self.app.log.debug("import_2files_excellon()")

        if excellon_fileobj is None and self.process_finished:
            excellon_fileobj = self.build_modified_excellon()

        # How the object should be initialized
        def obj_init(excellon_obj, app_obj):
            # populate excellon_obj.tools dict
            try:
                ret = excellon_obj.parse_file(file_obj=excellon_fileobj)
                if ret == "fail":
                    app_obj.log.debug("Excellon parsing failed.")
                    app_obj.inform.emit('[ERROR_NOTCL] %s' % _("This is not Excellon file."))
                    return "fail"
            except IOError:
                app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Cannot parse file"), self.outname))
                app_obj.log.debug("Could not import Excellon object.")
                return "fail"
            except Exception as e:
                app_obj.log.error("PcbWizard.on_import_excellon().obj_init() %s" % str(e))
                msg = '[ERROR_NOTCL] %s' % _("An internal error has occurred. See shell.\n")
                msg += app_obj.traceback.format_exc()
                app_obj.inform.emit(msg)
                return "fail"

            # populate excellon_obj.solid_geometry list
            ret = excellon_obj.create_geometry()
            if ret == 'fail':
                app_obj.log.error("Could not create geometry for Excellon object.")
                return "fail"

            for tool in excellon_obj.tools:
                if excellon_obj.tools[tool]['solid_geometry']:
                    return
            app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("No geometry found in file"), name))
            return "fail"

        if excellon_fileobj is not None and excellon_fileobj != '':
            if self.process_finished:
                with self.app.proc_container.new('%s ...' % _("Importing")):

                    # Object name
                    name = self.outname

                    ret_val = self.new_object("excellon", name, obj_init, autoselected=False)
                    if ret_val == 'fail':
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _('Import Excellon file failed.'))
                        return

                        # Register recent file
                    self.app.file_opened.emit("excellon", name)

                    # GUI feedback
                    self.app.inform.emit('[success] %s: %s' % (_("Imported"), name))
            else:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _('Excellon merging is in progress. Please wait...'))
        else:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('The imported Excellon file is empty.'))
