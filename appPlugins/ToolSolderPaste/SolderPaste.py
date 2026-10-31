# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appTool import AppTool
from defaults import AppDefaults
from appGUI.GUIElements import VerticalScrollArea, GLay, FCComboBox, FCFileSaveDialog, \
    FCComboBox2, FCEntry, FCDoubleSpinner, FCSpinner, FCInputSpinner

import traceback
from copy import deepcopy
from datetime import datetime as dt

import gettext
import appTranslation as fcTranslate
import builtins

from appCommon.Common import LoudDict
from appCommon.solderpaste import parse_numbers, extract_pads, parameters, PREFIX
from appPlugins.ToolSolderPaste.SolderPasteUI import SolderUI
from appPlugins.ToolSolderPaste.SolderPasteGen import pad_geometry, cnc_tools, gcode_header

from camlib import grace
from appEditors.appTextEditor import AppTextEditor

from io import StringIO

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class SolderPaste(AppTool):
    
    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app
        
        # Number of decimals to be used for tools/nozzles in this FlatCAM Tool
        self.decimals = self.app.decimals

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = SolderUI(layout=self.layout, app=self.app, solder_class=self)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        self.app.object_status_changed.connect(self.update_comboboxes)

        self.tooltable_tools = {}
        self.tooluid = 0

        self.obj_options = LoudDict()
        self.form_fields = {}
        self.general_form_fields = {}

        self.units = ''
        self.name = ""

        self.obj = None
        self.text_editor_tab = None

        # this will be used in the combobox context menu, for delete entry
        self.obj_to_be_deleted_name = ''

        # stpre here the flattened geometry
        self.flat_geometry = []

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolSolderPaste()")

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
        self.build_ui()

        self.app.ui.notebook.setTabText(2, _("SolderPaste"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+K', **kwargs)

    def clear_context_menu(self):
        self.ui.tools_table.removeContextMenu()

    def init_context_menu(self):
        self.ui.tools_table.setupContextMenu()
        self.ui.tools_table.addContextMenu(
            _("Add"), lambda: self.on_tool_add(dia=None, muted=None),
            icon=QtGui.QIcon(self.app.resource_location + "/plus16.png"))
        self.ui.tools_table.addContextMenu(
            _("Delete"), lambda:
            self.on_tool_delete(rows_to_delete=None, all_tools=None),
            icon=QtGui.QIcon(self.app.resource_location + "/delete32.png")
        )

    def connect_signals_at_init(self):
        self.ui.combo_context_del_action.triggered.connect(self.on_delete_object)

        self.ui.tools_table.horizontalHeader().sectionClicked.connect(self.on_toggle_all_rows)

        self.ui.addtool_btn.clicked.connect(self.on_tool_add)
        self.ui.addtool_entry.returnPressed.connect(self.on_tool_add)
        self.ui.deltool_btn.clicked.connect(self.on_tool_delete)
        self.ui.soldergeo_btn.clicked.connect(self.on_create_geo_click)
        self.ui.solder_gcode_btn.clicked.connect(self.on_create_gcode_click)
        self.ui.solder_gcode_view_btn.clicked.connect(self.on_view_gcode)
        self.ui.solder_gcode_save_btn.clicked.connect(self.on_save_gcode)

        self.ui.geo_obj_combo.currentIndexChanged.connect(self.on_geo_select)
        self.ui.cnc_obj_combo.currentIndexChanged.connect(self.on_cncjob_select)

        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def on_toggle_all_rows(self):
        """

        :return:
        :rtype:
        """

        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        if len(sel_rows) == self.ui.tools_table.rowCount():
            self.ui.tools_table.clearSelection()
        else:
            self.ui.tools_table.selectAll()

    def set_tool_ui(self):
        self.clear_ui(self.layout)
        self.ui = SolderUI(layout=self.layout, app=self.app, solder_class=self)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.form_fields.update({
            "tools_solderpaste_new":            self.ui.addtool_entry,
            "tools_solderpaste_z_start":        self.ui.z_start_entry,
            "tools_solderpaste_z_dispense":     self.ui.z_dispense_entry,
            "tools_solderpaste_z_stop":         self.ui.z_stop_entry,
            "tools_solderpaste_z_travel":       self.ui.z_travel_entry,
            "tools_solderpaste_margin":         self.ui.margin_entry,
            "tools_solderpaste_z_toolchange":   self.ui.z_toolchange_entry,
            "tools_solderpaste_xy_toolchange":  self.ui.xy_toolchange_entry,
            "tools_solderpaste_frxy":           self.ui.frxy_entry,
            "tools_solderpaste_fr_rapids":      self.ui.fr_rapids_entry,
            "tools_solderpaste_frz":            self.ui.frz_entry,
            "tools_solderpaste_frz_dispense":   self.ui.frz_dispense_entry,
            "tools_solderpaste_speedfwd":       self.ui.speedfwd_entry,
            "tools_solderpaste_dwellfwd":       self.ui.dwellfwd_entry,
            "tools_solderpaste_speedrev":       self.ui.speedrev_entry,
            "tools_solderpaste_dwellrev":       self.ui.dwellrev_entry,
            "tools_solderpaste_pp":             self.ui.pp_combo
        })
        self.form_fields.update(self.ui.calibration.fields)

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        app_option = lambda key: self.app.options.get(
            key, app_defaults.get(key, AppDefaults.factory_defaults.get(key))
        )
        self.set_form_from_defaults()

        self.general_form_fields.update({
            "tools_solderpaste_pp": self.ui.pp_combo
        })
        for name in ('controller', 'drive', 'axis', 'axis_mode', 'volume_mode', 'mach3_dwell',
                     'grbl_mode', 'marlin_cold'):
            self.general_form_fields[PREFIX + name] = self.ui.calibration.fields[PREFIX + name]

        for option in self.app.options:
            if option.find('tools_') == 0:
                self.obj_options[option] = deepcopy(app_option(option))
        self.read_form_to_options()

        self.clear_context_menu()
        self.init_context_menu()

        # either originally it was a string or not, xy_end will be made string
        dias_option = app_option("tools_solderpaste_tools")
        try:
            dias = parse_numbers(dias_option)
            if any(dia <= 0 for dia in dias):
                raise ValueError('Nozzle diameters must be positive.')
        except Exception as err:
            self.app.log.error("SolderPaste.set_tool_ui() -> nozzle dias -> %s" % str(err))
            self.app.log.error("At least one Nozzle tool diameter needed. "
                               "Verify in Edit -> Preferences -> Plugins -> Solder Paste Tools.")
            return

        self.tooluid = 0

        self.tooltable_tools.clear()
        for tool_dia in dias:
            self.tooluid += 1
            self.tooltable_tools.update({
                int(self.tooluid): {
                    'tooldia': float('%.*f' % (self.decimals, tool_dia)),
                    'data': deepcopy(self.obj_options),
                    'solid_geometry': []
                }
            })

        self.name = ""
        self.obj = None

        self.units = self.app.app_units.upper()

        for name in self.app.preprocessors.keys():
            # populate only with preprocessor files that start with 'Paste_'
            if name.partition('_')[0].lower() != 'paste':
                continue
            self.ui.pp_combo.addItem(name)

        self.ui.pp_combo.set_value(app_option("tools_solderpaste_pp"))
        self.obj_options['tools_solderpaste_pp'] = self.ui.pp_combo.get_value()
        for tool in self.tooltable_tools.values():
            tool['data']['tools_solderpaste_pp'] = self.obj_options['tools_solderpaste_pp']

        self.reset_fields()

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.obj_combo.set_value(obj_name)
        else:
            # select first Gerber object found
            for o in self.app.collection.get_list():
                if o.kind == 'gerber':
                    obj_name = o.obj_options['name']
                    self.ui.obj_combo.set_value(obj_name)

    def build_ui(self):
        """
        Will rebuild the UI populating it (tools table)
        :return:
        """
        self.ui_disconnect()

        # updated units
        self.units = self.app.app_units.upper()

        sorted_tools = []
        for k, v in self.tooltable_tools.items():
            sorted_tools.append(float('%.*f' % (self.decimals, float(v['tooldia']))))
        sorted_tools.sort(reverse=True)

        n = len(sorted_tools)
        self.ui.tools_table.setRowCount(n)
        tool_id = 0

        for tool_sorted in sorted_tools:
            for tooluid_key, tooluid_value in self.tooltable_tools.items():
                if float('%.*f' % (self.decimals, tooluid_value['tooldia'])) == tool_sorted:
                    tool_id += 1

                    # Tool name/id
                    id_item = QtWidgets.QTableWidgetItem('%d' % int(tool_id))
                    id_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                    row_no = tool_id - 1
                    self.ui.tools_table.setItem(row_no, 0, id_item)

                    # Diameter
                    dia = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals, tooluid_value['tooldia']))
                    dia.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                    self.ui.tools_table.setItem(row_no, 1, dia)

                    # Tool unique ID
                    tool_uid_item = QtWidgets.QTableWidgetItem(str(int(tooluid_key)))
                    self.ui.tools_table.setItem(row_no, 2, tool_uid_item)

        # make the diameter column editable
        for row in range(tool_id):
            self.ui.tools_table.item(row, 1).setFlags(
                QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsSelectable |
                QtCore.Qt.ItemFlag.ItemIsEnabled)

        # all the tools are selected by default
        self.ui.tools_table.selectColumn(0)
        #
        self.ui.tools_table.resizeColumnsToContents()
        self.ui.tools_table.resizeRowsToContents()

        vertical_header = self.ui.tools_table.verticalHeader()
        vertical_header.hide()
        self.ui.tools_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.tools_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)

        # self.ui.tools_table.setSortingEnabled(True)
        # sort by tool diameter
        # self.ui.tools_table.sortItems(1)

        self.ui.tools_table.setMinimumHeight(self.ui.tools_table.getHeight())
        self.ui.tools_table.setMaximumHeight(self.ui.tools_table.getHeight())

        self.ui_connect()

    def update_ui(self, row=None):
        """
        Will update the UI form with the data from obj.tools

        :param row: the row (tool) from which to extract information's used to populate the form
        :return:
        """
        self.ui_disconnect()

        if row is None:
            try:
                current_row = self.ui.tools_table.currentRow()
            except Exception:
                current_row = 0
        else:
            current_row = row

        if current_row < 0:
            current_row = 0

        # populate the form with the data from the tool associated with the row parameter
        try:
            tooluid = int(self.ui.tools_table.item(current_row, 2).text())
        except Exception as e:
            self.app.log.error("Tool missing. Add a tool in Tool Table. %s" % str(e))
            return

        # update the form
        try:
            # set the form with data from the newly selected tool
            for tooluid_key, tooluid_value in self.tooltable_tools.items():
                if int(tooluid_key) == tooluid:
                    self.set_form(deepcopy(tooluid_value['data']))
        except Exception as e:
            self.app.log.error("ToolSolderPaste ---> update_ui() " + str(e))

        self.ui_connect()

    def on_add_tool_by_key(self):
        tool_add_popup = FCInputSpinner(title='%s...' % _("New Tool"),
                                        text='%s:' % _('Enter a Tool Diameter'),
                                        min=0.0000, max=100.0000, decimals=self.decimals, step=0.1)
        tool_add_popup.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/letter_t_32.png'))

        val, ok = tool_add_popup.get_value()
        if ok:
            if float(val) == 0:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Please enter a tool diameter with non-zero value, in Float format."))
                return
            self.on_tool_add(dia=float(val))
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s...' % _("Adding Tool cancelled"))

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

    def ui_connect(self):
        # on any change to the widgets that matter it will be called self.gui_form_to_storage which will save the
        # changes in geometry UI
        if not hasattr(self, '_storage_slot'):
            self._storage_slot = lambda: self.form_to_storage()
        for grid in self.ui.tools_box.parentWidget().findChildren(GLay):
            assert isinstance(grid, QtWidgets.QGridLayout)
            for i in range(grid.count()):
                wdg = grid.itemAt(i).widget()
                if hasattr(self.ui, 'calibration') and wdg in self.ui.calibration.fields.values():
                    continue
                if isinstance(wdg, (FCComboBox, FCComboBox2)):
                    wdg.currentIndexChanged.connect(self._storage_slot)
                elif isinstance(wdg, FCEntry):
                    wdg.editingFinished.connect(self.form_to_storage)
                elif isinstance(wdg, (FCDoubleSpinner, FCSpinner)):
                    wdg.editingFinished.connect(self.form_to_storage)

        self.ui.tools_table.itemChanged.connect(self.on_tool_edit)
        self.ui.tools_table.itemSelectionChanged.connect(self.on_row_selection_change)
        if hasattr(self.ui, 'calibration'):
            self.ui.calibration.changed.connect(self.form_to_storage)

    def ui_disconnect(self):
        # if connected, disconnect the signal from the slot on item_changed as it creates issues
        for grid in self.ui.tools_box.parentWidget().findChildren(GLay):
            assert isinstance(grid, QtWidgets.QGridLayout)
            for i in range(grid.count()):
                wdg = grid.itemAt(i).widget()
                if hasattr(self.ui, 'calibration') and wdg in self.ui.calibration.fields.values():
                    continue
                if isinstance(wdg, (FCComboBox, FCComboBox2)):
                    try:
                        wdg.currentIndexChanged.disconnect(self._storage_slot)
                    except (TypeError, AttributeError):
                        pass
                elif isinstance(wdg, FCEntry):
                    try:
                        wdg.editingFinished.disconnect(self.form_to_storage)
                    except (TypeError, AttributeError):
                        pass
                elif isinstance(wdg, (FCDoubleSpinner, FCSpinner)):
                    try:
                        wdg.editingFinished.disconnect(self.form_to_storage)
                    except (TypeError, AttributeError):
                        pass

        try:
            self.ui.tools_table.itemChanged.disconnect(self.on_tool_edit)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.tools_table.itemSelectionChanged.disconnect(self.on_row_selection_change)
        except (TypeError, AttributeError):
            pass
        if hasattr(self.ui, 'calibration'):
            try:
                self.ui.calibration.changed.disconnect(self.form_to_storage)
            except (TypeError, AttributeError):
                pass

    def update_comboboxes(self, obj, status):
        """
        Modify the current text of the comboboxes to show the last object
        that was created.

        :param obj: object that was changed and called this PyQt slot
        :param status: what kind of change happened: 'append' or 'delete'
        :return:
        """
        try:
            obj_name = obj.obj_options['name']
        except AttributeError:
            # this happens when the 'delete all' is emitted since in that case the obj is set to None and None has no
            # attribute named 'options'
            return

        if status == 'append':
            idx = self.ui.obj_combo.findText(obj_name)
            if idx != -1:
                self.ui.obj_combo.setCurrentIndex(idx)

            idx = self.ui.geo_obj_combo.findText(obj_name)
            if idx != -1:
                self.ui.geo_obj_combo.setCurrentIndex(idx)

            idx = self.ui.cnc_obj_combo.findText(obj_name)
            if idx != -1:
                self.ui.cnc_obj_combo.setCurrentIndex(idx)

    def read_form_to_options(self):
        """
        Will read all the parameters from Solder Paste Tool UI and update the self.obj_options dictionary
        :return:
        """

        for key in self.form_fields:
            self.obj_options[key] = self.form_fields[key].get_value()

    def form_to_storage(self, tooluid=None):
        """
        Will read all the items in the UI form and set the self.tools data accordingly

        :param tooluid: the uid of the tool to be updated in the obj.tools
        :return:
        """

        current_row = self.ui.tools_table.currentRow()
        if current_row < 0:
            return
        uid = tooluid if tooluid else int(self.ui.tools_table.item(current_row, 2).text())
        if uid < 0:
            return 
        for key in self.form_fields:
            self.tooltable_tools[uid]['data'].update({
                key: self.form_fields[key].get_value()
            })

        # set General Parameters for all tools; always done last
        for key in self.general_form_fields:
            for uid in self.tooltable_tools:
                self.tooltable_tools[uid]['data'].update({
                    key: self.general_form_fields[key].get_value()
                })

    def set_form_from_defaults(self):
        """
        Will read all the parameters of Solder Paste Tool from the app self.defaults and update the UI

        :return:
        """
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        app_option = lambda key: self.app.options.get(
            key, app_defaults.get(key, AppDefaults.factory_defaults.get(key))
        )
        for key in self.form_fields:
            self.form_fields[key].set_value(app_option(key))

    def set_form(self, val):
        """
        Will read all the parameters of Solder Paste Tool from the provided val parameter and update the UI
        :param val: dictionary with values to store in the form
        param_type: dictionary
        :return:
        """

        if not isinstance(val, dict):
            self.app.log.debug("ToolSoderPaste.set_form() --> parameter not a dict")
            return

        for key in self.form_fields:
            if key in val:
                self.form_fields[key].set_value(val[key])
            elif key.startswith(PREFIX) and key in AppDefaults.factory_defaults:
                self.form_fields[key].set_value(AppDefaults.factory_defaults[key])

    def on_tool_add(self, dia=None, muted=None):
        """
        Add a Tool in the Tool Table

        :param dia: diameter of the tool to be added
        :param muted: if True will not send status bar messages about adding tools
        :return:
        """
        self.ui_disconnect()

        if dia:
            tool_dia = dia
        else:
            try:
                tool_dia = float(self.ui.addtool_entry.get_value())
            except ValueError:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    tool_dia = float(self.ui.addtool_entry.get_value().replace(',', '.'))
                except ValueError:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                    return
            if tool_dia is None:
                self.build_ui()
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter to add, in Float format."))
                return

        if tool_dia == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Please enter a tool diameter with non-zero value, in Float format."))
            return

        # construct a list of all 'tooluid' in the self.tooltable_tools
        tool_uid_list = []
        for tooluid_key in self.tooltable_tools:
            tool_uid_item = int(tooluid_key)
            tool_uid_list.append(tool_uid_item)

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        if not tool_uid_list:
            max_uid = 0
        else:
            max_uid = max(tool_uid_list)
        self.tooluid = int(max_uid + 1)

        tool_dias = []
        for k, v in self.tooltable_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(float('%.*f' % (self.decimals, v[tool_v])))

        # if float('%.*f' % (self.decimals, tool_dia)) in tool_dias:
        if self.app.dec_format(tool_dia, self.decimals) in tool_dias:
            if muted is None:
                self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
            self.ui.tools_table.itemChanged.connect(self.on_tool_edit)
            return
        else:
            if muted is None:
                self.app.inform.emit('[success] %s' % _("New tool added to Tool Table."))
            self.tooltable_tools.update({
                int(self.tooluid): {
                    'tooldia':          float('%.*f' % (self.decimals, tool_dia)),
                    'data':             deepcopy(self.obj_options),
                    'solid_geometry':   []
                }
            })

        self.build_ui()

    def on_tool_edit(self):
        """
        Edit a tool in the Tool Table
        :return:
        """
        self.ui_disconnect()

        tool_dias = []
        for k, v in self.tooltable_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(float('%.*f' % (self.decimals, v[tool_v])))

        for row in range(self.ui.tools_table.rowCount()):

            try:
                new_tool_dia = float(self.ui.tools_table.item(row, 1).text())
            except ValueError:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    new_tool_dia = float(self.ui.tools_table.item(row, 1).text().replace(',', '.'))
                except ValueError:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                    return

            tooluid = int(self.ui.tools_table.item(row, 2).text())

            # identify the tool that was edited and get it's tooluid
            if new_tool_dia not in tool_dias:
                self.tooltable_tools[tooluid]['tooldia'] = new_tool_dia
                self.app.inform.emit('[success] %s' % _("Tool from Tool Table was edited."))
                self.build_ui()
                return
            else:
                old_tool_dia = ''
                # identify the old tool_dia and restore the text in tool table
                for k, v in self.tooltable_tools.items():
                    if k == tooluid:
                        old_tool_dia = v['tooldia']
                        break
                restore_dia_item = self.ui.tools_table.item(row, 1)
                restore_dia_item.setText(str(old_tool_dia))
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Already in the Tool Table."))
        self.build_ui()

    def on_tool_delete(self, rows_to_delete=None, all_tools=None):
        """
        Will delete tool(s) in the Tool Table

        :param rows_to_delete:  tell which row (tool) to be deleted
        :param all_tools:       to delete all tools at once
        :return:
        """
        self.ui_disconnect()

        deleted_tools_list = []
        if all_tools:
            self.tooltable_tools.clear()
            self.build_ui()
            return

        if rows_to_delete:
            try:
                for row in rows_to_delete:
                    tooluid_del = int(self.ui.tools_table.item(row, 2).text())
                    deleted_tools_list.append(tooluid_del)
            except TypeError:
                deleted_tools_list.append(rows_to_delete)

            for t in deleted_tools_list:
                self.tooltable_tools.pop(t, None)
            self.build_ui()
            return

        try:
            if self.ui.tools_table.selectedItems():
                for row_sel in self.ui.tools_table.selectedItems():
                    row = row_sel.row()
                    if row < 0:
                        continue
                    tooluid_del = int(self.ui.tools_table.item(row, 2).text())
                    deleted_tools_list.append(tooluid_del)

                for t in deleted_tools_list:
                    self.tooltable_tools.pop(t, None)

        except AttributeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Delete failed. Select a tool to delete."))
            return
        except Exception as e:
            self.app.log.error(str(e))

        self.app.inform.emit('[success] %s' % _("Tools deleted from Tool Table."))
        self.build_ui()

    def on_rmb_combo(self, pos, combo):
        """
        Will create a context menu on the combobox items
        :param pos: mouse click position passed by the signal that called this slot
        :param combo: the actual combo from where the signal was triggered
        :return:
        """
        view = combo.view
        idx = view.indexAt(pos)
        if not idx.isValid():
            return

        self.obj_to_be_deleted_name = combo.model().itemData(idx)[0]

        menu = QtWidgets.QMenu()
        menu.addAction(self.ui.combo_context_del_action)
        menu.exec(view.mapToGlobal(pos))

    def on_delete_object(self):
        """
        Slot for the 'delete' action triggered in the combobox context menu.
        The name of the object to be deleted is collected when the combobox context menu is created.
        :return:
        """
        if self.obj_to_be_deleted_name != '':
            self.app.collection.set_active(self.obj_to_be_deleted_name)
            self.app.collection.delete_active(select_project=False)
            self.obj_to_be_deleted_name = ''

    def on_geo_select(self):
        obj = self.app.collection.get_by_name(self.ui.geo_obj_combo.currentText())
        if obj is None or getattr(obj, 'special_group', None) != 'solder_paste_tool':
            return
        self.ui_disconnect()
        self.tooltable_tools = {uid: {'tooldia': tool['tooldia'], 'data': deepcopy(tool['data']),
                                     'solid_geometry': []} for uid, tool in obj.tools.items()}
        for tool in self.tooltable_tools.values():
            tool['data'].setdefault(PREFIX + 'volume_mode', 'Legacy')
        self.build_ui()
        self.update_ui(row=0)

    def on_cncjob_select(self):
        # if self.cnc_obj_combo.currentText().rpartition('_')[2] == 'solderpaste':
        #     self.save_gcode_frame.setDisabled(False)
        # else:
        #     self.save_gcode_frame.setDisabled(True)
        pass

    def on_create_geo_click(self):
        """
        Will create a solderpaste dispensing geometry.

        :return:
        """
        name = self.ui.obj_combo.currentText()
        if name == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selected object cannot be used."))
            return

        obj = self.app.collection.get_by_name(name)
        # update the self.obj_options
        self.read_form_to_options()
        self.form_to_storage()

        self.on_create_geo(name=name, work_object=obj)

    def on_create_geo(self, name, work_object, use_thread=True):
        """
        The actual work for creating solderpaste dispensing geometry is done here.

        :param name: the outname for the resulting geometry object
        :param work_object: the source Gerber object from which the geometry is created
        :param use_thread: use thread, True or False
        :return: a Geometry type object
        """

        obj = work_object
        tooltable_tools = deepcopy(self.tooltable_tools)
        obj_options = deepcopy(self.obj_options)

        # Sort tools in descending order
        sorted_tools = []
        for k, v in self.tooltable_tools.items():
            # make sure that the tools diameter is more than zero and not zero
            if float(v['tooldia']) > 0:
                sorted_tools.append(float('%.*f' % (self.decimals, float(v['tooldia']))))
        sorted_tools.sort(reverse=True)

        if not sorted_tools:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Tools table is empty."))
            return 'fail'

        tools_geometry = extract_pads(obj)

        if not tools_geometry:
            self.app.log.debug("Failed due of missing Gerber pads geometry.")
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

        def geo_init(geo_obj, app_obj):
            geo_obj.obj_options.update(obj_options)
            geo_obj.solid_geometry = []

            geo_obj.tools = {}
            geo_obj.multigeo = True
            geo_obj.multitool = True
            geo_obj.special_group = 'solder_paste_tool'

            work_geo = tools_geometry
            rest_geo = []
            tooluid = 1

            for tool in sorted_tools:
                for uid, vl in tooltable_tools.items():
                    if float('%.*f' % (self.decimals, float(vl['tooldia']))) == tool:
                        tooluid = int(uid)
                        break

                geo_obj.tools[tooluid] = {}
                geo_obj.tools[tooluid]['tooldia'] = tool
                geo_obj.tools[tooluid]['data'] = deepcopy(tooltable_tools[tooluid]['data'])
                geo_obj.tools[tooluid]['solid_geometry'] = []
                geo_obj.tools[tooluid]['dispense_volumes'] = []
                geo_obj.tools[tooluid]['type'] = 'SolderPaste'

                geo_obj.tools[tooluid]['data']['tools_mill_offset_type'] = 0  # 'Path'
                geo_obj.tools[tooluid]['data']['tools_mill_offset_value'] = 0.0
                geo_obj.tools[tooluid]['data']['tools_mill_job_type'] = 'SP'  # ''
                geo_obj.tools[tooluid]['data']['tools_mill_tool_shape'] = 'DN'  # 'DN'

                data = geo_obj.tools[tooluid]['data']
                calibrated = data.get(PREFIX + 'volume_mode', 'Legacy') != 'Legacy'
                plan_parameters = None
                if calibrated:
                    try:
                        plan_parameters = parameters(data, self.units, for_geometry=True)
                    except ValueError as err:
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % str(err))
                        return 'fail'

                for poly in work_geo:
                    if self.app.abort_flag:
                        raise grace
                    try:
                        paths, volumes = pad_geometry(poly, tool, data, self.units, plan_parameters)
                    except ValueError as err:
                        if not calibrated:
                            raise
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % str(err))
                        return 'fail'
                    if paths:
                        geo_obj.tools[tooluid]['solid_geometry'].extend(paths)
                        geo_obj.tools[tooluid]['dispense_volumes'].extend(volumes)
                    else:
                        rest_geo.append(poly)

                work_geo = rest_geo
                rest_geo = []

                if not work_geo:
                    a = 0
                    for tooluid_key in geo_obj.tools:
                        if not geo_obj.tools[tooluid_key]['solid_geometry']:
                            a += 1
                    if a == len(geo_obj.tools):
                        msg = '[ERROR_NOTCL] %s' % '%s ...' % _('Cancelled.')
                        self.app.inform.emit(msg)
                        return 'fail'

                    app_obj.inform.emit('[success] %s' % _("Done."))
                    return

            # if we still have geometry not processed at the end of the tools then we failed
            # some or all the pads are not covered with solder paste
            if work_geo:
                app_obj.inform.emit('[WARNING_NOTCL] %s' %
                                    _("Some or all pads have no solder "
                                      "due of inadequate nozzle diameters..."))
                return 'fail'

        if use_thread:
            proc = self.app.proc_container.new('%s...' % _("Working"))
            def job_thread(app_obj):
                try:
                    self.new_object("geometry", name + "_solderpaste", geo_init, source=obj)
                except Exception as e:
                    self.app.log.error("SolderPaste.on_create_geo() --> %s" % str(e))
                finally:
                    proc.done()

            self.app.inform.emit(_("Generating Solder Paste dispensing geometry..."))
            # Promise object with the new name
            self.app.collection.promise(name + "_solderpaste")

            # Background
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})
        else:
            with self.app.proc_container.new('%s...' % _("Working")):
                return self.new_object("geometry", name + "_solderpaste", geo_init, source=obj)

    def on_create_gcode_click(self):
        """
        Will create a CNCJob object from the solderpaste dispensing geometry.

        :return:
        """
        name = self.ui.geo_obj_combo.currentText()
        obj = self.app.collection.get_by_name(name)
        self.form_to_storage()

        if name == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Geometry object available."))
            return

        if obj is None or obj.special_group != 'solder_paste_tool':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selected object cannot be used."))
            return

        a = 0
        for tooluid_key in obj.tools:
            if not obj.tools[tooluid_key]['solid_geometry']:
                a += 1
        if a == len(obj.tools):
            self.app.inform.emit('[ERROR_NOTCL] %s...' % _('Cancelled.'))
            return

        # use the name of the first tool selected in self.geo_tools_table which has the diameter passed as tool_dia
        originar_name = obj.obj_options['name'].partition('_')[0]
        outname = "%s_%s" % (originar_name, 'cnc_solderpaste')

        self.on_create_gcode(name=outname, workobject=obj)

    def on_create_gcode(self, name, workobject, use_thread=True):
        """
        Creates a multi-tool CNCJob. The actual work is done here.

        :param name: outname for the resulting CNCJob object
        :param workobject: the solderpaste dispensing Geometry object that is the source
        :param use_thread: True if threaded execution is desired
        :return:
        """
        obj = workobject
        current_tools = deepcopy(self.tooltable_tools)

        try:
            xmin = obj.obj_options['xmin']
            ymin = obj.obj_options['ymin']
            xmax = obj.obj_options['xmax']
            ymax = obj.obj_options['ymax']
        except Exception as e:
            self.app.log.error("SolderPaste.on_create_gcode() --> %s\n" % str(e))
            msg = '[ERROR] %s' % _("An internal error has occurred. See shell.\n")
            msg += 'SolderPaste.on_create_gcode() --> %s' % str(e)
            msg += traceback.format_exc()
            self.app.inform.emit(msg)
            return

        # Object initialization function for app.app_obj.new_object()
        # RUNNING ON SEPARATE THREAD!
        def job_init(new_obj, app_obj):
            assert new_obj.kind == 'cncjob', \
                "Initializer expected a CNCJobObject, got %s" % type(new_obj)

            app_defaults = getattr(app_obj, "defaults", None) or AppDefaults.factory_defaults
            app_option = lambda key: app_obj.options.get(
                key, app_defaults.get(key, AppDefaults.factory_defaults.get(key))
            )

            # this turn on the FlatCAMCNCJob plot for multiple tools
            new_obj.multitool = True
            new_obj.multigeo = True
            # new_obj object is a CNCJob object made from a Geometry object
            new_obj.tools = cnc_tools(obj.tools, current_tools)
            if not new_obj.tools:
                app_obj.inform.emit('[ERROR_NOTCL] %s...' % _('Cancelled.'))
                return 'fail'
            new_obj.special_group = 'solder_paste_tool'

            new_obj.obj_options['xmin'] = xmin
            new_obj.obj_options['ymin'] = ymin
            new_obj.obj_options['xmax'] = xmax
            new_obj.obj_options['ymax'] = ymax

            total_gcode = []
            for index, (tooluid_key, tooluid_value) in enumerate(new_obj.tools.items()):
                # find the tool_dia associated with the tooluid_key
                tool_dia = tooluid_value['tooldia']
                tool_cnc_dict = tooluid_value

                new_obj.coords_decimals = app_option("cncjob_coords_decimals")
                new_obj.fr_decimals = app_option("cncjob_fr_decimals")
                new_obj.tool = int(tooluid_key)

                # Propagate options
                new_obj.obj_options["tooldia"] = tool_dia
                new_obj.obj_options['tool_dia'] = tool_dia

                # ## CREATE GCODE # ##
                is_first = index == 0
                try:
                    res = new_obj.generate_gcode_from_solder_paste_geo(is_first=is_first, **tooluid_value)
                except grace:
                    raise
                except Exception as err:
                    app_obj.inform.emit('[ERROR_NOTCL] %s' % str(err))
                    return 'fail'

                if res == 'fail':
                    app_obj.log.debug("SolderPaste.on_create_gcode() --> generate_gcode_from_solderpaste_geo() failed")
                    return 'fail'
                else:
                    tool_cnc_dict['gcode'] = res
                total_gcode.append(res)

                # ## PARSE GCODE # ##
                tool_cnc_dict['gcode_parsed'] = new_obj.gcode_parse(tool_data=tool_cnc_dict['data'])

                # Bounds and plotting accept paths directly; don't node every travel intersection.
                tool_cnc_dict['solid_geometry'] = [geo['geom'] for geo in tool_cnc_dict['gcode_parsed']]

            used_tools = list(new_obj.tools.keys())
            new_obj.used_tools = used_tools

            new_obj.gcode = ''.join(total_gcode)
            new_obj.source_file = StringIO(new_obj.gcode)

        if use_thread:
            # To be run in separate thread
            def job_thread(app_obj):
                with self.app.proc_container.new('%s...' % _("Working")):
                    if self.new_object("cncjob", name, job_init, source=obj) != 'fail':
                        app_obj.inform.emit('[success] %s: %s' % (_("CNCjob created"), name))
            # Create a promise with the name
            self.app.collection.promise(name)
            # Send to worker
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})
        else:
            self.new_object("cncjob", name, job_init, source=obj)

    def on_view_gcode(self):
        """
        View GCode in the Editor Tab.

        :return:
        """
        time_str = "{:%A, %d %B %Y at %H:%M}".format(dt.now())

        name = self.ui.cnc_obj_combo.currentText()
        obj = self.app.collection.get_by_name(name)

        if not obj:
            return

        try:
            if obj.special_group != 'solder_paste_tool':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selected object cannot be used."))
                return
        except AttributeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selected object cannot be used."))
            return

        self.text_editor_tab = AppTextEditor(app=self.app, plain_text=True, language='gcode')

        # add the tab if it was closed
        self.app.ui.plot_tab_area.addTab(self.text_editor_tab, _("GCode Editor"))
        self.text_editor_tab.setObjectName('solderpaste_gcode_editor_tab')

        # Switch plot_area to CNCJob tab
        self.app.ui.plot_tab_area.setCurrentWidget(self.text_editor_tab)

        gcode = gcode_header(self.app.version, self.app.version_date, name, obj.obj_options['type'],
                             obj.units, time_str, obj.pp_solderpaste_name)

        # obj is a CNCJob object made from an Geometry object
        for tool in obj.tools:
            try:
                # it's text
                gcode += obj.tools[tool]['gcode']
            except TypeError:
                # it's StringIO
                gcode += obj.tools[tool]['gcode'].getvalue()

        # then append the text from GCode to the text editor
        # try:
        #     lines = StringIO(gcode)
        # except Exception as e:
        #     self.app.log.error("ToolSolderpaste.on_view_gcode() --> %s" % str(e))
        #     self.app.inform.emit('[ERROR_NOTCL] %s...' % _("No Gcode in the object"))
        #     return

        try:
            # for line in lines:
            #     proc_line = str(line).strip('\n')
            #     self.text_editor_tab.code_editor.append(proc_line)
            self.text_editor_tab.load_text(gcode, move_to_start=True)
        except Exception as e:
            self.app.log.error('ToolSolderPaste.on_view_gcode() -->%s' % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

    def on_save_gcode(self):
        """
        Save solderpaste dispensing GCode to a file on HDD.

        :return:
        """
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        app_option = lambda key: self.app.options.get(
            key, app_defaults.get(key, AppDefaults.factory_defaults.get(key))
        )
        time_str = "{:%A, %d %B %Y at %H:%M}".format(dt.now())
        name = self.ui.cnc_obj_combo.currentText()
        obj = self.app.collection.get_by_name(name)

        if obj is None or obj.special_group != 'solder_paste_tool':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selected object cannot be used."))
            return

        _filter_ = "G-Code Files (*.nc);;G-Code Files (*.txt);;G-Code Files (*.tap);;G-Code Files (*.cnc);;" \
                   "G-Code Files (*.g-code);;All Files (*.*);;G-Code Files (*.gcode);;G-Code Files (*.ngc)"

        try:
            dir_file_to_save = self.app.get_last_save_folder() + '/' + str(name)
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export GCode ..."),
                directory=dir_file_to_save,
                ext_filter=_filter_
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                ext_filter=_filter_)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Export cancelled ..."))
            return

        gcode = gcode_header(self.app.version, self.app.version_date, name, obj.obj_options['type'],
                             obj.units, time_str, obj.pp_solderpaste_name)

        # for CNCJob objects made from Gerber or Geometry objects
        for tool in obj.tools:
            gcode += obj.tools[tool]['gcode']
        lines = StringIO(gcode)

        # ## Write
        if filename is not None:
            try:
                with open(filename, 'w') as f:
                    for line in lines:
                        f.write(line)
            except FileNotFoundError:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No such file or directory"))
                return
            except PermissionError:
                self.app.inform.emit('[WARNING] %s' %
                                     _("Permission denied, saving not possible.\n"
                                       "Most likely another app is holding the file open and not accessible."))
                return 'fail'

        if app_option("global_open_style") is False:
            self.app.file_opened.emit("gcode", filename)
        self.app.file_saved.emit("gcode", filename)
        self.app.inform.emit('[success] %s: %s' % (_("Saved to"), filename))

    def reset_fields(self):
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.geo_obj_combo.setRootModelIndex(self.app.collection.index(2, 0, QtCore.QModelIndex()))
        self.ui.cnc_obj_combo.setRootModelIndex(self.app.collection.index(3, 0, QtCore.QModelIndex()))


