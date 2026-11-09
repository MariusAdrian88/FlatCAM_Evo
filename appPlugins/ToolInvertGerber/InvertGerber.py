# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 2/14/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appCommon.Common import GracefulException
from appHandlers.appHistory import history_of
from appObjects.ProjectContext import unique_name
from appPlugins.ToolInvertGerber.InvertGerberGen import (
    InvertError, apertures_from_polygons, invert_geometry, join_style_name, reference_area)
from appPlugins.ToolSub.SubGen import units_factor
import appPlugins.ToolInvertGerber as invert_plugin

import logging
import traceback
from copy import deepcopy

import gettext
import appTranslation as fcTranslate
import builtins

from defaults import AppDefaults

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolInvertGerber(AppTool):
    """
    The widgets, the collection, the Undo history and the messages are used in the GUI thread only. One click reads
    the form, puts ONE job on the app worker (the geometry work) and the job ends with one job_done signal, which
    creates the new objects in the GUI thread as one history group.
    """

    # (kind, payload): 'result' [(source, polygons)] | 'cancelled' | 'error' text
    job_done = QtCore.pyqtSignal(str, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals
        self._busy = False

        AppTool.__init__(self, app)
        self.job_done.connect(self.on_job_done)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = invert_plugin.InvertUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+G', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolInvertGerber()")
        self.app.log.debug("ToolInvertGerber() is running ...")

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
            if found_idx is None:
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
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx is not None:
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

        self.app.ui.notebook.setTabText(2, _("Invert Gerber"))

    def connect_signals_at_init(self):
        self.ui.invert_btn.clicked.connect(self.on_grb_invert)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def _opt(self, key):
        return self.app.options.get(key, AppDefaults.factory_defaults.get(key))

    def set_tool_ui(self):
        self.clear_ui(self.layout)
        self.ui = invert_plugin.InvertUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.ui.margin_entry.set_value(float(self._opt("tools_invert_margin")))
        self.ui.join_radio.set_value(self._opt("tools_invert_join_style"))
        self.ui.source_radio.set_value(self._opt("tools_invert_source"))
        self.ui.reference_radio.set_value(self._opt("tools_invert_reference"))
        self.ui.ref_area_radio.set_value(self._opt("tools_invert_ref_area"))
        self.ui.on_source_changed(self.ui.source_radio.get_value())
        self.ui.on_reference_changed(self.ui.reference_radio.get_value())
        self._set_busy(self._busy)

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.gerber_combo.set_value(obj_name)

    def _set_busy(self, busy):
        self._busy = busy
        try:
            self.ui.invert_btn.setEnabled(not busy)
        except RuntimeError:
            pass

    def _error(self, text):
        self.app.inform.emit('[ERROR_NOTCL] %s' % text)
        return None

    def collect(self):
        """Read the form (GUI thread) and look the objects up. Returns a dict, or None after a message."""
        ui = self.ui
        collection = self.app.collection

        if ui.source_radio.get_value() == 'selection':
            sources = [o for o in collection.get_selected() if o.kind == 'gerber']
            if not sources:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No Gerber object is selected."))
                return None
        else:
            obj_name = ui.gerber_combo.currentText()
            try:
                source = collection.get_by_name(obj_name)
            except Exception as e:
                log.error("ToolInvertGerber.collect() --> %s" % str(e))
                source = None
            if source is None:
                return self._error('%s: %s' % (_("Object not found"), obj_name if obj_name else 'None'))
            sources = [source]

        reference = None
        if ui.reference_radio.get_value() == 'object':
            ref_name = ui.ref_combo.currentText()
            reference = collection.get_by_name(ref_name) if ref_name else None
            if reference is None:
                return self._error('%s: %s' % (_("Reference object not found"), ref_name if ref_name else 'None'))

        return {
            'sources': sources,
            'reference': reference,
            'ref_area': ui.ref_area_radio.get_value(),
            'margin': float(ui.margin_entry.get_value()),
            'join_style': join_style_name(ui.join_radio.get_value()),
            'options': self.app.options,
            'hole_steps': int(self._opt("excellon_circle_steps")),    # the holes of an Excellon reference
            'app_units': str(getattr(self.app, 'app_units', None) or ''),
        }

    def on_grb_invert(self):
        if self._busy:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Another Invert Gerber run is still working."))
            return
        params = self.collect()
        if params is None:
            return
        self._set_busy(True)
        self.app.inform.emit('%s' % _("Working..."))
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [params]})

    # ------------------------------------------------------------------------------------------------------------
    # the job (worker thread): geometry only, no widgets, no collection changes
    # ------------------------------------------------------------------------------------------------------------
    def check_abort(self):
        if getattr(self.app, 'abort_flag', False):
            raise GracefulException()

    def worker_job(self, params):
        kind, payload = 'error', ''
        try:
            with self.app.proc_container.new('%s...' % _("Working")):
                results = []
                for source in params['sources']:
                    self.check_abort()
                    units = (source.obj_options or {}).get('units')
                    margin = params['margin'] * units_factor(units, params['app_units'])
                    try:
                        area = None
                        if params['reference'] is not None:
                            area = reference_area(params['reference'], params['ref_area'], params['hole_steps'], units)
                        polys = invert_geometry(source.solid_geometry, source.bounds(), margin, params['options'],
                                                params['join_style'], area=area, abort=self.check_abort, units=units)
                    except InvertError:
                        polys = None
                    results.append((source, polys))
                kind, payload = 'result', results
        except GracefulException:
            kind, payload = 'cancelled', None
        except InvertError:
            kind, payload = 'result', []
        except Exception as e:
            log.error("ToolInvertGerber.worker_job() --> %s\n%s" % (str(e), traceback.format_exc()))
            kind, payload = 'error', str(e)
        self.job_done.emit(kind, payload)

    # ------------------------------------------------------------------------------------------------------------
    # the end of the job (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def on_job_done(self, kind, payload):
        self._set_busy(False)
        try:
            if kind == 'cancelled':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            elif kind == 'result':
                self.finish(payload)
            else:
                self._error('%s: %s' % (_("Invert Gerber failed"), payload))
        except Exception as e:
            log.error("ToolInvertGerber.on_job_done() --> %s\n%s" % (str(e), traceback.format_exc()))
            self._error('%s: %s' % (_("Invert Gerber failed"), str(e)))

    def finish(self, results):
        """Create one object per source, as ONE history group."""
        collection = self.app.collection
        done = []
        history = history_of(self.app)
        with history.group(self.pluginName):
            for source, polys in results:
                if not polys:
                    continue
                outname = unique_name(source.obj_options['name'] + "_inverted", collection.get_names())
                if self._create(source, polys, outname) is None:
                    return
                done.append(outname)

        if not done:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "There is no area to invert, or the copper covers all of it. Nothing was created."))
        elif len(done) < len(results):
            self.app.inform.emit('[success] %s: %s. %s' % (
                _("Created"), ', '.join(done), _("Some objects had no area to invert.")))
        else:
            self.app.inform.emit('[success] %s: %s' % (_("Created"), ', '.join(done)))

    def _create(self, source, polys, outname):
        def init_func(new_obj, app_obj):
            for key, value in (source.obj_options or {}).items():
                if key != 'name':
                    new_obj.obj_options[key] = deepcopy(value)
            new_obj.obj_options['name'] = outname
            new_obj.fill_color = deepcopy(source.fill_color)
            new_obj.outline_color = deepcopy(source.outline_color)
            new_obj.tools = apertures_from_polygons(polys)
            new_obj.solid_geometry = list(polys)
            new_obj.regenerate_source(name=outname)

        ret = self.new_object('gerber', outname, init_func, source=source, autoselected=False)
        if ret is None or (isinstance(ret, str) and ret == 'fail'):
            self._error(_("Generating new object failed."))
            return None
        return outname

    def reset_fields(self):
        self.ui.gerber_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
