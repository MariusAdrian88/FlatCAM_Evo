# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/12/2020                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from defaults import AppDefaults
from appGUI.GUIElements import (
    VerticalScrollArea,
    FCFileSaveDialog,
)

import logging
from copy import deepcopy
import sys

from datetime import datetime as dt

import gettext
import appTranslation as fcTranslate
import builtins

from appEditors.appTextEditor import AppTextEditor

from camlib import CNCjob

from appPlugins.Common.levelling_core import LevellingCoreMixin, _AlContext, _iter_polygons, VORONOI_ENABLED, MAX_AVOID_TRIES
from appPlugins.ToolLevelling.LevellingGen import generate_probing_gcode

from io import StringIO


fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolLevelling(LevellingCoreMixin, CNCjob, AppTool):
    # a worker sends a probing/import result to the GUI thread with this signal
    al_result_ready_sig = QtCore.pyqtSignal(object)
    build_al_table_sig = QtCore.pyqtSignal()
    apply_autolevel_sig = QtCore.pyqtSignal()
    show_probing_geo_sig = QtCore.pyqtSignal(bool, bool)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        CNCjob.__init__(
            self,
            steps_per_circle=self.app.options.get(
                "cncjob_steps_per_circle",
                app_defaults.get("cncjob_steps_per_circle", AppDefaults.factory_defaults.get("cncjob_steps_per_circle"))),
            app=app
        )

        self._al_init_state()

        self.gcode_viewer_tab = None

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        # Keep the legacy public UI factory as the patch target.
        self.ui = sys.modules[__package__].LevelUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        self._al_core_signals_connected = False
        self.connect_signals_at_init()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolLevelling()")

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

        self.app.ui.notebook.setTabText(2, _("Levelling"))

    def connect_signals_at_init(self):
        # the signals of the tool and of the app live as long as the tool;
        # set_tool_ui() calls this method again for each new LevelUI, so
        # connect them only 1 time
        if not getattr(self, '_al_core_signals_connected', False):
            self._al_core_signals_connected = True
            self.al_result_ready_sig.connect(self._on_al_result_ready)
            self.build_al_table_sig.connect(self.build_al_table)
            self.apply_autolevel_sig.connect(self.apply_autolevel)
            self.show_probing_geo_sig.connect(self._on_show_probing_geo_sig)
            # When object selection on canvas change
            self.app.proj_selection_changed.connect(self.on_object_selection_changed)
            # Cleanup on Graceful exit (CTRL+ALT+X combo key)
            self.app.cleanup.connect(self.set_tool_ui)
        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.persist_probe_preference = self.persist_probe_preference
        for signal, slot in (
                (self.ui.plot_probing_pts_cb.stateChanged, self.on_plot_points_changed),
                (self.ui.avoid_exc_holes_cb.stateChanged, self.on_avoid_exc_holes_changed)):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        self.ui.plot_probing_pts_cb.stateChanged.connect(self.on_plot_points_changed)
        self.ui.avoid_exc_holes_cb.stateChanged.connect(self.on_avoid_exc_holes_changed)
        self.ui.avoid_exc_holes_cb.toggled.connect(self.on_avoid_exc_holes)

        self.ui.al_mode_radio.activated_custom.connect(self.on_mode_radio)
        self.ui.al_method_radio.activated_custom.connect(self.on_method_radio)
        self.ui.al_controller_combo.currentIndexChanged.connect(self.on_controller_change)
        self.ui.plot_probing_pts_cb.toggled.connect(self.show_probing_geo)
        self.ui.view_h_gcode_button.clicked.connect(self.on_edit_probing_gcode)
        self.ui.h_gcode_button.clicked.connect(self.on_save_probing_gcode)
        self.ui.import_heights_button.clicked.connect(self.on_import_height_map)

        # Reset Tool
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def persist_probe_preference(self, key, state):
        self.app.options[key] = False if not state else True

    def on_plot_points_changed(self, state):
        self.persist_probe_preference("tools_al_plot_points", state)

    def on_avoid_exc_holes_changed(self, state):
        self.persist_probe_preference("tools_al_avoid_exc_holes", state)

    def set_tool_ui(self):
        # closing or resetting the tool while adding manual points must give
        # the canvas events back to the app
        self._release_manual_mouse_events()

        self.units = self.app.app_units.upper()
        # new probe points carry a placeholder height of 0.0, so a full storage
        # alone does not make a valid map: keep the flag that a probe or an import set
        self.al_heights_valid = bool(getattr(self, 'al_heights_valid', False)) and \
            self._height_map_is_complete()
        self.clear_ui(self.layout)
        self.ui = sys.modules[__package__].LevelUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        mode_blocked = self.ui.al_mode_radio.blockSignals(True)
        method_blocked = self.ui.al_method_radio.blockSignals(True)
        try:
            self._populate_tool_ui()
        finally:
            self.ui.al_method_radio.blockSignals(method_blocked)
            self.ui.al_mode_radio.blockSignals(mode_blocked)

    def _populate_tool_ui(self):
        # Both radio groups stay blocked, including the initial to_form().
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        # try to select in the CNCJob combobox the active object
        try:
            selected_obj = self.app.collection.get_active()
            if selected_obj.kind == 'cncjob':
                current_name = selected_obj.obj_options['name']
                self.ui.object_combo.set_value(current_name)
        except Exception:
            pass

        loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        # kept probe data belongs to its owner object; drop it if the tool opens on another object
        if getattr(self, '_al_target_name', None) is not None and (
                self.ui.object_combo.get_value() != self._al_target_name or
                loaded_obj is not getattr(self, '_al_target_obj', None)):
            self._clear_probe_data()
        if loaded_obj and loaded_obj.kind == 'cncjob':
            name = loaded_obj.obj_options['name']
        else:
            name = ''

        # Shapes container for the Voronoi cells in Autolevelling
        self._new_probing_shapes(name)

        self.form_fields.update({
            "tools_al_probe_tip_dia":           self.ui.probe_tip_dia_entry,
            "tools_al_travel_z":                self.ui.ptravelz_entry,
            "tools_al_probe_depth":             self.ui.pdepth_entry,
            "tools_al_probe_fr":                self.ui.feedrate_probe_entry,
            "tools_al_controller":              self.ui.al_controller_combo,
            "tools_al_method":                  self.ui.al_method_radio,
            "tools_al_mode":                    self.ui.al_mode_radio,
            "tools_al_avoid_exc_holes_size":    self.ui.avoid_exc_holes_size_entry,
            "tools_al_rows":                    self.ui.al_rows_entry,
            "tools_al_columns":                 self.ui.al_columns_entry,
        })

        # Fill Form fields
        if self.app.options.get("tools_al_controller") == 'GRBL':
            self.app.options["tools_al_controller"] = 'MACH3'
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The Levelling Plugin no longer has GRBL. Use the GCode sender Plugin."))
        self.to_form()
        self.on_controller_change_alter_ui()

        self.ui.plot_probing_pts_cb.set_value(
            self.app.options.get(
                "tools_al_plot_points",
                app_defaults.get("tools_al_plot_points", AppDefaults.factory_defaults.get("tools_al_plot_points")))
        )
        self.ui.avoid_exc_holes_cb.set_value(
            self.app.options.get(
                "tools_al_avoid_exc_holes",
                app_defaults.get("tools_al_avoid_exc_holes", AppDefaults.factory_defaults.get("tools_al_avoid_exc_holes")))
        )

        self.ui.al_probe_points_table.setRowCount(0)
        self.ui.al_probe_points_table.resizeColumnsToContents()
        self.ui.al_probe_points_table.resizeRowsToContents()
        v_header = self.ui.al_probe_points_table.verticalHeader()
        v_header.hide()
        self.ui.al_probe_points_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        h_header = self.ui.al_probe_points_table.horizontalHeader()
        h_header.setMinimumSectionSize(10)
        h_header.setDefaultSectionSize(70)
        h_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h_header.resizeSection(0, 20)
        h_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.ui.al_probe_points_table.setMinimumHeight(self.ui.al_probe_points_table.getHeight())
        self.ui.al_probe_points_table.setMaximumHeight(self.ui.al_probe_points_table.getHeight())

        # Set initial UI
        self.ui.al_rows_entry.setDisabled(True)
        self.ui.al_rows_label.setDisabled(True)
        self.ui.al_columns_entry.setDisabled(True)
        self.ui.al_columns_label.setDisabled(True)
        self.ui.al_method_lbl.setDisabled(True)
        self.ui.al_method_radio.set_value('v')
        self.ui.al_method_radio.setDisabled(True)

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        try:
            self.ui.object_combo.currentIndexChanged.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.object_combo.currentIndexChanged.connect(self.on_object_changed)

        self.build_tool_ui()

        if self._is_supported_al_target(loaded_obj):
            self.ui.al_frame.setDisabled(False)
            self._restore_al_radios(loaded_obj)
            self.on_controller_change()
        else:
            self.ui.al_frame.setDisabled(True)

        # a retained (probed/imported) height map was preserved above (its
        # validity was already computed at the top of this method); rebuild
        # the table so the kept points show up again
        if self.al_heights_valid:
            self.build_al_table()
            # the slot plots only if 'Plot probing points' is checked
            self._on_show_probing_geo_sig(True, True)

        self.on_avoid_exc_holes(
            self.app.options.get(
                "tools_al_avoid_exc_holes",
                app_defaults.get("tools_al_avoid_exc_holes", AppDefaults.factory_defaults.get("tools_al_avoid_exc_holes")))
        )

    def on_object_changed(self):

        # load the object
        obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            target_obj = self.app.collection.get_by_name(obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
            return

        # probe points, heights and probing G-code belong to 1 object (name and
        # identity); forget them when the user selects a different object
        if obj_name != getattr(self, '_al_target_name', None) or \
                target_obj is not getattr(self, '_al_target_obj', None):
            self._clear_probe_data()

        if self._is_supported_al_target(target_obj):
            self.ui.al_frame.setDisabled(False)

            # Shapes container for the Voronoi cells in Autolevelling
            self._new_probing_shapes(obj_name)

            self._restore_al_radios(target_obj)

            # the new container is empty: draw the kept points again
            if self.al_voronoi_geo_storage:
                self._on_show_probing_geo_sig(True, True)
        else:
            self.ui.al_frame.setDisabled(True)

    @staticmethod
    def _is_supported_al_target(target_obj):
        if target_obj is None:
            return False
        obj_options = getattr(target_obj, 'obj_options', None) or {}
        return (
                (bool(getattr(target_obj, 'is_segmented_gcode', False)) or
                 getattr(target_obj, 'gcode_imported', False) is True) and
                obj_options.get('type') in ('Geometry', 'Excellon')
        )

    def to_form(self, storage=None):
        if storage is None:
            storage = self.app.options

        for k in self.form_fields:
            for option in storage:
                if option.startswith('tools_al_'):
                    if k == option:
                        try:
                            self.form_fields[k].set_value(storage[option])
                        except Exception:
                            # it may fail for form fields found in the tools tables if there are no rows
                            pass

    def on_controller_change(self):
        self.on_controller_change_alter_ui()

        # the cached Probing GCode belongs to the previous controller: drop it and build it again for the new one
        # (GRBL gets it's Probing GCode differently, so for GRBL it stays empty)
        self.probing_gcode_text = ''
        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if target_obj is not None:
            # generate Probing GCode
            al_method = self.ui.al_method_radio.get_value()
            storage = self.al_voronoi_geo_storage if al_method == 'v' else self.al_bilinear_geo_storage
            self.probing_gcode_text = self.probing_gcode(storage=storage) or ''

    def on_controller_change_alter_ui(self):
        self.ui.h_gcode_button.show()
        self.ui.view_h_gcode_button.show()
        self.ui.import_heights_button.show()

    def probing_gcode(self, storage):
        """
        :param storage:         either a dict of dicts (voronoi) or a list of tuples (bilinear)
        :return:                Probing GCode
        :rtype:                 str
        """

        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())

        time_str = "{:%A, %d %B %Y at %H:%M}".format(dt.now())

        coords = []
        al_method = self.ui.al_method_radio.get_value()
        if al_method == 'v':
            for id_key, value in storage.items():
                x = value['point'].x
                y = value['point'].y
                coords.append(
                    (
                        self.app.dec_format(x, dec=self.app.decimals),
                        self.app.dec_format(y, dec=self.app.decimals)
                    )
                )
        else:
            for pt in storage:
                x = pt[0]
                y = pt[1]
                coords.append(
                    (
                        self.app.dec_format(x, dec=self.app.decimals),
                        self.app.dec_format(y, dec=self.app.decimals)
                    )
                )

        pr_travel = self.ui.ptravelz_entry.get_value()
        probe_fr = self.ui.feedrate_probe_entry.get_value()
        pr_depth = self.ui.pdepth_entry.get_value()
        controller = self.ui.al_controller_combo.get_value()

        if controller not in ('MACH3', 'MACH4', 'LinuxCNC'):
            self.app.log.debug("CNCJobObject.probing_gcode() -> controller not supported")
            return

        return generate_probing_gcode(
            coords=coords, units=self.units, controller=controller,
            travel=pr_travel, depth=pr_depth, feedrate=probe_fr,
            coords_decimals=target_obj.coords_decimals, fr_decimals=target_obj.fr_decimals,
            name=target_obj.obj_options['name'], version=self.app.version,
            version_date=self.app.version_date, time_str=time_str)

    def on_save_probing_gcode(self):
        lines = StringIO(self.probing_gcode_text)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        _filter_ = self.app.options.get(
            'cncjob_save_filters',
            app_defaults.get('cncjob_save_filters', AppDefaults.factory_defaults.get('cncjob_save_filters'))
        )
        name = "probing_gcode"
        # Resolve the public dialog too: callers can replace the legacy export.
        file_dialog = sys.modules[__package__].FCFileSaveDialog
        try:
            dir_file_to_save = self.app.get_last_save_folder() + '/' + str(name)
            filename, _f = file_dialog.get_saved_filename(
                caption=_("Export Code ..."),
                directory=dir_file_to_save,
                ext_filter=_filter_
            )
        except TypeError:
            filename, _f = file_dialog.get_saved_filename(
                caption=_("Export Code ..."),
                ext_filter=_filter_)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Export cancelled ..."))
            return
        else:
            try:
                force_windows_line_endings = self.app.options.get(
                    'cncjob_line_ending',
                    app_defaults.get('cncjob_line_ending', AppDefaults.factory_defaults.get('cncjob_line_ending'))
                )
                if force_windows_line_endings and sys.platform != 'win32':
                    with open(filename, 'w', newline='\r\n') as f:
                        for line in lines:
                            f.write(line)
                else:
                    with open(filename, 'w') as f:
                        for line in lines:
                            f.write(line)
            except FileNotFoundError:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No such file or directory"))
                return
            except PermissionError:
                self.app.inform.emit(
                    '[WARNING] %s' % _("Permission denied, saving not possible.\n"
                                       "Most likely another app is holding the file open and not accessible.")
                )
                return 'fail'

    def on_edit_probing_gcode(self):
        self.app.proc_container.view.set_busy('%s...' % _("Loading"))

        gco = self.probing_gcode_text
        if gco is None or gco == '':
            self.app.inform.emit('[WARNING_NOTCL] %s...' % _('There is nothing to view'))
            return

        self.gcode_viewer_tab = AppTextEditor(app=self.app, plain_text=True, language='gcode')

        # add the tab if it was closed
        self.app.ui.plot_tab_area.addTab(self.gcode_viewer_tab, '%s' % _("Code Viewer"))
        self.gcode_viewer_tab.setObjectName('code_viewer_tab')

        # delete the absolute and relative position and messages in the infobar
        self.app.ui.update_location_labels(None, None, None, None)

        self.gcode_viewer_tab.code_editor.completer_enable = False
        self.gcode_viewer_tab.buttonRun.hide()

        # Switch plot_area to CNCJob tab
        self.app.ui.plot_tab_area.setCurrentWidget(self.gcode_viewer_tab)

        self.gcode_viewer_tab.t_frame.hide()
        # then append the text from GCode to the text editor
        try:
            self.gcode_viewer_tab.load_text(gco, move_to_start=True, clear_text=True)
        except Exception as e:
            self.app.log.error('FlatCAMCNCJob.on_edit_probing_gcode() -->%s' % str(e))
            return

        self.gcode_viewer_tab.t_frame.show()
        self.app.proc_container.view.set_idle()

        self.gcode_viewer_tab.buttonSave.hide()
        self.gcode_viewer_tab.buttonOpen.hide()
        self.gcode_viewer_tab.buttonPrint.hide()
        self.gcode_viewer_tab.buttonPreview.hide()
        self.gcode_viewer_tab.buttonReplace.hide()
        self.gcode_viewer_tab.sel_all_cb.hide()
        self.gcode_viewer_tab.entryReplace.hide()

        self.gcode_viewer_tab.button_update_code.show()

        # self.gcode_viewer_tab.code_editor.setReadOnly(True)

        self.gcode_viewer_tab.button_update_code.clicked.connect(self.on_update_probing_gcode)

        self.app.inform.emit('[success] %s...' % _('Loaded Machine Code into Code Viewer'))

    def on_update_probing_gcode(self):
        self.probing_gcode_text = self.gcode_viewer_tab.code_editor.toPlainText()

    def apply_autolevel(self, target_obj=None):
        """
        Apply the probed/imported height map to the currently selected
        CNCJob object's G-code and create a new '<name>_levelled' CNCJob
        object holding the result. The source object is left unchanged.

        Runs on the GUI thread (connected to apply_autolevel_sig, emitted
        from worker threads after a successful probe or height-map
        import), reads the widgets it needs here, then hands the actual
        levelling + object creation off to a worker thread.
        """
        if self._al_apply_running:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("Autolevelling apply is already running.")
            )
            return

        if target_obj is None:
            target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        elif self.app.collection.get_by_name(target_obj.obj_options['name']) is not target_obj:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The probed object is no longer in the project."))
            return
        al_method = self.ui.al_method_radio.get_value()

        if target_obj is None or target_obj.kind != 'cncjob':
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("No CNCJob object selected for autolevelling.")
            )
            return

        ctx = self._al_capture_context()
        snapshot = self._al_capture_apply_input(target_obj, units=ctx.units)
        if snapshot is None:
            return

        def context_current():
            if (self._al_context_current(ctx) and ctx.target_obj is target_obj and
                    ctx.target_name == target_obj.obj_options['name'] and
                    ctx.units == self._al_data_units and
                    ctx.units == str(target_obj.units).upper()):
                return True
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The height map or its CNCJob context changed. Apply autolevelling again."))
            return False

        if not context_current():
            return
        self._al_apply_running = True

        def worker_task():
            try:
                with self.app.proc_container.new('%s...' % _("Autolevelling")):
                    if not context_current():
                        return
                    diagnostics = []
                    tools_gcode = self.autolevell_gcode(
                        target_obj, al_method, snapshot=snapshot, diagnostics=diagnostics)
                    if tools_gcode is None:
                        return

                    outname = target_obj.obj_options['name'] + '_levelled'

                    def obj_init(new_obj, app_obj):
                        if not context_current():
                            return 'fail'
                        # copy over the tools/options needed to reconstruct the source_file,
                        # gcode_parsed (plot) and solid_geometry from the levelled per-tool
                        # G-code, exactly as export_gcode()/plot() rebuild them for any other
                        # CNCJob object.
                        #
                        # CNCJobObject.ser_attrs (appObjects/CNCJobObject.py) is the list used
                        # to save/load a project, but it does not cover everything gcode_parse()
                        # / codes_split() need (pp_geometry_name, pp_excellon_name,
                        # pp_solderpaste_name, exc_tools aren't in it - project load rebuilds
                        # those separately), so it isn't a drop-in replacement here; the explicit
                        # list below is extended instead of switching to ser_attrs.
                        new_obj.obj_options.update(
                            {k: v for k, v in target_obj.obj_options.items() if k != 'name'}
                        )
                        new_obj.units = target_obj.units
                        new_obj.multitool = target_obj.multitool
                        new_obj.used_tools = list(target_obj.used_tools)
                        new_obj.gc_start = target_obj.gc_start
                        new_obj.prepend_snippet = target_obj.prepend_snippet
                        new_obj.append_snippet = target_obj.append_snippet
                        new_obj.is_segmented_gcode = target_obj.is_segmented_gcode
                        new_obj.pp_geometry_name = getattr(target_obj, 'pp_geometry_name', 'default')
                        new_obj.pp_excellon_name = getattr(target_obj, 'pp_excellon_name', 'default')
                        new_obj.pp_solderpaste_name = getattr(target_obj, 'pp_solderpaste_name', None)
                        new_obj.exc_tools = deepcopy(getattr(target_obj, 'exc_tools', {}))
                        # deepcopy(target_obj.tools) also carries over each tool's
                        # 'solid_geometry' unchanged, on purpose: levelling only ever
                        # rewrites the Z word of cutting moves, never X/Y, so the XY
                        # footprint stays identical to the source object's.
                        #
                        # 'multigeo' is intentionally NOT copied here: it is a
                        # GeometryObject-only flag (single vs. multi-tool *Geometry*
                        # source). CNCJob objects track multi-tool-ness with
                        # 'multitool' instead, which is copied above.
                        new_obj.tools = deepcopy(target_obj.tools)

                        # accumulate the object-level gcode / gcode_parsed across all tools,
                        # in target_obj.tools order (the same order export_gcode() concatenates
                        # them into source_file); gcode_parse() only ever sets self.gcode_parsed
                        # from self.gcode, so calling it once per tool and keeping only the last
                        # result would silently drop every other tool's geometry
                        total_gcode = ''
                        total_gcode_parsed = []
                        if getattr(target_obj, 'gcode_imported', False) is True:
                            self._al_imported_result(new_obj, target_obj, tools_gcode)
                            return
                        for tooluid, levelled_text in tools_gcode.items():
                            if tooluid not in new_obj.tools:
                                continue
                            # gcode_parse() parses self.gcode (not the per-tool dict), so it
                            # has to be set as the "current" gcode for the duration of the call
                            new_obj.gcode = levelled_text
                            tool_gcode_parsed = new_obj.gcode_parse(
                                tool_data=new_obj.tools[tooluid].get('data', {})
                            )
                            new_obj.tools[tooluid]['gcode_parsed'] = tool_gcode_parsed
                            new_obj.tools[tooluid]['gcode'] = levelled_text

                            total_gcode += levelled_text
                            total_gcode_parsed += tool_gcode_parsed

                        new_obj.gcode = total_gcode
                        new_obj.gcode_parsed = total_gcode_parsed
                        new_obj.create_geometry()

                    if not context_current():
                        return
                    ret = self.new_object('cncjob', outname, obj_init, source=target_obj)
                    if ret == 'fail':
                        self.app.inform.emit(
                            '[ERROR_NOTCL] %s' % _("Failed to create the levelled CNCJob object.")
                        )
                        return

                    if diagnostics and context_current():
                        self.al_result_ready_sig.emit({
                            'kind': 'coverage', 'context': ctx, 'warnings': diagnostics})
                    self.app.inform.emit('[success] %s' % _("Finished autolevelling."))
            finally:
                self._al_apply_running = False

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    @staticmethod
    def _al_imported_result(new_obj, target_obj, tools_gcode):
        """
        Fill the levelled job of an imported G-code file. The text of the blocks is the whole levelled program
        (the blocks are slices of it, no tool holds the text of another), it is parsed once and the parsed moves are
        given to the block they come from.
        """
        from appParsers.ParseGCode import parse_gcode

        new_obj.gcode_imported = True
        new_obj.gcode_encoding = getattr(target_obj, 'gcode_encoding', 'utf-8')
        texts ={key: text for key, text in tools_gcode.items() if key in new_obj.tools}
        total_gcode = ''.join(texts.values())
        for key, text in texts.items():
            new_obj.tools[key]['gcode'] = text

        parsed = parse_gcode(total_gcode, steps_per_circle=new_obj.steps_per_circle,
                             default_tooldia=float(new_obj.obj_options.get('tooldia') or 0.0),
                             target_units=str(new_obj.units).upper())
        blocks = list(parsed['tools'].values())
        by_order = len(blocks) == len(texts)
        total_gcode_parsed = []
        for index, key in enumerate(texts):
            if by_order:
                block = blocks[index]
            else:
                tool_no = new_obj.tools[key].get('tool')
                block = next((b for b in blocks if b.get('tool') == tool_no), None)
            new_obj.tools[key]['gcode_parsed'] = list(block['gcode_parsed']) if block else []
            total_gcode_parsed += new_obj.tools[key]['gcode_parsed']

        new_obj.gcode = total_gcode
        new_obj.gcode_parsed = total_gcode_parsed
        new_obj.travel_distance = target_obj.travel_distance
        new_obj.routing_time = target_obj.routing_time
        new_obj.create_geometry()

    def ui_connect(self):
        self.ui.al_add_button.clicked.connect(self.on_add_al_probepoints)
        self.ui.show_al_table.stateChanged.connect(self.on_show_al_table)

    def ui_disconnect(self):
        try:
            self.ui.al_add_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.show_al_table.stateChanged.disconnect()
        except (TypeError, AttributeError):
            pass

    def reset_fields(self):
        self.al_heights_valid = False
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))

    # ------------------------------------------------------------------
    # LevellingCoreMixin hooks
    # ------------------------------------------------------------------
    def _al_set_buttons_enabled(self, enabled):
        self.ui.h_gcode_button.setDisabled(not enabled)
        self.ui.view_h_gcode_button.setDisabled(not enabled)

    def _al_on_height_map_ready(self, target_obj, result=None):
        self.apply_autolevel(target_obj=target_obj)
