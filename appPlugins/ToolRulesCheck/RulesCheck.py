# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 09/27/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea, FCFileSaveDialog
from defaults import AppDefaults
from appPlugins.ToolRulesCheck import RulesCheckGen
from appPlugins.ToolRulesCheck.RulesCheckUI import RulesUI, ROLE_LOCATION

import logging
import math

from shapely.geometry import LineString

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

MARKER_COLOR = "#ff1744ff"
SELECTED_COLOR = "#ffea00ff"
MARKER_STEPS = 24
# minimum size of the marker and of the zoom window: 0.1 mm and 2 mm
MIN_MARKER_MM = 0.1
MIN_ZOOM_MM = 2.0

# rule key stem -> (value checkbox attribute, value spinner attribute) of RulesUI; the preference keys are
# tools_cr_<stem> and tools_cr_<stem>_val and the RulesCheckGen config keys are the stems
RULE_WIDGETS = {
    'trace_size': ('trace_size_cb', 'trace_size_entry'),
    'c2c': ('clearance_copper2copper_cb', 'clearance_copper2copper_entry'),
    'c2o': ('clearance_copper2ol_cb', 'clearance_copper2ol_entry'),
    's2s': ('clearance_silk2silk_cb', 'clearance_silk2silk_entry'),
    's2sm': ('clearance_silk2sm_cb', 'clearance_silk2sm_entry'),
    's2o': ('clearance_silk2ol_cb', 'clearance_silk2ol_entry'),
    'sm2sm': ('clearance_sm2sm_cb', 'clearance_sm2sm_entry'),
    'ri': ('ring_integrity_cb', 'ring_integrity_entry'),
    'h2h': ('clearance_d2d_cb', 'clearance_d2d_entry'),
    'dh': ('drill_size_cb', 'drill_size_entry'),
    'slot': ('slot_size_cb', 'slot_size_entry'),
    'h2o': ('hole2outline_cb', 'hole2outline_entry'),
    'h2c': ('hole2copper_cb', 'hole2copper_entry'),
    'iso': ('iso_cb', 'iso_entry'),
}

# RulesCheckGen layer key -> (use checkbox, object combo) attributes of RulesUI
OBJECT_WIDGETS = {
    'copper_top': ('copper_t_cb', 'copper_t_object'),
    'copper_bottom': ('copper_b_cb', 'copper_b_object'),
    'sm_top': ('sm_t_cb', 'sm_t_object'),
    'sm_bottom': ('sm_b_cb', 'sm_b_object'),
    'silk_top': ('ss_t_cb', 'ss_t_object'),
    'silk_bottom': ('ss_b_cb', 'ss_b_object'),
    'outline': ('out_cb', 'outline_object'),
    'exc_1': ('e1_cb', 'e1_object'),
    'exc_2': ('e2_cb', 'e2_object'),
}


