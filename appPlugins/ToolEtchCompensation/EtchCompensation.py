# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 2/14/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from defaults import AppDefaults
from appCommon.SafeEval import safe_eval
from appHandlers.appHistory import history_of
from appObjects.ProjectContext import unique_name
from appPlugins.ToolEtchCompensation.EtchCompensationUI import EtchUI
from appPlugins.ToolEtchCompensation.EtchCompensationGen import compensate_gerber, lateral_offset_um, um_to_units
import appPlugins.ToolEtchCompensation as etch_plugin

import logging
import traceback
from copy import deepcopy

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolEtchCompensation(AppTool):
    """
    The values are read in the GUI thread. The geometry work runs as one job on the app worker; the job ends with one
    job_done signal and the objects are created in the GUI thread (on_job_done).
    """

    # (kind, payload): 'preview' list of polygons | 'result' (params, [(source, result)]) | 'invalid' | 'error' text
    job_done = QtCore.pyqtSignal(str, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)

        self._busy = False
        self._preview_drawn = False
        self.job_done.connect(self.on_job_done)

        self.ui = etch_plugin.EtchUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolEtchCompensation()")
        self.app.log.debug("ToolEtchCompensation() is running ...")

        if toggle:
            # if the splitter is hidden, display it
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
        self.app.ui.notebook.setTabText(2, _("Etch Compensation"))

    def connect_signals_at_init(self):
        self.ui.compensate_btn.clicked.connect(self.on_compensate)
        self.ui.preview_button.clicked.connect(self.on_preview)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        self.ui.ratio_radio.activated_custom.connect(self.on_ratio_change)
        self.ui.oz_entry.textChanged.connect(self.on_oz_conversion)
        self.ui.mils_entry.textChanged.connect(self.on_mils_conversion)

        self.ui.thick_entry.valueChanged.connect(self.update_lateral_label)
        self.ui.offset_entry.valueChanged.connect(self.update_lateral_label)
        self.ui.factor_entry.textChanged.connect(self.update_lateral_label)
        self.ui.etchants_combo.currentIndexChanged.connect(self.update_lateral_label)

    def on_plugin_cleanup(self):
        self.clear_preview()

    def _opt(self, key):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

    def set_tool_ui(self):
        self.clear_preview()
        self.clear_ui(self.layout)
        self.ui = etch_plugin.EtchUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.ui.thick_entry.set_value(float(self._opt("tools_etch_thickness")))
        self.ui.factor_entry.set_value(self._opt("tools_etch_factor"))
        self.ui.offset_entry.set_value(float(self._opt("tools_etch_offset")))
        idx = self.ui.etchants_combo.findData(self._opt("tools_etch_etchant"))
        self.ui.etchants_combo.setCurrentIndex(max(0, idx))
        ratio = self._opt("tools_etch_ratio")
        self.ui.ratio_radio.set_value(ratio)
        self.ui.on_ratio_change(self.ui.ratio_radio.get_value())
        self.ui.multi_cb.set_value(False)

        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.gerber_combo.set_value(obj_name)
        self.update_lateral_label()
        self._set_busy(self._busy)

    def on_ratio_change(self, val):
        self.ui.on_ratio_change(val)
        self.update_lateral_label()

    def on_oz_conversion(self, txt):
        try:
            val = safe_eval(txt)
            # oz thickness to mils, then mils to microns
            val *= 34.798
        except Exception:
            self.ui.oz_to_um_entry.set_value('')
            return
        self.ui.oz_to_um_entry.set_value(val, self.decimals)

    def on_mils_conversion(self, txt):
        try:
            val = safe_eval(txt)
            val *= 25.4
        except Exception:
            self.ui.mils_to_um_entry.set_value('')
            return
        self.ui.mils_to_um_entry.set_value(val, self.decimals)

    # ------------------------------------------------------------------------------------------------------------
    # the values (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def read_values(self):
        """The values of the form: thickness [um], mode, factor, etchant key, manual offset [um]."""
        ui = self.ui
        return {
            'thickness': ui.thick_entry.get_value(),
            'mode': ui.ratio_radio.get_value(),
            'factor': ui.factor_entry.get_value(),
            'etchant': ui.etchants_combo.currentData(),
            'offset_um': ui.offset_entry.get_value(),
        }

    def offset_of(self, values):
        """Offset in um; ValueError for values that can not give an offset."""
        off_um = lateral_offset_um(values['thickness'], values['mode'], factor=values['factor'],
                                   etchant=values['etchant'], offset_um=values['offset_um'])
        return off_um

    def update_lateral_label(self, *args):
        try:
            off_um = self.offset_of(self.read_values())
            text = "%.*f um = %.*f %s" % (self.decimals, off_um, self.decimals + 2,
                                          um_to_units(off_um, self.app.app_units), self.app.app_units)
        except (ValueError, TypeError) as e:
            text = str(e)
        try:
            self.ui.lateral_value.set_value(text)
        except RuntimeError:
            pass

    def collect(self):
        """Read the form and look the Gerber objects up. Returns a dict, or None after a message."""
        values = self.read_values()
        try:
            off_um = self.offset_of(values)
        except (ValueError, TypeError) as e:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
            return None

        if self.ui.multi_cb.get_value():
            sources = [o for o in self.app.collection.get_selected() if o.kind == 'gerber']
            if not sources:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No Gerber object is selected."))
                return None
        else:
            obj_name = self.ui.gerber_combo.currentText()
            try:
                src = self.app.collection.get_by_name(obj_name)
            except Exception as e:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
                log.debug("ToolEtchCompensation.collect() --> %s" % str(e))
                return None
            if src is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), obj_name or 'None'))
                return None
            sources = [src]

        return {
            'sources': sources, 'values': values, 'off_um': off_um,
            'units': self.app.app_units,
            'options': self.app.options,
            'suffix': str(self._opt("tools_etch_suffix") or ''),
        }

    def _set_busy(self, busy):
        self._busy = busy
        try:
            self.ui.compensate_btn.setEnabled(not busy)
            self.ui.preview_button.setEnabled(not busy)
        except RuntimeError:
            pass

    def _start(self, params, mode):
        if self._busy:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Another Etch Compensation run is still working."))
            return
        self._set_busy(True)
        self.app.inform.emit('%s' % _("Working..."))
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [params, mode]})

    def on_compensate(self):
        self.app.log.debug("ToolEtchCompensation.on_compensate()")
        params = self.collect()
        if params is None:
            return
        if params['off_um'] == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The lateral offset is zero. Nothing was created."))
            return
        self.clear_preview()
        self._start(params, 'apply')

    def on_preview(self):
        params = self.collect()
        if params is None:
            return
        params['sources'] = params['sources'][:1]
        self._start(params, 'preview')

    # ------------------------------------------------------------------------------------------------------------
    # the job (worker thread): geometry only, no Qt widgets, no collection changes
    # ------------------------------------------------------------------------------------------------------------
    def worker_job(self, params, mode):
        kind, payload = 'error', ''
        values = params['values']
        try:
            with self.app.proc_container.new('%s...' % _("Working")):
                results = []
                for src in params['sources']:
                    # the preview needs only the outline, the apertures are left out
                    apertures = {} if mode == 'preview' else src.tools
                    res = compensate_gerber(
                        src.solid_geometry, apertures, values['thickness'], values['mode'], params['units'],
                        params['options'], factor=values['factor'], etchant=values['etchant'],
                        offset_um=values['offset_um'])
                    results.append((src, res))
                if mode == 'preview':
                    kind, payload = 'preview', results[0][1]['solid_geometry']
                else:
                    kind, payload = 'result', (params, results)
        except ValueError as e:
            kind, payload = 'invalid', str(e)
        except Exception as e:
            log.error("ToolEtchCompensation.worker_job() --> %s\n%s" % (str(e), traceback.format_exc()))
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
            elif kind == 'invalid':
                self.app.inform.emit('[ERROR_NOTCL] %s' % str(payload))
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Etch Compensation failed"), payload))
        except Exception as e:
            log.error("ToolEtchCompensation.on_job_done() --> %s\n%s" % (str(e), traceback.format_exc()))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Etch Compensation failed"), str(e)))

    def finish(self, params, results):
        """Create one Gerber per source, as ONE Undo step."""
        kept = []
        for src, res in results:
            if not res['solid_geometry']:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The compensated copper is empty."))
                continue
            kept.append((src, res))
        if not kept:
            return
        results = kept

        collection = self.app.collection
        history = history_of(self.app)
        created = []
        with history.group(self.pluginName):
            for src, res in results:
                outname = unique_name(src.obj_options['name'] + params['suffix'], collection.get_names())
                # Gen returned fresh geometry and apertures: they are handed over as they are
                new_options = deepcopy(dict(src.obj_options))

                def init_func(new_obj, app_obj, src=src, res=res, outname=outname, new_options=new_options):
                    new_obj.obj_options.update(new_options)
                    new_obj.obj_options['name'] = outname
                    new_obj.fill_color = deepcopy(src.fill_color)
                    new_obj.outline_color = deepcopy(src.outline_color)
                    new_obj.follow_geometry = deepcopy(getattr(src, 'follow_geometry', []))
                    new_obj.aperture_macros = deepcopy(getattr(src, 'aperture_macros', {}))
                    new_obj.export_as_regions = bool(getattr(src, 'export_as_regions', False))
                    new_obj.multigeo = getattr(src, 'multigeo', False)
                    new_obj.tools = res['apertures']
                    new_obj.solid_geometry = res['solid_geometry']
                    new_obj.regenerate_source(name=outname)

                ret = self.new_object('gerber', outname, init_func, source=src, autoselected=False)
                if ret is None or (isinstance(ret, str) and ret == 'fail'):
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Generating new object failed."))
                    return
                created.append(outname)

        self.store_values(params['values'])
        off_um = results[0][1]['offset_um']
        off = results[0][1]['offset']
        self.app.inform.emit('[success] %s: %s. %s %.*f um (%.*f %s)' % (
            _("Created"), ', '.join(created), _("Lateral offset"), self.decimals, off_um,
            self.decimals + 2, off, params['units']))

    def store_values(self, values):
        """Keep the values used for the session."""
        self.app.options["tools_etch_thickness"] = values['thickness']
        self.app.options["tools_etch_ratio"] = values['mode']
        self.app.options["tools_etch_etchant"] = values['etchant']
        if values['factor'] is not None:
            self.app.options["tools_etch_factor"] = values['factor']
        if values['offset_um'] is not None:
            self.app.options["tools_etch_offset"] = values['offset_um']

    # ------------------------------------------------------------------------------------------------------------
    # the preview (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def draw_preview(self, polygons):
        self.clear_preview()
        shapes = self.app.tool_shapes
        if shapes is None:
            return
        if not polygons:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The compensated copper is empty."))
            return
        for poly in polygons:
            shapes.add(poly, color='#00AA00FF', face_color='#00AA0000', update=False, layer=0, tolerance=None)
        shapes.redraw()
        self._preview_drawn = True

    def clear_preview(self):
        if self._preview_drawn and self.app.tool_shapes is not None:
            self.app.tool_shapes.clear(update=True)
        self._preview_drawn = False

    def reset_fields(self):
        self.ui.gerber_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
