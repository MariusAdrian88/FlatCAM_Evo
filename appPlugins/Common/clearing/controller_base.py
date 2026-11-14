# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The part of the NCC and Paint controllers that is the same: opening the plugin tab, the tool table (selection, delete,
refresh, drag and drop), the Tools Database entry, the preview state, the exclusion zones and the canvas selection.

``ClearingToolBase`` is a base class (single inheritance, no mixin). A tool sets these hooks:

- ``key_prefix``: 'tools_ncc' / 'tools_paint', the prefix of its options
- ``tools_attr``: the name of the dict that holds its tool table
- ``tool_target``: the number of the tool in the Tools Database (NCC 5, Paint 4); the General tools (0) fit both
- ``method_map``: the ``MethodMap`` of its methods
- ``usage_name``: the name reported by ``run()``
- ``tab_title()``, ``generate_slot()``, ``type_obj_slot()``, ``multi_objects_widget()``: its texts, slots and widget
- ``extra_signals()``: the signals that only this tool has (the common ones are in ``signal_table()``)
- ``on_zones_selected(zones)``: what an Area Selection does with its zones
- ``on_tool_from_db_inserted(tool)``: adds a Tools Database record to its tool table
- ``reset_last_regions()``, ``reset_source()``
"""

import logging
from contextlib import contextmanager
from copy import deepcopy

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from shapely import Polygon

import gettext
import builtins
import appTranslation as fcTranslate

from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from camlib import deepcopy_keep_geometry
from appPlugins.Common.clearing.selection import AreaSelector

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


@contextmanager
def signals_blocked(obj):
    """Block the signals of obj and restore them on every exit path."""
    obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(False)


class ClearingToolBase(AppTool):
    key_prefix = ''
    tools_attr = ''
    tool_target = 0
    method_map = None
    usage_name = ''

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.selector = AreaSelector(self)

        # the exclusion zones, drawn in their own color for as long as the plugin is open (not saved)
        self.exclusion_list: list[Polygon] = []
        self._exclusion_shapes = None
        # the zones of the last Area Selection run: the Preview of that mode uses them
        self.last_zones: list[Polygon] = []
        # the shape collection of the preview, made on the first use
        self._preview_shapes = None
        self._preview_drawn = False
        # a preview result is drawn only if no clear_preview() happened since its job started
        self._preview_token = 0

    # ------------------------------------------------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------------------------------------------------
    @property
    def tool_store(self):
        """The tool table of the controller: {uid: {'tooldia', 'data', ...}}."""
        return getattr(self, self.tools_attr)

    def tab_title(self):
        raise NotImplementedError

    def generate_slot(self):
        raise NotImplementedError

    def type_obj_slot(self):
        raise NotImplementedError

    def multi_objects_widget(self):
        raise NotImplementedError

    def extra_signals(self):
        return []

    def on_zones_selected(self, zones):
        raise NotImplementedError

    def on_tool_from_db_inserted(self, tool):
        raise NotImplementedError

    def reset_last_regions(self):
        self.last_zones = []

    def reset_source(self):
        """Forget the objects of the last run."""

    # ------------------------------------------------------------------------------------------------------------
    # the plugin tab
    # ------------------------------------------------------------------------------------------------------------
    def run(self, toggle=True):
        self.app.defaults.report_usage(self.usage_name)

        # AppTool.run() does nothing while the Plugin tab is locked; do not touch the tabs or the UI either
        if self.app.plugin_tab_locked is True:
            return

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
        self.reset_source()
        self.build_ui()

        # all the tools are selected by default
        self.ui.tools_table.selectAll()
        self.update_ui()

        self.app.ui.notebook.setTabText(2, self.tab_title())

    def clear_context_menu(self):
        self.ui.tools_table.removeContextMenu()

    def init_context_menu(self):
        self.ui.tools_table.setupContextMenu()
        self.ui.tools_table.addContextMenu(
            _("Add"), self.on_add_tool_by_key, icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Add from DB"), self.on_tool_add_from_db_clicked,
            icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Delete"), lambda: self.on_tool_delete(rows_to_delete=None, all_tools=None),
            icon=QtGui.QIcon(self.app.resource_location + "/delete32.png")
        )

    # ------------------------------------------------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------------------------------------------------
    def signal_table(self):
        """(signal, slot) of the signals that both tools connect, then the ones of the tool."""
        ui = self.ui
        return [
            (ui.level.toggled, self.on_level_changed),
            (ui.deltool_btn.clicked, self.on_tool_delete),
            (ui.generate_button.clicked, self.generate_slot()),
            (ui.select_method_combo.currentIndexChanged, ui.on_selection),
            (ui.method_combo.currentIndexChanged, ui.on_method_changed),
            (ui.type_obj_radio.activated_custom, self.type_obj_slot()),
            (ui.reference_type_combo.currentIndexChanged, self.on_reference_combo_changed),
            (ui.apply_param_to_all.clicked, self.on_apply_param_to_all_clicked),
            # clicked() passes a bool that must not become the diameter
            (ui.search_and_add_btn.clicked, lambda: self.on_tool_add()),
            (ui.addtool_from_db_btn.clicked, self.on_tool_add_from_db_clicked),
            (self.app.proj_selection_changed, self.on_object_selection_changed),
            (ui.reset_button.clicked, self.set_tool_ui),
            # Cleanup on Graceful exit (CTRL+ALT+X combo key)
            (self.app.cleanup, self.set_tool_ui),
            (ui.preview_button.clicked, self.on_preview),
            (ui.excl_add_button.clicked, self.on_add_exclusion),
            (ui.excl_delete_button.clicked, self.on_delete_exclusions),
            (ui.obj_combo.currentIndexChanged, self.on_source_changed),
            (self.multi_objects_widget().stateChanged, self.clear_preview),
            (self.preview_done_sig, self.on_preview_done),
        ] + self.extra_signals()

    def connect_signals(self):
        for signal, slot in self.signal_table():
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError, AttributeError):
                pass
            signal.connect(slot)

    # ------------------------------------------------------------------------------------------------------------
    # the tool table
    # ------------------------------------------------------------------------------------------------------------
    def change_level(self, level):
        """
        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """
        self.ui.level.setChecked(level == 'a')
        self.on_level_changed(self.ui.level.isChecked())

    def on_toggle_all_rows(self):
        """Toggle the selection of all rows in the Tools table."""
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        if len(sel_rows) == self.ui.tools_table.rowCount():
            self.ui.tools_table.clearSelection()
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
        else:
            self.ui.tools_table.selectAll()
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
            )

    def on_row_selection_change(self):
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        # update UI only if only one row is selected otherwise having multiple rows selected will deform information
        # for the rows other that the current one (first selected)
        if len(sel_rows) == 1:
            self.update_ui()

    def on_object_selection_changed(self, current):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                name = current.indexes()[0].internalPointer().obj.obj_options['name']
                kind = current.indexes()[0].internalPointer().obj.kind

                if kind in ['gerber', 'geometry']:
                    self.ui.type_obj_radio.set_value(kind)

                self.ui.obj_combo.set_value(name)
            except Exception:
                pass

    def update_ui(self):
        with signals_blocked(self):
            sel_rows = set()
            table_items = self.ui.tools_table.selectedItems()
            if table_items:
                for it in table_items:
                    sel_rows.add(it.row())

            if not sel_rows:
                self.ui.generate_button.setDisabled(True)
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
                )
                return
            else:
                self.ui.generate_button.setDisabled(False)

            for current_row in sel_rows:
                # populate the form with the data from the tool associated with the row parameter
                try:
                    item = self.ui.tools_table.item(current_row, 3)
                    if item is None:
                        return
                    tooluid = int(item.text())
                except Exception as e:
                    self.app.log.error("Tool missing. Add a tool in the Tool Table. %s" % str(e))
                    return

                # update the QLabel that shows for which Tool we have the parameters in the UI form
                if len(sel_rows) == 1:
                    cr = self.ui.tools_table.item(current_row, 0).text()
                    self.ui.tool_data_label.setText(
                        "<b>%s: <font color='#0000FF'>%s %s</font></b>" % (_('Parameters for'), _("Tool"), cr)
                    )

                    try:
                        # set the form with data from the newly selected tool
                        for tooluid_key, tooluid_value in list(self.tool_store.items()):
                            if int(tooluid_key) == tooluid:
                                self.storage_to_form(tooluid_value['data'])
                    except Exception as e:
                        self.app.log.error("%s.update_ui() --> %s" % (type(self).__name__, str(e)))
                else:
                    self.ui.tool_data_label.setText(
                        "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
                    )

    def form_to_storage(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table, so we can't save the GUI elements values to storage
            return

        with signals_blocked(self):
            widget_changed = self.sender()
            wdg_objname = widget_changed.objectName()
            option_changed = self.name2option[wdg_objname]

            rows = sorted(set(index.row() for index in self.ui.tools_table.selectedIndexes()))
            for row in rows:
                if row < 0:
                    row = 0
                tooluid_item = int(self.ui.tools_table.item(row, 3).text())

                for tooluid_key, tooluid_val in self.tool_store.items():
                    if int(tooluid_key) == tooluid_item:
                        new_option_value = self.form_fields[option_changed].get_value()
                        if option_changed in tooluid_val:
                            tooluid_val[option_changed] = new_option_value
                        if option_changed in tooluid_val['data']:
                            tooluid_val['data'][option_changed] = new_option_value

    def rebuild_ui(self):
        # read the table tools uid
        current_uid_list = []
        for row in range(self.ui.tools_table.rowCount()):
            uid = int(self.ui.tools_table.item(row, 3).text())
            current_uid_list.append(uid)

        store = self.tool_store
        new_tools = {}
        new_uid = 1

        for current_uid in current_uid_list:
            new_tools[new_uid] = deepcopy_keep_geometry(store[current_uid])
            new_uid += 1

        # keep the same dict object: the generator and the tool state hold a reference to it
        store.clear()
        store.update(new_tools)

        # build_ui() sorts by diameter when the order is Forward or Reverse, which would undo the drag & drop
        combo = self.ui.order_combo
        if combo.isEnabled() and combo.get_value() != 0:
            with signals_blocked(combo):
                combo.set_value(0)

        # the tools table changed therefore we need to rebuild it
        QtCore.QTimer.singleShot(20, self.build_ui)

    def on_tool_delete(self, rows_to_delete=None, all_tools=None):
        """
        Will delete a tool in the tool table

        :param rows_to_delete:  which rows to delete; a row number or a list of row numbers
        :param all_tools:       delete all tools in the tool table
        :return:
        """
        deleted_tools_list = []
        store = self.tool_store

        with signals_blocked(self):
            if all_tools:
                store.clear()
            else:
                if rows_to_delete is not None and not isinstance(rows_to_delete, bool):
                    if isinstance(rows_to_delete, (int, np.integer)):
                        rows_to_delete = [rows_to_delete]
                    rows = list(rows_to_delete)
                else:
                    # clicked() passes a bool here, the context menu passes None: use the selected rows
                    rows = sorted({it.row() for it in self.ui.tools_table.selectedItems()})

                for row in rows:
                    if row is None or row < 0:
                        continue
                    try:
                        deleted_tools_list.append(int(self.ui.tools_table.item(row, 3).text()))
                    except (AttributeError, ValueError):
                        continue

                if not deleted_tools_list:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Delete failed. Select a tool to delete."))
                    return

                for t in deleted_tools_list:
                    store.pop(t, None)

        if not all_tools:
            self.app.inform.emit('[success] %s' % _("Tools deleted from Tool Table."))
        self.build_ui()
        # the selection is gone: refresh the parameters label and the Generate button
        self.update_ui()

    # ------------------------------------------------------------------------------------------------------------
    # the Tools Database
    # ------------------------------------------------------------------------------------------------------------
    def on_tool_add_from_db_executed(self, tool):
        """Here add the tool from DB in the selected geometry object."""
        tool_from_db = deepcopy(tool)

        if tool['data']['tool_target'] not in [0, self.tool_target]:   # [General, this plugin]
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
            return 'fail'

        res = self.on_tool_from_db_inserted(tool=tool_from_db)

        if res == 'fail':
            return 'fail'
        self.app.inform.emit('[success] %s' % _("Tool from DB added in Tool Table."))

        # select last tool added
        toolid = res
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == toolid:
                self.ui.tools_table.selectRow(row)
        self.on_row_selection_change()
        return res

    def on_tool_add_from_db_clicked(self):
        """
        Called when the user wants to add a new tool from Tools Database. It will create the Tools Database object
        and display the Tools Database tab in the form needed for the Tool adding
        :return: None
        """

        # if the Tools Database is already opened focus on it
        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.tabText(idx) == _("Tools Database"):
                self.app.ui.plot_tab_area.setCurrentWidget(self.app.tools_db_tab)
                break
        ret_val = self.app.on_tools_database(source=self.key_prefix.split('_', 1)[1])
        if ret_val == 'fail':
            return
        self.app.tools_db_tab.ok_to_add = True
        self.app.tools_db_tab.ui.buttons_frame.hide()
        self.app.tools_db_tab.ui.add_tool_from_db.show()
        self.app.tools_db_tab.ui.cancel_tool_from_db.show()

    # ------------------------------------------------------------------------------------------------------------
    # the preview
    # ------------------------------------------------------------------------------------------------------------
    def clear_preview(self, *args):
        """Remove the preview from the canvas; a job still running will not draw its result."""
        self._preview_token += 1
        if self._preview_drawn and self._preview_shapes is not None:
            self._preview_shapes.clear(update=True)
        self._preview_drawn = False

    def on_source_changed(self, *args):
        """The object to clear changed: its preview and the zones of the last run do not apply any more."""
        self.reset_last_regions()
        self.clear_preview()

    def _shape_store(self, attribute):
        """The shape collection kept in the attribute, created on the first use (None without a canvas)."""
        store = getattr(self, attribute)
        if store is None:
            try:
                store = self.app.plotcanvas.new_shape_collection(layers=1)
            except Exception as e:
                self.app.log.error("%s._shape_store() --> %s" % (type(self).__name__, str(e)))
                return None
            setattr(self, attribute, store)
        return store

    # ------------------------------------------------------------------------------------------------------------
    # the canvas selection and the exclusion zones
    # ------------------------------------------------------------------------------------------------------------
    def start_area_selection(self, exclusion=False):
        """
        Let the user draw zones on the canvas: the area to work on, or the exclusion zones. The panel is disabled
        until the selection ends (right click or Escape).
        """
        self.selector.start('exclusion' if exclusion else 'area',
                            commit=self.on_exclusion_zones if exclusion else self.on_zones_selected)

    def end_selection(self):
        """Leave the selection mode without a result: the canvas and the panel are the app's again."""
        self.selector.cancel()

    def on_exclusion_zones(self, zones):
        self.exclusion_list.extend(zones)
        self.update_exclusions()
        self.app.inform.emit('[success] %s' % _("Exclusion zones added."))

    def on_add_exclusion(self):
        """Let the user draw exclusion zones with the area selection mouse handlers (right click or Escape ends)."""
        if self.selector.active:
            return
        self.clear_preview()
        self.start_area_selection(exclusion=True)

    def on_delete_exclusions(self):
        self.exclusion_list.clear()
        self.update_exclusions()

    def update_exclusions(self):
        """Show the count of the zones and draw them in their own color."""
        self.ui.excl_count_value.setText(str(len(self.exclusion_list)))
        self.clear_preview()
        store = self._shape_store('_exclusion_shapes')
        if store is None:
            return
        store.clear(update=False)
        for poly in self.exclusion_list:
            store.add(poly, color='#CC0000FF', face_color='#CC000040', update=False, layer=0, tolerance=None)
        store.redraw()

    def reset_drawn_regions(self):
        """A Reset or the close of the plugin: no selection, no preview, no exclusion zones, no zones of a last run."""
        self.selector.cancel()
        self.clear_preview()
        self.reset_last_regions()
        self.exclusion_list.clear()
        if self._exclusion_shapes is not None:
            self._exclusion_shapes.clear(update=True)

    def on_plugin_cleanup(self):
        """The plugin is closing: do not leave the canvas in the selection mode, take the shapes off the canvas."""
        self.reset_drawn_regions()

