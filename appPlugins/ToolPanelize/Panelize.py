# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolPanelize.PanelizeUI import PanelizeUI
from appPlugins.ToolPanelize import PanelizeGen
from appParsers import panelize_core
from defaults import AppDefaults

import logging
from copy import deepcopy

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class Panelize(AppTool):

    pluginName = _("Panelize PCB")

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.decimals = app.decimals
        self.app = app

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = PanelizeUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # list to hold the temporary objects
        self.objs = []

        # final name for the panel object
        self.outname = ""

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolPanelize()")

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
                        self.app.ui.notebook.removeTab(found_idx)

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
                    return
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(self.app.ui.notebook.indexOf(self.app.ui.plugin_tab), _("Panelization"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+Z', **kwargs)

    def connect_signals_at_init(self):
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.reference_radio.activated_custom.connect(self.on_reference_radio_changed)
        self.ui.panelize_object_button.clicked.connect(self.on_panelize)
        self.ui.type_obj_combo.currentIndexChanged.connect(self.on_type_obj_index_changed)
        self.ui.type_box_combo.currentIndexChanged.connect(self.on_type_box_index_changed)

        if not getattr(self, '_selection_signal_connected', False):
            self.app.proj_selection_changed.connect(self.on_object_selection_changed)
            self._selection_signal_connected = True

        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def set_tool_ui(self):
        self.clear_ui(self.layout)
        self.ui = PanelizeUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        self.reset_fields()

        self.ui.reference_radio.set_value('bbox')
        self.on_reference_radio_changed(current_val=self.ui.reference_radio.get_value())

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj:
            obj_name = obj.obj_options['name']
            if obj.kind == 'gerber':
                # run once to make sure that the obj_type attribute is updated in the FCComboBox
                self.ui.type_obj_combo.set_value(_("Gerber"))
            elif obj.kind == 'excellon':
                # run once to make sure that the obj_type attribute is updated in the FCComboBox
                self.ui.type_obj_combo.set_value(_("Excellon"))
            elif obj.kind == 'geometry':
                # run once to make sure that the obj_type attribute is updated in the FCComboBox
                self.ui.type_obj_combo.set_value(_("Geometry"))

            self.ui.object_combo.set_value(obj_name)

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        sp_c = self.app.options.get(
            "tools_panelize_spacing_columns",
            app_defaults.get("tools_panelize_spacing_columns", AppDefaults.factory_defaults.get("tools_panelize_spacing_columns")))
        self.ui.spacing_columns.set_value(float(sp_c))

        sp_r = self.app.options.get(
            "tools_panelize_spacing_rows",
            app_defaults.get("tools_panelize_spacing_rows", AppDefaults.factory_defaults.get("tools_panelize_spacing_rows")))
        self.ui.spacing_rows.set_value(float(sp_r))

        rr = self.app.options.get(
            "tools_panelize_rows",
            app_defaults.get("tools_panelize_rows", AppDefaults.factory_defaults.get("tools_panelize_rows")))
        self.ui.rows.set_value(int(rr))

        cc = self.app.options.get(
            "tools_panelize_columns",
            app_defaults.get("tools_panelize_columns", AppDefaults.factory_defaults.get("tools_panelize_columns")))
        self.ui.columns.set_value(int(cc))

        optimized_path_cb = self.app.options.get(
            "tools_panelize_optimization",
            app_defaults.get("tools_panelize_optimization", AppDefaults.factory_defaults.get("tools_panelize_optimization")))
        self.ui.optimization_cb.set_value(optimized_path_cb)

        c_cb = self.app.options.get(
            "tools_panelize_constrain",
            app_defaults.get("tools_panelize_constrain", AppDefaults.factory_defaults.get("tools_panelize_constrain")))
        self.ui.constrain_cb.set_value(c_cb)

        x_w = self.app.options.get(
            "tools_panelize_constrainx",
            app_defaults.get("tools_panelize_constrainx", AppDefaults.factory_defaults.get("tools_panelize_constrainx")))
        self.ui.x_width_entry.set_value(float(x_w))

        y_w = self.app.options.get(
            "tools_panelize_constrainy",
            app_defaults.get("tools_panelize_constrainy", AppDefaults.factory_defaults.get("tools_panelize_constrainy")))
        self.ui.y_height_entry.set_value(float(y_w))

        panel_type = self.app.options.get(
            "tools_panelize_panel_type",
            app_defaults.get("tools_panelize_panel_type", AppDefaults.factory_defaults.get("tools_panelize_panel_type")))
        # run once the following so the obj_type attribute is updated in the FCComboBoxes
        # such that the last loaded object is populated in the combo boxes
        self.on_type_obj_index_changed()
        self.on_type_box_index_changed()

        if obj and obj.kind in ('gerber', 'excellon', 'geometry'):
            self.ui.object_combo.set_value(obj.obj_options['name'])
        self.ui.panel_type_radio.set_value(panel_type)
        self.ui.on_panel_type(val=panel_type)
        if self.ui.type_obj_combo.currentIndex() == 1:
            self.ui.optimization_cb.setDisabled(True)

        self.connect_signals_at_init()

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level", app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

    def on_type_obj_index_changed(self):
        obj_type = self.ui.type_obj_combo.currentIndex()
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.object_combo.setCurrentIndex(0)

        self.ui.object_combo.obj_type = {0: "Gerber", 1: "Excellon", 2: "Geometry"}[obj_type]

        # hide the panel type for Excellons, the panel can be only of type Geometry or Gerber
        if obj_type != 1:   # not Excellon
            self.ui.panel_type_label.setDisabled(False)
            self.ui.panel_type_radio.setDisabled(False)
            panel_type = self.ui.panel_type_radio.get_value()
            self.ui.on_panel_type(val=panel_type)
        else:
            self.ui.panel_type_label.setDisabled(True)
            self.ui.panel_type_radio.setDisabled(True)
            self.ui.optimization_cb.setDisabled(True)

        if obj_type in [0, 2]:
            # type_box_combo is missing the Excellon therefore it has only index 0 an 1
            self.ui.type_box_combo.setCurrentIndex(0) if obj_type == 0 else self.ui.type_box_combo.setCurrentIndex(1)
            self.ui.panel_type_radio.set_value(self.ui.object_combo.obj_type.lower())

    def on_type_box_index_changed(self):
        obj_type = self.ui.type_box_combo.currentIndex()
        obj_type = 2 if obj_type == 1 else obj_type
        self.ui.box_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.box_combo.setCurrentIndex(0)

        self.ui.box_combo.obj_type = {0: "Gerber", 2: "Geometry"}[obj_type]

    def on_reference_radio_changed(self, current_val):
        PanelizeUI.set_reference(self.ui, current_val)

    def on_object_selection_changed(self, current, previous):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                name = current.indexes()[0].internalPointer().obj.obj_options['name']
                kind = current.indexes()[0].internalPointer().obj.kind

                if kind in ['gerber', 'excellon', 'geometry']:
                    obj_type = {
                        "gerber": _("Gerber"), "excellon": _("Excellon"), "geometry": _("Geometry")
                    }[kind]

                    self.ui.type_obj_combo.set_value(obj_type)
                    self.ui.type_box_combo.set_value(obj_type)

                self.ui.object_combo.set_value(name)
                self.ui.box_combo.set_value(name)
            except Exception:
                pass

    def change_level(self, level):
        """

        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """

        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        PanelizeUI.set_level(self.ui, checked)

    def on_panelize(self):
        name = self.ui.object_combo.currentText()

        # delete any selection box
        self.app.delete_selection_shape()

        # Get source object to be panelized.
        try:
            panel_source_obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("Panelize.on_panelize() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return

        if panel_source_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' %
                                 (_("Object not found"), name))
            return

        box_obj = panel_source_obj
        if self.ui.reference_radio.get_value() == 'object':
            boxname = self.ui.box_combo.currentText()
            try:
                box_obj = self.app.collection.get_by_name(boxname)
            except Exception as e:
                self.app.log.error("Panelize.on_panelize() --> %s" % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), boxname))
                return

            if box_obj is None:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("No object Box. Using instead"), name))
                self.ui.reference_radio.set_value('bbox')
                box_obj = panel_source_obj

        outname = name + '_panelized'
        self.outname = outname

        spacing_columns = float(self.ui.spacing_columns.get_value())
        spacing_columns = spacing_columns if spacing_columns is not None else 0

        spacing_rows = float(self.ui.spacing_rows.get_value())
        spacing_rows = spacing_rows if spacing_rows is not None else 0

        rows = int(self.ui.rows.get_value())
        rows = rows if rows is not None else 1

        columns = int(self.ui.columns.get_value())
        columns = columns if columns is not None else 1

        constrain_dx = float(self.ui.x_width_entry.get_value())
        constrain_dy = float(self.ui.y_height_entry.get_value())

        panel_type = str(self.ui.panel_type_radio.get_value())

        if columns < 1 or rows < 1:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Columns or Rows are zero value. Change them to a positive integer."))
            return

        constrained = False
        try:
            box_bounds = box_obj.bounds()
            if self.ui.constrain_cb.isChecked():
                rows, columns, constrained = panelize_core.constrain_grid(
                    box_bounds, rows, columns, spacing_columns, spacing_rows, constrain_dx, constrain_dy)
                if rows < 1 or columns < 1:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("The reference object does not fit the constrain area."))
                    return

            offsets = panelize_core.grid_offsets(box_bounds, rows, columns, spacing_columns, spacing_rows)
        except (ValueError, TypeError) as e:
            self.app.log.error("Panelize.on_panelize() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
            return

        to_optimize = self.ui.optimization_cb.get_value()

        def abort():
            return self.app.abort_flag

        last_shown = {}

        def progress(element, percent, tag):
            # show a change only, the core reports every record
            if last_shown.get((element, tag)) != percent:
                last_shown[(element, tag)] = percent
                self.app.proc_container.update_view_text(' %s: %d %s%d%%' % (_("Copy"), element, tag, percent))

        def panelize_worker():
            if panel_source_obj is not None:
                self.app.inform.emit(_("Generating panel ... "))

                def job_init_excellon(obj_fin, app_obj):
                    obj_fin.multitool = True
                    obj_fin.tools = panelize_core.panelize_excellon(
                        panel_source_obj.tools, offsets, abort=abort, progress=progress)
                    obj_fin.solid_geometry = []
                    # the holes keep their TO ids, so the table of the ids goes with them
                    for name in ('attributes', 'file_attributes', 'to_attributes'):
                        if name in vars(panel_source_obj):
                            setattr(obj_fin, name, deepcopy(vars(panel_source_obj)[name]))

                    for option in panel_source_obj.obj_options:
                        if option != 'name':
                            try:
                                obj_fin.obj_options[option] = panel_source_obj.obj_options[option]
                            except KeyError:
                                app_obj.log.warning("Failed to copy option. %s" % str(option))

                    obj_fin.create_geometry()
                    obj_fin.zeros = panel_source_obj.zeros
                    obj_fin.units = panel_source_obj.units
                    app_obj.inform.emit('%s' % _("Generating panel ... Adding the source code."))
                    obj_fin.regenerate_source(name=outname)
                    app_obj.proc_container.update_view_text('')

                def job_init_geometry(new_obj, app_obj):
                    if panel_source_obj.kind == 'geometry':
                        new_obj.multigeo = panel_source_obj.multigeo
                        new_obj.tools, new_obj.solid_geometry = panelize_core.panelize_geometry(
                            panel_source_obj.tools, panel_source_obj.solid_geometry, panel_source_obj.multigeo,
                            offsets, abort=abort, progress=progress)
                    elif panel_source_obj.kind == 'gerber':
                        # The Geometry tool table is made before panelizing, like convert_any2geo does: every tool
                        # holds the composed copper of the Gerber.
                        built = self.app.edit_class.build_geometry_tool_table()
                        if built is None:
                            raise ValueError("The tool diameters of the Geometry could not be read.")
                        default_data, built_tools, tools_diameters = built
                        new_obj.tools, new_obj.solid_geometry = PanelizeGen.geometry_from_gerber(
                            built_tools, panel_source_obj.solid_geometry, offsets, abort=abort, progress=progress)
                        new_obj.multigeo = True
                        new_obj.obj_options.update(deepcopy(default_data))
                        new_obj.obj_options["tools_mill_tooldia"] = tools_diameters[0] if tools_diameters else 0.0

                    # #################################################################################################
                    # ###########################   Path Optimization   ###############################################
                    # #################################################################################################
                    if panel_source_obj.kind == 'geometry' and panel_source_obj.multigeo is True:
                        # I'm going to do this only here as a fix for panelizing cutouts
                        # I'm going to separate linestrings out of the solid geometry from other
                        # possible type of elements and apply unary_union on them to fuse them
                        if to_optimize is True:
                            app_obj.inform.emit('%s' % _("Optimizing the overlapping paths."))

                        panelize_core.merge_tool_lines(new_obj.tools, snap_overlaps=to_optimize is True, abort=abort)

                        if to_optimize is True:
                            app_obj.inform.emit('%s' % _("Optimization complete."))


                    app_obj.inform.emit('%s' % _("Generating panel ... Adding the source code."))
                    new_obj.regenerate_source(name=outname)

                    # new_obj.solid_geometry = unary_union(obj_fin.solid_geometry)
                    # app_obj.log.debug("Finished creating a unary_union for the panel.")
                    app_obj.proc_container.update_view_text('')

                def job_init_gerber(new_obj, app_obj):
                    if panel_source_obj.kind == 'gerber':
                        new_obj.tools, new_obj.solid_geometry = panelize_core.panelize_gerber(
                            panel_source_obj.tools, panel_source_obj.solid_geometry, offsets, abort=abort,
                            progress=progress)
                        panelize_core.copy_gerber_attributes(new_obj, panel_source_obj)
                    else:
                        # The Gerber apertures are made from the Geometry before panelizing.
                        new_obj.tools, new_obj.solid_geometry, as_regions = PanelizeGen.gerber_from_geometry(
                            panel_source_obj.tools, panel_source_obj.solid_geometry, panel_source_obj.multigeo,
                            offsets, abort=abort, progress=progress)
                        new_obj.multitool = False
                        new_obj.multigeo = True
                        new_obj.follow = False
                        new_obj.follow_geometry = []
                        # The native writer draws a region record only if its 'solid' is a polygon: a line or a
                        # point is written as a stroke or a flash with no aperture, which parses back as nothing.
                        # Such a panel is exported as regions of its composed solid_geometry instead.
                        new_obj.export_as_regions = as_regions

                    app_obj.inform.emit('%s' % _("Generating panel ... Adding the source code."))

                    new_obj.regenerate_source(name=outname)

                    # new_obj.solid_geometry = unary_union(new_obj.solid_geometry)
                    # app_obj.log.debug("Finished creating a unary_union for the panel.")
                    app_obj.proc_container.update_view_text('')

                self.app.inform.emit('%s: %d' % (_("Generating panel... Spawning copies"), (int(rows * columns))))
                if panel_source_obj.kind == 'excellon':
                    return self.new_object("excellon", outname, job_init_excellon,
                                    source=panel_source_obj, plot=True, autoselected=False)
                else:
                    if panel_type == 'geometry':
                        return self.new_object('geometry', outname, job_init_geometry,
                                        source=panel_source_obj, plot=True, autoselected=False)
                    if panel_type == 'gerber':
                        return self.new_object('gerber', outname, job_init_gerber,
                                        source=panel_source_obj, plot=True, autoselected=False)

        def job_thread(app_obj):
            with self.app.proc_container.new('%s...' % _("Working")):
                try:
                    result = panelize_worker()
                    if result == 'fail' or result is False or result is None:
                        app_obj.collection.promises.discard(outname)
                        app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Panelize failed"), outname))
                        return
                except Exception as ee:
                    self.app.log.error(str(ee))
                    app_obj.collection.promises.discard(outname)
                    app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Panelize failed"), str(ee)))
                    return

                # reported after the panel exists, not before the worker ran
                if constrained:
                    app_obj.inform.emit(_("{text} Too big for the constrain area. "
                                          "Final panel has {col} columns and {row} rows").format(
                        text='[WARNING] ', col=columns, row=rows))
                else:
                    app_obj.inform.emit('[success] %s' % _("Panel created successfully."))

        self.app.collection.promise(outname)
        self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})

    def reset_fields(self):
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.box_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
