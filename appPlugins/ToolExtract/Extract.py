# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 1/10/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea, FCCheckBox
from appObjects.ProjectContext import unique_name
from defaults import AppDefaults
from appPlugins.ToolExtract.ExtractUI import ExtractUI
from appPlugins.ToolExtract import ExtractGen
from appHandlers.appJoinConvert import geometry_shapes

import logging
import traceback

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolExtract(AppTool):
    """
    Threads: the widgets, the object collection and the messages are used in the GUI thread only. A click reads all the
    values and puts ONE job on the app worker (the ExtractGen work). The job ends with one job_done signal; the new
    object is created in on_job_done(), in the GUI thread, so the Undo step is recorded as for any other object.
    """

    # (kind, payload): 'result' (params, ExtractResult) | 'error' text
    job_done = QtCore.pyqtSignal(str, object)

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.decimals = self.app.decimals

        # store here the old object name
        self.old_name = ''
        self._busy = False
        self._loading = False
        # the name of the Gerber that the table shows
        self._table_name = ''

        # #############################################################################################################
        # ######################### Tool GUI ##########################################################################
        # #############################################################################################################
        self.ui = ExtractUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # connected ONCE; the widgets are connected each time the UI is built
        self.job_done.connect(self.on_job_done)

    def _opt(self, key):
        return self.app.options.get(key, AppDefaults.factory_defaults.get(key))

    def _is_open(self):
        try:
            return self.app.ui.plugin_scroll_area.widget() is self
        except (AttributeError, RuntimeError):
            return False

    def _combo_object(self):
        """The object of the source combo (any kind), or None."""
        selection_index = self.ui.gerber_object_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.gerber_object_combo.rootModelIndex())
        try:
            return model_index.internalPointer().obj
        except Exception:
            return None

    def _combo_gerber(self):
        """The Gerber of the source combo, or None."""
        obj = self._combo_object()
        return obj if obj is not None and getattr(obj, 'kind', None) == 'gerber' else None

    def on_object_combo_changed(self, *args):
        if self._loading or not self._is_open():
            return
        self.select_source()

    def select_source(self):
        """The source Gerber changed: clear the marks of the old one, enable the marks of the new, build the table."""
        grb_obj = self._combo_gerber()

        # the old marks first
        if self.old_name != '':
            old_obj = self.app.collection.get_by_name(self.old_name)
            if old_obj:
                old_obj.clear_plot_apertures()
                old_obj.mark_shapes.enabled = False
            self.old_name = ''

        # enable mark shapes
        if grb_obj:
            grb_obj.mark_shapes.enabled = True

            # create storage for shapes
            for ap_code in grb_obj.tools:
                grb_obj.mark_shapes_storage[ap_code] = []

            self.old_name = grb_obj.obj_options['name']

        self.build_tool_ui()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+E', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("Extract Drills()")

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

    def connect_signals_at_init(self):
        # ## Signals
        ui = self.ui
        ui.method_radio.activated_custom.connect(self.on_extract_drills_method_changed)
        ui.type_obj_radio.activated_custom.connect(self.on_type_obj_changed)

        for cb in ui.pad_boxes + (ui.include_regions_cb,):
            cb.stateChanged.connect(self.on_type_changed)

        ui.e_drills_button.clicked.connect(self.on_extract_drills_click)
        ui.e_sm_button.clicked.connect(self.on_extract_soldermask_click)
        ui.e_cut_button.clicked.connect(self.on_extract_cutout_click)
        ui.reset_button.clicked.connect(self.set_tool_ui)

        ui.apertures_table.itemSelectionChanged.connect(self.update_preview)
        ui.all_selected_cb.stateChanged.connect(self.update_preview)
        ui.gerber_object_combo.currentIndexChanged.connect(self.on_object_combo_changed)

    def update_ring_entries(self):
        if self.ui.type_obj_radio.get_value() == 'geo':
            self.ui.circular_ring_entry.setEnabled(True)
            return
        for cb, entry in zip(self.ui.pad_boxes, self.ui.ring_entries):
            entry.setEnabled(cb.get_value())

    def on_type_obj_changed(self, val):
        """The source type changed: the combo lists the objects of that type, the Gerber only options are hidden."""
        self.ui.gerber_object_combo.setRootModelIndex(
            self.app.collection.index({'grb': 0, 'geo': 2}[val], 0, QtCore.QModelIndex()))
        self.ui.gerber_object_combo.setCurrentIndex(0)
        self.ui.gerber_object_combo.obj_type = {'grb': 'Gerber', 'geo': 'Geometry'}[val]
        self.ui.set_source_type(val)
        self.update_ring_entries()
        if not self._loading:
            self.select_source()

    def on_type_changed(self, *args):
        """A pad type (or Include regions) was toggled: the table is rebuilt once, the selection is kept."""
        self.update_ring_entries()
        if self._loading:
            return
        self.build_tool_ui()

    def set_tool_ui(self, *args):
        self._loading = True
        try:
            self.clear_ui(self.layout)
            self.ui = ExtractUI(layout=self.layout, app=self.app)
            self.add_object_creation_controls()
            self.pluginName = self.ui.pluginName
            self.connect_signals_at_init()
            self._set_busy(self._busy)

            ui = self.ui
            ui.method_radio.set_value(self._opt("tools_extract_hole_type"))
            ui.dia_entry.set_value(float(self._opt("tools_extract_hole_fixed_dia")))
            ui.circular_ring_entry.set_value(float(self._opt("tools_extract_circular_ring")))
            ui.oblong_ring_entry.set_value(float(self._opt("tools_extract_oblong_ring")))
            ui.square_ring_entry.set_value(float(self._opt("tools_extract_square_ring")))
            ui.rectangular_ring_entry.set_value(float(self._opt("tools_extract_rectangular_ring")))
            ui.other_ring_entry.set_value(float(self._opt("tools_extract_others_ring")))

            ui.circular_cb.set_value(self._opt("tools_extract_circular"))
            ui.oblong_cb.set_value(self._opt("tools_extract_oblong"))
            ui.square_cb.set_value(self._opt("tools_extract_square"))
            ui.rectangular_cb.set_value(self._opt("tools_extract_rectangular"))
            ui.other_cb.set_value(self._opt("tools_extract_others"))
            ui.all_selected_cb.set_value(bool(self._opt("tools_extract_all_selected")))

            ui.factor_entry.set_value(float(self._opt("tools_extract_hole_prop_factor")))
            ui.oblong_slots_cb.set_value(bool(self._opt("tools_extract_oblong_slots")))

            # Extract Soldermask
            ui.clearance_entry.set_value(float(self._opt("tools_extract_sm_clearance")))
            ui.include_regions_cb.set_value(bool(self._opt("tools_extract_include_regions")))

            # Extract Cutout
            ui.margin_cut_entry.set_value(float(self._opt("tools_extract_cut_margin")))
            ui.thick_cut_entry.set_value(float(self._opt("tools_extract_cut_thickness")))
            ui.cut_shape_radio.set_value(self._opt("tools_extract_cut_shape"))

            ui.set_drill_method(ui.method_radio.get_value())
            self.update_ring_entries()

            # SELECT THE CURRENT OBJECT
            self.reset_fields()
            obj = self.app.collection.get_active()
            if obj:
                if obj.kind == 'gerber':
                    ui.gerber_object_combo.set_value(obj.obj_options['name'])
                elif obj.kind == 'geometry':
                    ui.type_obj_radio.set_value('geo')
                    self.on_type_obj_changed('geo')
                    ui.gerber_object_combo.set_value(obj.obj_options['name'])
            else:
                # take first available Gerber file, if any
                available_gerber_list = [o for o in self.app.collection.get_list() if o.kind == 'gerber']
                if available_gerber_list:
                    ui.gerber_object_combo.set_value(available_gerber_list[0].obj_options['name'])
        finally:
            self._loading = False

        self.app.ui.notebook.setTabText(2, _("Extract"))

        # the ONE table build of a launch / reset
        self._table_name = ''
        self.select_source()

    def table_codes(self):
        """(listed codes, selected codes) of the table."""
        table = self.ui.apertures_table
        listed, selected = [], []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None:
                continue
            code = int(item.text())
            listed.append(code)
            if item.isSelected():
                selected.append(code)
        return listed, selected

    def build_tool_ui(self):
        """Rebuild the table once. The selected codes are kept when the table shows the same Gerber."""
        self.ui_disconnect()
        table = self.ui.apertures_table
        obj = self._combo_gerber()
        table.blockSignals(True)
        try:
            tools = obj.tools if obj is not None else {}
            sorted_apertures = sorted(int(k) for k in tools.keys())

            name = obj.obj_options['name'] if obj is not None else ''
            selected = None
            if name != '' and name == self._table_name:
                prev_listed, prev_selected = self.table_codes()
                prev_listed, prev_selected = set(prev_listed), set(prev_selected)
                # a code that was not listed before is new: it starts selected
                selected = {c for c in sorted_apertures if c in prev_selected or c not in prev_listed}
            self._table_name = name

            table.setRowCount(0)
            self.ui.populate_apertures(tools, sorted_apertures, selected)
        finally:
            table.blockSignals(False)

        # the marks belong to the old rows
        if obj is not None:
            obj.clear_plot_apertures()

        self.ui_connect()
        self.update_preview()

    def ui_connect(self):
        self.ui.all_cb.stateChanged.connect(self.on_select_all)

        # Mark Checkboxes
        for row in range(self.ui.apertures_table.rowCount()):
            wdg = self.ui.apertures_table.cellWidget(row, 3)
            if isinstance(wdg, FCCheckBox):
                wdg.stateChanged.connect(self.on_mark_cb_click_table)

    def ui_disconnect(self):
        try:
            self.ui.all_cb.stateChanged.disconnect()
        except (AttributeError, TypeError):
            pass

        # Mark Checkboxes
        for row in range(self.ui.apertures_table.rowCount()):
            wdg = self.ui.apertures_table.cellWidget(row, 3)
            if isinstance(wdg, FCCheckBox):
                try:
                    wdg.stateChanged.disconnect()
                except (TypeError, AttributeError):
                    pass

    def on_select_all(self, state):
        boxes = self.ui.pad_boxes
        for cb in boxes:
            cb.blockSignals(True)
        try:
            for cb in boxes:
                cb.setChecked(bool(state))
        finally:
            for cb in boxes:
                cb.blockSignals(False)
        self.update_ring_entries()
        self.build_tool_ui()

    def update_preview(self, *args):
        """Pads: N, the number of pads that the drills would use."""
        ui = self.ui
        try:
            obj = self._combo_gerber()
            _listed, codes = self.table_codes()
            kinds = ui.enabled_kinds()
            count = sum(ExtractGen.count_pads(tools, sel, self.decimals, kinds)
                        for tools, sel in self.sources(obj, codes))
            ui.pads_label.setText('%s: %d' % (_("Pads"), count))
        except RuntimeError:
            pass

    def sources(self, obj, codes):
        """[(tools, codes)] for the Gerber of the combo (the table selection) and the other selected Gerbers."""
        sources = []
        if obj is not None and codes:
            sources.append((obj.tools, codes))
        sources.extend((other.tools, None) for other in self.other_gerbers(obj))
        return sources

    def other_gerbers(self, obj):
        """The other Gerbers selected in the project tree, when "All selected Gerbers" is on."""
        if not self.ui.all_selected_cb.get_value():
            return []
        return [other for other in self.app.collection.get_selected() if other.kind == 'gerber' and other is not obj]

    # ------------------------------------------------------------------------------------------------------------
    # the click: read the values (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def _set_busy(self, busy):
        self._busy = busy
        try:
            for button in (self.ui.e_drills_button, self.ui.e_sm_button, self.ui.e_cut_button):
                button.setEnabled(not busy)
        except RuntimeError:
            pass

    def _error(self, text):
        self.app.inform.emit('[ERROR_NOTCL] %s' % text)

    def _warning(self, text):
        self.app.inform.emit('[WARNING_NOTCL] %s' % text)

    def collect(self, task):
        """The source Gerber and the values for a task. Returns a dict, or None after a message."""
        if self._busy:
            self._warning(_("Another Extract run is still working."))
            return None
        obj = self._combo_object()
        if obj is None:
            self._warning(_("No object is selected."))
            return None
        ui = self.ui
        if ui.type_obj_radio.get_value() == 'geo':
            if task != 'drills' or getattr(obj, 'kind', None) != 'geometry':
                self._warning(_("The source object is not a Geometry."))
                return None
            params = {'task': task, 'obj': obj, 'name': obj.obj_options['name']}
            params['mode'] = ui.method_radio.get_value()
            params['fixed_dia'] = float(ui.dia_entry.get_value())
            params['ring'] = float(ui.circular_ring_entry.get_value())
            params['factor'] = float(ui.factor_entry.get_value()) / 100.0
            params['shapes'] = [shape for _dia, shapes in geometry_shapes(obj) for shape in shapes]
            params['outname'] = ExtractGen.out_name(params['name'], 'extracted')
            return params
        if getattr(obj, 'kind', None) != 'gerber':
            self._warning(_("The source object is not a Gerber."))
            return None

        params = {'task': task, 'obj': obj, 'name': obj.obj_options['name']}
        if task == 'cutout':
            params['margin'] = float(ui.margin_cut_entry.get_value())
            params['thickness'] = float(ui.thick_cut_entry.get_value())
            params['shape'] = ui.cut_shape_radio.get_value()
            params['geometry'] = [obj.solid_geometry] + [o.solid_geometry for o in self.other_gerbers(obj)]
            params['outname'] = ExtractGen.out_name(params['name'], 'ecut')
            return params

        _listed, codes = self.table_codes()
        sources = self.sources(obj, codes)
        if not sources:
            self._warning(_("Select one or more apertures in the table."))
            return None
        params['sources'] = sources
        params['kinds'] = ui.enabled_kinds()
        if not params['kinds']:
            self._warning(_("Enable at least one pad type."))
            return None

        if task == 'drills':
            params['mode'] = ui.method_radio.get_value()
            params['fixed_dia'] = float(ui.dia_entry.get_value())
            params['rings'] = {kind: float(entry.get_value()) for kind, entry in zip(ExtractGen.KINDS, ui.ring_entries)}
            params['factor'] = float(ui.factor_entry.get_value()) / 100.0
            params['oblong_slots'] = bool(ui.oblong_slots_cb.get_value())
            params['outname'] = ExtractGen.out_name(params['name'], 'extracted')
        else:
            params['clearance'] = float(ui.clearance_entry.get_value())
            params['include_regions'] = bool(ui.include_regions_cb.get_value())
            params['outname'] = ExtractGen.out_name(params['name'], 'esm')
        return params

    def _start(self, task):
        params = self.collect(task)
        if params is None:
            return
        self._set_busy(True)
        self.app.inform.emit('%s' % _("Working..."))
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [params]})

    def on_extract_drills_click(self):
        self._start('drills')

    def on_extract_soldermask_click(self):
        self._start('soldermask')

    def on_extract_cutout_click(self):
        self._start('cutout')

    # ------------------------------------------------------------------------------------------------------------
    # the job (worker thread): ExtractGen only, no widgets, no collection changes
    # ------------------------------------------------------------------------------------------------------------
    def worker_job(self, params):
        kind, payload = 'error', ''
        try:
            with self.app.proc_container.new('%s...' % _("Working")):
                task = params['task']
                if 'shapes' in params:
                    res = ExtractGen.extract_drills_geometry(
                        params['shapes'], params['mode'], self.decimals, fixed_dia=params['fixed_dia'],
                        ring=params['ring'], factor=params['factor'])
                elif task == 'drills':
                    res = ExtractGen.extract_drills(
                        params['sources'], params['mode'], params['kinds'], self.decimals,
                        fixed_dia=params['fixed_dia'], rings=params['rings'], factor=params['factor'],
                        oblong_slots=params['oblong_slots'])
                elif task == 'soldermask':
                    res = ExtractGen.extract_soldermask(
                        params['sources'], params['kinds'], params['clearance'], self.decimals,
                        include_regions=params['include_regions'])
                else:
                    res = ExtractGen.extract_cutout(
                        params['geometry'], params['margin'], params['thickness'], shape=params['shape'])
                kind, payload = 'result', (params, res)
        except Exception as e:
            log.error("ToolExtract.worker_job() --> %s\n%s" % (str(e), traceback.format_exc()))
            kind, payload = 'error', str(e)
        self.job_done.emit(kind, payload)

    # ------------------------------------------------------------------------------------------------------------
    # the end of the job (GUI thread)
    # ------------------------------------------------------------------------------------------------------------
    def on_job_done(self, kind, payload):
        self._set_busy(False)
        try:
            if kind == 'result':
                self.finish(*payload)
            else:
                self._error('%s: %s' % (_("Extract failed"), payload))
        except Exception as e:
            log.error("ToolExtract.on_job_done() --> %s\n%s" % (str(e), traceback.format_exc()))
            self._error('%s: %s' % (_("Extract failed"), str(e)))

    @staticmethod
    def error_text(code):
        return {
            ExtractGen.ERR_NO_APERTURES: _("No aperture matches the selected pad types."),
            ExtractGen.ERR_NO_PADS: _("The selected apertures have no pads to extract."),
            ExtractGen.ERR_BAD_DIAMETER: _("The fixed diameter must be above zero."),
            ExtractGen.ERR_BAD_THICKNESS: _("The thickness must be above zero."),
            ExtractGen.ERR_NO_CIRCLES: _("The source object has no circular shapes."),
            ExtractGen.ERR_NO_GEOMETRY: _("The source object has no geometry."),
            ExtractGen.ERR_EMPTY_SHAPE: _("The cutout shape is empty. Try a different margin."),
        }.get(code, code)

    @staticmethod
    def skipped_text(skipped):
        texts = {
            ExtractGen.SKIP_NON_POSITIVE: _("pads skipped because the hole diameter is not above zero"),
            ExtractGen.SKIP_UNKNOWN_SIZE: _("pads skipped because their size is unknown"),
            ExtractGen.SKIP_DUPLICATE: _("duplicate holes removed"),
            ExtractGen.SKIP_EMPTY: _("empty shapes skipped"),
        }
        return ', '.join('%d %s' % (count, texts.get(reason, reason)) for reason, count in skipped.items() if count)

    def finish(self, params, res):
        skipped = self.skipped_text(res.skipped)
        if res.error:
            text = self.error_text(res.error)
            self._warning('%s %s' % (text, skipped + '.' if skipped else ''))
            return

        outname = unique_name(params['outname'], self.app.collection.get_names())

        if params['task'] == 'drills':
            def obj_init(obj_inst, app_inst):
                obj_inst.tools = res.tools
                # create_geometry() builds the per-tool 'solid_geometry' itself
                if obj_inst.create_geometry() == 'fail':
                    return 'fail'
                obj_inst.regenerate_source(name=outname)
            kind = 'excellon'
        else:
            def obj_init(new_obj, app_obj):
                ExtractGen.apply_result(new_obj, res)

                try:
                    new_obj.regenerate_source(name=outname)
                except (AttributeError, TypeError):
                    pass
            kind = 'gerber'

        self.clear_aperture_marking()
        try:
            ret = self.new_object(kind, outname, obj_init, source=params['obj'], autoselected=False)
        except Exception as e:
            log.error("ToolExtract.finish() --> %s\n%s" % (str(e), traceback.format_exc()))
            self._error('%s: %s' % (_("Generating new object failed"), str(e)))
            return
        if ret is None or (isinstance(ret, str) and ret == 'fail'):
            self._error(_("Generating new object failed."))
            return

        text = '%s: %s' % (_("Created"), outname)
        if skipped:
            text = '%s. %s.' % (text, skipped)
        self.app.inform.emit('[success] %s' % text)

    def on_extract_drills_method_changed(self, val):
        ExtractUI.set_drill_method(self.ui, val)
        self.update_ring_entries()

    def on_mark_cb_click_table(self):
        """
        Will mark aperture geometries on canvas or delete the markings depending on the checkbox state
        :return:
        """

        try:
            cw = self.sender()
            cw_index = self.ui.apertures_table.indexAt(cw.pos())
            cw_row = cw_index.row()
        except AttributeError:
            cw_row = 0
        except TypeError:
            return

        try:
            aperture = int(self.ui.apertures_table.item(cw_row, 0).text())
        except AttributeError:
            return

        grb_obj = self._combo_gerber()
        if grb_obj is None:
            return

        wdg = self.ui.apertures_table.cellWidget(cw_row, 3)
        if not isinstance(wdg, FCCheckBox):
            return
        if wdg.isChecked():
            color = self._opt('global_sel_draw_color')
            color = (color + 'AA') if len(color) == 7 else (color[:-2] + 'AA')
            grb_obj.plot_aperture(color=color, marked_aperture=aperture, visible=True, run_thread=True)
        else:
            grb_obj.clear_plot_apertures(aperture=aperture)

    def on_plugin_cleanup(self):
        self._loading = True
        try:
            self.clear_aperture_marking()
            names = {self.old_name}
            obj = self._combo_gerber()
            if obj is not None:
                names.add(obj.obj_options['name'])
            for name in names:
                grb_obj = self.app.collection.get_by_name(name) if name else None
                if grb_obj is not None and getattr(grb_obj, 'kind', None) == 'gerber':
                    grb_obj.clear_plot_apertures()
                    grb_obj.mark_shapes.enabled = False
            self.old_name = ''
            self._table_name = ''
            self.reset_fields()
        finally:
            self._loading = False

    def clear_aperture_marking(self):
        """
        Will clear all aperture markings after creating an object from the extracted data

        :return:
        :rtype:
        """

        for row in range(self.ui.apertures_table.rowCount()):
            wdg = self.ui.apertures_table.cellWidget(row, 3)
            if isinstance(wdg, FCCheckBox):
                wdg.set_value(False)

    def reset_fields(self):
        self.ui.type_obj_radio.set_value('grb')
        self.ui.set_source_type('grb')
        self.ui.gerber_object_combo.obj_type = 'Gerber'
        self.ui.gerber_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.gerber_object_combo.setCurrentIndex(0)