class RulesCheck(AppTool):

    tool_finished = QtCore.pyqtSignal(list)

    def __init__(self, app):
        AppTool.__init__(self, app)

        # #############################################################################################################
        # ######################### Tool GUI ##########################################################################
        # #############################################################################################################
        self.ui = RulesUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls(has_source=False)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # list to hold the temporary objects
        self.objs = []

        # final name for the panel object
        self.outname = ""

        # flag to signal that the constraint was activated
        self.constrain_flag = False

        # the result dicts of the last run
        self.last_results = []

        # canvas markers: all violations and the one of the selected row
        self.markers = None
        self.selected_marker = None

        # set by the Cancel button, read by the worker
        self.cancel_flag = False
        # True while the preset values are written into the spinners
        self.applying_preset = False

        # Custom Signals: connected 1 time, the UI is rebuilt on every set_tool_ui()
        self.tool_finished.connect(self.on_tool_finished)

    # def on_object_loaded(self, index, row):
    #     print(index.internalPointer().child_items[row].obj.obj_options['name'], index.data())

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolRulesCheck()")

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

        self.app.ui.notebook.setTabText(2, _("Check Rules"))


    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+R', **kwargs)

    def connect_signals_at_init(self):
        # UI signals: called for every UI build
        for cb_name, combo_name in OBJECT_WIDGETS.values():
            combo = getattr(self.ui, combo_name)
            getattr(self.ui, cb_name).stateChanged.connect(lambda st, c=combo: c.setDisabled(not st))

        self.ui.run_button.clicked.connect(self.execute)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        self.ui.cancel_button.clicked.connect(self.on_cancel)
        self.ui.export_button.clicked.connect(self.on_export_csv)
        self.ui.results_table.cellClicked.connect(self.on_row_clicked)
        self.ui.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        for _stem, (_cb_name, entry_name) in RULE_WIDGETS.items():
            getattr(self.ui, entry_name).valueChanged.connect(self.on_value_edited)

    def set_tool_ui(self):
        self.clear_markers()
        self.last_results = []
        self.clear_ui(self.layout)
        self.ui = RulesUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls(has_source=False)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # all object checkboxes unchecked and the object comboboxes disabled
        for cb_name, combo_name in OBJECT_WIDGETS.values():
            getattr(self.ui, cb_name).setChecked(False)
            getattr(self.ui, combo_name).setDisabled(True)
        for all_cb, _children in self.ui.select_groups:
            all_cb.setChecked(False)

        # the rules from the preferences
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        factory = AppDefaults.factory_defaults

        def pref(key):
            return self.app.options.get(key, app_defaults.get(key, factory.get(key)))

        for stem, (cb_name, entry_name) in RULE_WIDGETS.items():
            getattr(self.ui, cb_name).set_value(pref("tools_cr_%s" % stem))
            getattr(self.ui, entry_name).set_value(float(pref("tools_cr_%s_val" % stem)))

        self.ui.preset_combo.setCurrentIndex(0)
        self.ui.sync_select_all()
        self.reset_fields()

    def collect_inputs(self):
        """
        Read every widget and look up the objects on the GUI thread. The layers are built later, in the worker.

        :return:    (config, objects, problems); objects maps the layer keys to the app object or None
        """
        config = {}
        problems = []
        for stem, (cb_name, entry_name) in RULE_WIDGETS.items():
            enabled = bool(getattr(self.ui, cb_name).get_value())
            try:
                value = float(getattr(self.ui, entry_name).get_value())
            except (TypeError, ValueError):
                value = 0.0
                if enabled:
                    problems.append(_("Value is not valid.") + ' (%s)' % getattr(self.ui, cb_name).text())
            config[stem] = {'enabled': enabled, 'value': value}

        objects = {}
        for key, (cb_name, combo_name) in OBJECT_WIDGETS.items():
            objects[key] = None
            name = getattr(self.ui, combo_name).currentText()
            if not getattr(self.ui, cb_name).get_value() or name == '':
                continue
            obj = self.app.collection.get_by_name(name)
            if obj is None:
                problems.append('%s: %s' % (_("The object was not found"), name))
                continue
            objects[key] = obj
        return config, objects, problems

    def execute(self):
        self.app.log.debug("RuleCheck() executing")

        config, objects, problems = self.collect_inputs()
        # validation needs only to know which layers exist
        presence = {key: (True if obj is not None else None) for key, obj in objects.items()}
        for msg in RulesCheckGen.validate_config(config, presence):
            if msg not in problems:
                problems.append(msg)
        if problems:
            self.app.inform.emit('[ERROR_NOTCL] %s' % ' '.join(problems))
            return

        if not RulesCheckGen.build_jobs(config, presence):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No rule is enabled."))
            return

        self.clear_markers()
        self.cancel_flag = False
        self.ui.cancel_button.setEnabled(True)
        self.ui.run_button.setEnabled(False)
        self.app.worker_task.emit({'fcn': self.worker_job, 'params': [config, objects]})

    def is_cancelled(self):
        return self.cancel_flag or bool(getattr(self.app, 'abort_flag', False))

    def on_cancel(self):
        self.cancel_flag = True
        self.ui.cancel_button.setEnabled(False)

    def build_layers(self, objects):
        """Worker thread: the heavy geometry work. Returns (layers, error results of the objects that failed)."""
        layers = {}
        errors = []
        for key, obj in objects.items():
            layers[key] = None
            if obj is None or self.is_cancelled():
                continue
            try:
                layers[key] = RulesCheckGen.excellon_layer(obj) if key.startswith('exc') \
                    else RulesCheckGen.gerber_layer(obj)
            except Exception as err:
                log.error("RulesCheck.build_layers() --> %s" % str(err))
                name = RulesCheckGen._object_name(obj)
                result = RulesCheckGen.make_result(name, 0.0, [name])
                result['error'] = '%s: %s' % (type(err).__name__, err)
                errors.append(result)
        return layers, errors

    def worker_job(self, config, objects):
        """Worker thread: no Qt widgets here."""
        view = self.app.proc_container.view
        view.set_busy('%s' % _("Working..."))
        results = []
        try:
            layers, results = self.build_layers(objects)
            jobs = RulesCheckGen.build_jobs(config, layers)
            handles = []
            for func, args in jobs:
                if self.is_cancelled():
                    break
                try:
                    handles.append(self.app.pool.apply_async(func, args=args))
                except Exception as err:
                    handles.append(err)

            for (func, args), handle in zip(jobs, handles):
                try:
                    if isinstance(handle, Exception):
                        raise handle
                    # poll, so Cancel is honored while a job runs
                    while not handle.ready():
                        if self.is_cancelled():
                            break
                        handle.wait(0.1)
                    if self.is_cancelled():
                        break
                    results.append(handle.get())
                except Exception as err:
                    log.error("RulesCheck.worker_job() --> %s" % str(err))
                    result = RulesCheckGen.make_result(
                        args[-1] if args and isinstance(args[-1], str) else '',
                        args[-2] if len(args) > 1 and isinstance(args[-2], (int, float)) else 0.0, [])
                    result['error'] = '%s: %s' % (type(err).__name__, err)
                    results.append(result)
            if self.is_cancelled():
                results = []
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Check cancelled"))
        finally:
            view.set_idle()
            self.tool_finished.emit(results)
            self.app.log.debug("RuleCheck() finished")

    def on_tool_finished(self, res):
        self.ui.cancel_button.setEnabled(False)
        self.ui.run_button.setEnabled(True)
        if self.is_cancelled():
            # the run was cancelled: nothing to show
            return
        self.last_results = res
        self.ui.fill_results(res)
        self.ui.export_button.setEnabled(bool(res))
        self.draw_markers(res)
        self.show_results(res)

    # ------------------------------------------------------------------------------------------------------------
    # canvas markers and the results table
    # ------------------------------------------------------------------------------------------------------------
    def _min_size(self, mm):
        return mm / 25.4 if str(self.app.app_units).upper() == 'IN' else mm

    def _marker_radius(self, limit):
        return max(float(limit or 0.0) * 0.75, self._min_size(MIN_MARKER_MM))

    def _add_marker(self, collection, x, y, radius, color, linewidth):
        ring = [(x + radius * math.cos(2 * math.pi * i / MARKER_STEPS),
                 y + radius * math.sin(2 * math.pi * i / MARKER_STEPS)) for i in range(MARKER_STEPS + 1)]
        for geo in (LineString(ring), LineString([(x - radius, y), (x + radius, y)]),
                    LineString([(x, y - radius), (x, y + radius)])):
            collection.add(geo, color=color, update=True, layer=0, tolerance=None, linewidth=linewidth)

    def draw_markers(self, results):
        """A circle with a cross at every violation."""
        self.clear_markers()
        points = [(vio['x'], vio['y'], res.get('limit')) for res in results if isinstance(res, dict)
                  for vio in res.get('violations') or []]
        if not points:
            return
        try:
            if self.markers is None:
                self.markers = self.app.plotcanvas.new_shape_collection(layers=1)
            for x, y, limit in points:
                self._add_marker(self.markers, x, y, self._marker_radius(limit), MARKER_COLOR, 2)
            self.markers.redraw()
        except Exception as err:
            log.debug("RulesCheck.draw_markers() --> %s" % str(err))

    def highlight_marker(self, x, y, limit):
        """A bigger marker in another color for the selected row."""
        try:
            if self.selected_marker is None:
                self.selected_marker = self.app.plotcanvas.new_shape_collection(layers=1)
            self.selected_marker.clear(update=True)
            self._add_marker(self.selected_marker, x, y, self._marker_radius(limit) * 1.5, SELECTED_COLOR, 4)
            self.selected_marker.redraw()
        except Exception as err:
            log.debug("RulesCheck.highlight_marker() --> %s" % str(err))

    def clear_markers(self):
        for collection in (self.markers, self.selected_marker):
            if collection is None:
                continue
            try:
                collection.clear(update=True)
                collection.redraw()
            except Exception as err:
                log.debug("RulesCheck.clear_markers() --> %s" % str(err))

    def on_plugin_cleanup(self):
        self.clear_markers()

    def on_row_clicked(self, row, column=0):
        """Zoom the canvas to the violation of the row and move the cursor there."""
        item = self.ui.results_table.item(row, 0)
        location = item.data(ROLE_LOCATION) if item is not None else None
        if not location:
            return
        x, y, limit = location
        half = max(float(limit) * 10.0, self._min_size(MIN_ZOOM_MM))
        self.highlight_marker(x, y, limit)
        try:
            from vispy.geometry import Rect
            self.app.plotcanvas.fit_center(loc=(x, y), rect=Rect(x - half, y - half, 2 * half, 2 * half))
            self.app.on_jump_to(custom_location=(x, y), fit_center=False)
        except Exception as err:
            log.debug("RulesCheck.on_row_clicked() --> %s" % str(err))

    # ------------------------------------------------------------------------------------------------------------
    # CSV and presets
    # ------------------------------------------------------------------------------------------------------------
    def on_export_csv(self):
        if not self.last_results:
            return
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export CSV"), directory=self.app.get_last_save_folder() + '/rules_check',
                ext_filter="CSV Files (*.csv);;All Files (*.*)")
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export CSV"), ext_filter="CSV Files (*.csv);;All Files (*.*)")
        filename = str(filename)
        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        try:
            RulesCheckGen.results_to_csv(self.last_results, filename)
        except OSError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Cannot write the CSV file"), err))
            return
        self.app.inform.emit('[success] %s: %s' % (_("Exported"), filename))

    def on_preset_changed(self, index):
        names = list(RulesCheckGen.PRESETS)
        if index <= 0 or index > len(names):
            return
        values = RulesCheckGen.preset_values(names[index - 1], self.app.app_units)
        self.applying_preset = True
        try:
            for stem, value in values.items():
                getattr(self.ui, RULE_WIDGETS[stem][1]).set_value(value)
        finally:
            self.applying_preset = False

    def on_value_edited(self, *args):
        if not self.applying_preset and self.ui.preset_combo.currentIndex() != 0:
            self.ui.preset_combo.setCurrentIndex(0)

    def show_results(self, results):
        report = RulesCheckGen.render_report(results, self.app.decimals, self.app.app_units)

        def init(new_obj, app_obj):
            new_obj.source_file = report
            new_obj.read_only = True

        self.new_object('document', name='Rules_check_results', initialize=init, plot=False)

    def reset_fields(self):
        # self.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        # self.box_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        pass
