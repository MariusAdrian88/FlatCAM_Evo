# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File modified by: Marius Stanciu                         #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui

from appParsers.ParseExcellon import Excellon
from appObjects.AppObjectTemplate import FlatCAMObj, OffsetUiMixin, PropertiesPanelMixin, ObjectDeleted
from appGUI.GUIElements import FCCheckBox
from appGUI.ObjectUI import ExcellonObjectUI

import itertools
import math
import numpy as np
from copy import deepcopy

import shapely
from shapely import LineString

from camlib import object_circle_steps

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def dedupe_joined_holes(tools, sources, decimals):
    """
    Check the holes of the tools of a join: drills (and slots) of different source objects.

    An exact duplicate (same center, or same start and stop, after rounding to decimals) with the same diameter is
    removed from the tools (the first one stays). Other conflicts are kept and returned: a duplicate with another
    diameter and a drill that overlaps a drill of another source (center distance < r1 + r2, tangent is allowed).
    The drills are searched in a grid of the size of the largest diameter, so the time grows linearly.

    :param tools:       {tool id: tool dict with 'tooldia', 'drills' (Points) and 'slots' ((Point, Point))}, changed
    :param sources:     {tool id: index of the source object of the tool}
    :param decimals:    the decimals of the diameters and of the coordinates
    :return:            (number of removed duplicates, [(x, y)] of the kept conflicts, drills before slots)
    """
    removed = 0
    seen = {}
    drills = []     # (x, y, radius, source)
    slot_conflicts = []
    slot_dias = {}

    for tid, tool in tools.items():
        src = sources.get(tid)
        dia = round(float(tool.get('tooldia', 0.0)), decimals)

        # drills: keep a drill unless a kept one of another source has the same center and diameter
        kept = []
        for drill in tool.get('drills') or []:
            x, y = drill.x, drill.y
            key = ('d', round(x, decimals), round(y, decimals), dia)
            if any(s != src for s in seen.get(key, ())):
                removed += 1
                continue
            seen.setdefault(key, []).append(src)
            kept.append(drill)
            drills.append((x, y, float(tool['tooldia']) / 2.0, src))
        if 'drills' in tool:
            tool['drills'] = kept

        kept = []
        for slot in tool.get('slots') or []:
            ends = sorted(((round(p.x, decimals), round(p.y, decimals)) for p in slot))
            key = ('s', ends[0], ends[1], dia)
            if any(s != src for s in seen.get(key, ())):
                removed += 1
                continue
            seen.setdefault(key, []).append(src)
            kept.append(slot)
            # the same slot with another diameter
            if any(d != dia and s != src for d, s in slot_dias.get(key[:3], ())):
                slot_conflicts.append((slot[0].x, slot[0].y))
            slot_dias.setdefault(key[:3], []).append((dia, src))
        if 'slots' in tool:
            tool['slots'] = kept

    conflicts = []
    if len(drills) > 1:
        cell = 2.0 * max(d[2] for d in drills)
        if cell > 0:
            tol = 10.0 ** -decimals
            grid = {}
            for i, (x, y, r, src) in enumerate(drills):
                cx, cy = int(x // cell), int(y // cell)
                hit = False
                for gx in (cx - 1, cx, cx + 1):
                    for gy in (cy - 1, cy, cy + 1):
                        for j in grid.get((gx, gy), ()):
                            ox, oy, orad, osrc = drills[j]
                            if osrc != src and math.hypot(x - ox, y - oy) < r + orad - tol:
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        break
                if hit:
                    conflicts.append((x, y))
                grid.setdefault((cx, cy), []).append(i)

    return removed, conflicts + slot_conflicts


class ExcellonObject(OffsetUiMixin, PropertiesPanelMixin, FlatCAMObj, Excellon):
    """
    Represents Excellon/Drill code. An object stored in the FlatCAM objects collection (a dict)
    """
    # Dispatch metadata, read by AppObject, ObjectCollection and appPlotManager instead of kind chains.
    # status_color: color of the object name in the status bar message (None: no message).
    status_color = 'brown'
    source_exporter = 'export_excellon'
    # tool_option_prefix: extra application options (with this prefix) copied into obj_options on creation.
    tool_option_prefix = 'tools_drill_'
    # inherits_mill_options: the tools_mill_* and tools_* options are copied into obj_options on creation.
    inherits_mill_options = True
    # has_plot_colors: the object has user settable fill / outline plot colors.
    has_plot_colors = True
    # plot_default_fill_option / plot_default_line_option: option keys of the colors restored by "Default".
    plot_default_fill_option = 'excellon_plot_fill'
    plot_default_line_option = 'excellon_plot_line'
    # alpha_fill_option: option key whose color alpha is the default alpha level.
    # default_alpha_level: fixed default alpha level when alpha_fill_option is None.
    alpha_fill_option = 'excellon_plot_fill'
    default_alpha_level = None


    ui_type = ExcellonObjectUI
    multicolored_build_sig = QtCore.pyqtSignal()

    def __init__(self, name, app):
        self.app = app
        self.decimals = self.app.decimals

        # the circles of the holes follow the Excellon preferences (adaptive to the tool diameter, or fixed)
        Excellon.__init__(self, app=app)
        FlatCAMObj.__init__(self, name, app)

        self.kind = "excellon"

        self.obj_options.update({
            "plot": True,
            "solid": False,
            "multicolored": False,
            "merge_fuse_tools": True,

            "drill_tooldia": 0.1,
            "slot_tooldia": 0.1,

            "format_upper_in": 2,
            "format_lower_in": 4,
            "format_upper_mm": 3,
            "lower_mm": 3,
            "zeros": "T",
            "units": "INCH",
            "update": True,

            "optimization_type": "B",
            "search_time": 3
        })

        # TODO: Document this.
        self.tool_cbs = {}

        # dict that holds the object names and the option name
        # the key is the object name (defines in ObjectUI) for each UI element that is a parameter
        # particular for a tool and the value is the actual name of the option that the UI element is changing
        self.name2option = {}

        # default set of data to be added to each tool in self.tools as self.tools[tool]['data'] = self.default_data
        self.default_data = {}

        # variable to store the total amount of drills per job
        self.tot_drill_cnt = 0
        self.tool_row = 0

        # variable to store the total amount of slots per job
        self.tot_slot_cnt = 0
        self.tool_row_slots = 0

        # variable to store the distance travelled
        self.travel_distance = 0.0

        # store the source file here
        self.source_file = ""

        self.multigeo = False
        self.units_found = self.app.app_units

        self.fill_color = self._app_option('excellon_plot_fill')
        self.outline_color = self._app_option('excellon_plot_line')
        self.alpha_level = 'bf'

        # the key is the tool id and the value is a list of shapes keys (indexes)
        self.shape_indexes_dict = {}

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from predecessors.
        self.ser_attrs = ['obj_options', 'kind', 'fill_color', 'outline_color', 'alpha_level'] + self.ser_attrs

    def copy_from(self, src):
        """
        Make this (new, empty) Excellon a copy of ``src``. Used by both copy paths of AppObjectOps. Everything is
        deep-copied, nothing is shared with ``src``.

        Copied: tools (drills, slots, per-tool solid_geometry and the per-tool 'data', so 2 tools of the same
        diameter with different data stay different), solid_geometry (rebuilt with create_geometry() only if the
        source has none), source_file, units, and the file format attributes that are serialized with the project:
        zeros, excellon_format_upper/lower_mm/_in and excellon_units.

        Not copied, on purpose: obj_options (the new object takes the current app defaults) and the UI / plot
        state. ``on_copy_object2`` then replaces source_file with a freshly exported text under the new name.

        The 2 old paths copied only tools and source_file and rebuilt the geometry with create_geometry(); they
        lost the format attributes. The caller keeps its own check (fail on an object without tools).

        :param src: the ExcellonObject to copy
        :return:    None
        """
        self.tools = deepcopy(src.tools)
        self.solid_geometry = deepcopy(src.solid_geometry)
        self.source_file = deepcopy(getattr(src, 'source_file', self.source_file))
        self.units = getattr(src, 'units', self.units)
        self.zeros = getattr(src, 'zeros', self.zeros)
        self.excellon_format_upper_mm = getattr(src, 'excellon_format_upper_mm', self.excellon_format_upper_mm)
        self.excellon_format_lower_mm = getattr(src, 'excellon_format_lower_mm', self.excellon_format_lower_mm)
        self.excellon_format_upper_in = getattr(src, 'excellon_format_upper_in', self.excellon_format_upper_in)
        self.excellon_format_lower_in = getattr(src, 'excellon_format_lower_in', self.excellon_format_lower_in)
        self.excellon_units = getattr(src, 'excellon_units', self.excellon_units)
        if self.tools and not self.solid_geometry:
            self.create_geometry()

    def set_ui(self, ui):
        """
        Configures the user interface for this object.
        Connects options to form fields.

        :param ui:  User interface object.
        :type ui:   ExcellonObjectUI
        :return:    None
        """
        FlatCAMObj.set_ui(self, ui)

        self.app.log.debug("ExcellonObject.set_ui()")

        self.units = self.app.app_units.upper()

        # # fill in self.obj_options values  for the Drilling Tool from self.app.options
        # for opt_key, opt_val in self.app.options.items():
        #     if opt_key.find('tools_drill_') == 0:
        #         self.obj_options[opt_key] = deepcopy(opt_val)
        #
        # # fill in self.default_data values from self.obj_options
        # for opt_key, opt_val in self.app.options.items():
        #     if opt_key.find('excellon_') == 0 or opt_key.find('tools_drill_') == 0:
        #         self.default_data[opt_key] = deepcopy(opt_val)

        self.form_fields.update({
            "plot":             self.ui.plot_cb,
            "solid":            self.ui.solid_cb,
            "multicolored":     self.ui.multicolored_cb,

            "autoload_db":      self.ui.autoload_db_cb,
            "drill_tooldia":          self.ui.tooldia_entry,
            "slot_tooldia":     self.ui.slot_tooldia_entry,
        })

        self.to_form()

        assert isinstance(self.ui, ExcellonObjectUI), \
            "Expected a ExcellonObjectUI, got %s" % type(self.ui)

        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.plot_cb.stateChanged.connect(self.on_plot_cb_click)
        self.ui.solid_cb.stateChanged.connect(self.on_solid_cb_click)
        self.ui.multicolored_cb.stateChanged.connect(self.on_multicolored_cb_click)
        self.multicolored_build_sig.connect(self.on_multicolored_build)

        self.ui.autoload_db_cb.stateChanged.connect(self.on_autoload_db_toggled)

        # Editor
        self.ui.editor_button.clicked.connect(lambda: self.app.on_editing_start())

        # Properties
        self.ui.info_button.toggled.connect(self.on_properties)
        self.calculations_finished.connect(self.update_area_chull)
        self.ui.treeWidget.itemExpanded.connect(self.on_properties_expanded)
        self.ui.treeWidget.itemCollapsed.connect(self.on_properties_expanded)

        self.ui.drill_button.clicked.connect(lambda: self.app.drilling_tool.run(toggle=True))
        self.ui.milling_button.clicked.connect(self.on_milling_button_clicked)

        # UTILITIES
        self.ui.util_button.clicked.connect(lambda st: self.ui.util_frame.show() if st else self.ui.util_frame.hide())
        self.ui.generate_milling_button.clicked.connect(self.on_generate_milling_button_click)
        self.ui.generate_milling_slots_button.clicked.connect(self.on_generate_milling_slots_button_click)

        # Toggle all Table rows
        self.ui.tools_table.horizontalHeader().sectionClicked.connect(self.on_toggle_rows)

        self.ui.table_visibility_cb.stateChanged.connect(self.on_table_visibility_toggle)

        self.units_found = self.app.app_units

        self.set_offset_values()

        self.clear_contex_menu()
        self.init_context_menu()

        # Show/Hide Advanced Options
        app_mode = self._app_option("global_app_level")
        self.change_level(app_mode)

    def on_level_changed(self, checked):
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: green;
                                                }
                                                """)

            self.ui.tools_table.setColumnHidden(4, True)
            self.ui.tools_table.setColumnHidden(5, True)
            self.ui.table_visibility_cb.set_value(True)
            self.ui.table_visibility_cb.hide()
            self.ui.autoload_db_cb.hide()

            # Context Menu section
            self.ui.tools_table.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: red;
                                                }
                                                """)

            self.ui.tools_table.setColumnHidden(4, False)
            self.ui.tools_table.setColumnHidden(5, False)
            self.ui.table_visibility_cb.show()
            self.ui.table_visibility_cb.set_value(self._app_option("excellon_tools_table_display"))
            self.on_table_visibility_toggle(state=self._app_option("excellon_tools_table_display"))
            self.ui.autoload_db_cb.show()

            # Context Menu section
            self.ui.tools_table.setupContextMenu()

    def build_ui(self):
        """
        Will (re)build the Excellon UI updating it (the tool table)

        :return:    None
        :rtype:
        """
        FlatCAMObj.build_ui(self)

        self.units = self.app.app_units.upper()

        for row in range(self.ui.tools_table.rowCount()):
            try:
                # if connected, disconnect the signal from the slot on item_changed as it creates issues
                offset_spin_widget = self.ui.tools_table.cellWidget(row, 4)
                offset_spin_widget.valueChanged.disconnect()
            except (TypeError, AttributeError):
                pass

        n = len(self.tools)
        # we have (n+2) rows because there are 'n' tools, each a row, plus the last 2 rows for totals.
        self.ui.tools_table.setRowCount(n + 2)

        self.tot_drill_cnt = 0
        self.tot_slot_cnt = 0

        self.tool_row = 0

        sort = []
        for k, v in list(self.tools.items()):
            try:
                sort.append((k, v['tooldia']))
            except KeyError:
                # for old projects to be opened
                sort.append((k, v['C']))

        sorted_tools = sorted(sort, key=lambda t1: t1[1])
        tools = [i[0] for i in sorted_tools]

        new_options = {}
        for opt in self.obj_options:
            new_options[opt] = self.obj_options[opt]

        for tool_no in tools:
            try:
                dia_val = self.tools[tool_no]['tooldia']
            except KeyError:
                # for old projects to be opened
                dia_val = self.tools[tool_no]['C']

            # add the data dictionary for each tool with the default values; the existing per-tool values are kept
            data = deepcopy(new_options)
            data.update(self.tools[tool_no].get('data') or {})
            self.tools[tool_no]['data'] = data

            # drill_cnt = 0  # variable to store the nr of drills per tool
            # slot_cnt = 0  # variable to store the nr of slots per tool

            # Find no of drills for the current tool
            try:
                drill_cnt = len(self.tools[tool_no]['drills'])
            except KeyError:
                drill_cnt = 0
            self.tot_drill_cnt += drill_cnt

            # Find no of slots for the current tool
            try:
                slot_cnt = len(self.tools[tool_no]['slots'])
            except KeyError:
                slot_cnt = 0
            self.tot_slot_cnt += slot_cnt

            # Tool ID
            exc_id_item = QtWidgets.QTableWidgetItem('%d' % int(tool_no))
            exc_id_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table.setItem(self.tool_row, 0, exc_id_item)  # Tool name/id

            # Diameter
            dia_item = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals, dia_val))
            dia_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table.setItem(self.tool_row, 1, dia_item)  # Diameter

            # Drill count
            drill_count_item = QtWidgets.QTableWidgetItem('%d' % drill_cnt)
            drill_count_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table.setItem(self.tool_row, 2, drill_count_item)  # Number of drills per tool

            # Slot Count
            # if the slot number is zero is better to not clutter the GUI with zero's so we print a space
            slot_count_str = '%d' % slot_cnt if slot_cnt > 0 else ''
            slot_count_item = QtWidgets.QTableWidgetItem(slot_count_str)
            slot_count_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table.setItem(self.tool_row, 3, slot_count_item)  # Number of drills per tool

            # Empty Plot Item
            empty_plot_item = QtWidgets.QTableWidgetItem('')
            empty_plot_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.ui.tools_table.setItem(self.tool_row, 4, empty_plot_item)

            if 'multicolor' in self.tools[tool_no] and self.tools[tool_no]['multicolor'] is not None:
                red = int(self.tools[tool_no]['multicolor'][0] * 255)
                green = int(self.tools[tool_no]['multicolor'][1] * 255)
                blue = int(self.tools[tool_no]['multicolor'][2] * 255)
                alpha = int(self.tools[tool_no]['multicolor'][3] * 255)
                h_color = QtGui.QColor(red, green, blue, alpha)
                self.ui.tools_table.item(self.tool_row, 4).setBackground(h_color)
            else:
                h1 = self._app_option("excellon_plot_fill")[1:7]
                h2 = self._app_option("excellon_plot_fill")[7:9]
                h_color = QtGui.QColor('#' + h2 + h1)
                self.ui.tools_table.item(self.tool_row, 4).setBackground(h_color)

            # Plot Item
            plot_item = FCCheckBox()
            plot_item.setLayoutDirection(QtCore.Qt.LayoutDirection.RightToLeft)
            if self.ui.plot_cb.isChecked():
                plot_item.setChecked(True)
            self.ui.tools_table.setCellWidget(self.tool_row, 5, plot_item)

            self.tool_row += 1

        # add a last row with the Total number of drills
        empty_1 = QtWidgets.QTableWidgetItem('')
        empty_1.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_1_1 = QtWidgets.QTableWidgetItem('')
        empty_1_1.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_1_2 = QtWidgets.QTableWidgetItem('')
        empty_1_2.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_1_3 = QtWidgets.QTableWidgetItem('')
        empty_1_3.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_1_4 = QtWidgets.QTableWidgetItem('')
        empty_1_4.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)

        label_tot_drill_count = QtWidgets.QTableWidgetItem(_('Total Drills'))
        tot_drill_count = QtWidgets.QTableWidgetItem('%d' % self.tot_drill_cnt)
        label_tot_drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        tot_drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table.setItem(self.tool_row, 0, empty_1)
        self.ui.tools_table.setItem(self.tool_row, 1, label_tot_drill_count)
        self.ui.tools_table.setItem(self.tool_row, 2, tot_drill_count)  # Total number of drills
        self.ui.tools_table.setItem(self.tool_row, 3, empty_1_1)
        self.ui.tools_table.setItem(self.tool_row, 4, empty_1_2)
        self.ui.tools_table.setItem(self.tool_row, 5, empty_1_3)

        font = QtGui.QFont()
        font.setBold(True)
        # font.setWeight(75)

        for k in [1, 2]:
            self.ui.tools_table.item(self.tool_row, k).setForeground(QtGui.QColor(127, 0, 255))
            self.ui.tools_table.item(self.tool_row, k).setFont(font)

        self.tool_row += 1

        # add a last row with the Total number of slots
        empty_2 = QtWidgets.QTableWidgetItem('')
        empty_2.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_2_1 = QtWidgets.QTableWidgetItem('')
        empty_2_1.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_2_2 = QtWidgets.QTableWidgetItem('')
        empty_2_2.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_2_3 = QtWidgets.QTableWidgetItem('')
        empty_2_3.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        empty_2_4 = QtWidgets.QTableWidgetItem('')
        empty_2_4.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)

        label_tot_slot_count = QtWidgets.QTableWidgetItem(_('Total Slots'))
        tot_slot_count = QtWidgets.QTableWidgetItem('%d' % self.tot_slot_cnt)
        label_tot_slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        tot_slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table.setItem(self.tool_row, 0, empty_2)
        self.ui.tools_table.setItem(self.tool_row, 1, label_tot_slot_count)
        self.ui.tools_table.setItem(self.tool_row, 2, empty_2_1)
        self.ui.tools_table.setItem(self.tool_row, 3, tot_slot_count)  # Total number of slots
        self.ui.tools_table.setItem(self.tool_row, 4, empty_2_3)
        self.ui.tools_table.setItem(self.tool_row, 5, empty_2_4)

        for kl in [1, 2, 3]:
            self.ui.tools_table.item(self.tool_row, kl).setFont(font)
            self.ui.tools_table.item(self.tool_row, kl).setForeground(QtGui.QColor(0, 70, 255))

        # sort the tool diameter column
        # self.ui.tools_table.sortItems(1)

        # all the tools are selected by default
        self.ui.tools_table.selectColumn(0)

        self.ui.tools_table.resizeColumnsToContents()
        self.ui.tools_table.resizeRowsToContents()

        vertical_header = self.ui.tools_table.verticalHeader()
        # vertical_header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        vertical_header.hide()
        self.ui.tools_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.tools_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)

        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)

        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(4, 17)
        horizontal_header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(5, 17)
        self.ui.tools_table.setColumnWidth(5, 17)

        # horizontal_header.setStretchLastSection(True)
        # horizontal_header.setColumnWidth(2, QtWidgets.QHeaderView.ResizeToContents)

        # horizontal_header.setStretchLastSection(True)
        self.ui.tools_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.ui.tools_table.setSortingEnabled(False)

        self.ui.tools_table.setMinimumHeight(self.ui.tools_table.getHeight())
        self.ui.tools_table.setMaximumHeight(self.ui.tools_table.getHeight())

        # find if we have drills:
        has_drills = None
        for tt in self.tools:
            if 'drills' in self.tools[tt] and self.tools[tt]['drills']:
                has_drills = True
                break
        if has_drills is None:
            self.ui.tooldia_entry.setDisabled(True)
            self.ui.generate_milling_button.setDisabled(True)
        else:
            self.ui.tooldia_entry.setDisabled(False)
            self.ui.generate_milling_button.setDisabled(False)

        # find if we have slots
        has_slots = None
        for tt in self.tools:
            if 'slots' in self.tools[tt] and self.tools[tt]['slots']:
                has_slots = True
                break
        if has_slots is None:
            self.ui.slot_tooldia_entry.setDisabled(True)
            self.ui.generate_milling_slots_button.setDisabled(True)
        else:
            self.ui.slot_tooldia_entry.setDisabled(False)
            self.ui.generate_milling_slots_button.setDisabled(False)

        # update the milling section; it ends with ui_connect()
        self.on_row_selection_change()

    def clear_contex_menu(self):
        self.ui.tools_table.removeContextMenu()

    def init_context_menu(self):
        # #############################################################################
        # ###################### Setup CONTEXT MENU ###################################
        # #############################################################################
        self.ui.tools_table.setupContextMenu()
        self.ui.tools_table.addContextMenu(
            _("Copy"),
            lambda: self.on_table_copy_dia(),
            icon=QtGui.QIcon(self.app.resource_location + "/copy32.png")
        )

    def on_table_copy_dia(self):
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too but I'm interested only on rows
        # so the duplicate rows will not be added
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        sel_rows_list = list(sel_rows)
        if len(sel_rows_list) == 1:
            copied_dia_text = self.ui.tools_table.item(sel_rows_list[0], 1).text()
            try:
                # only rows that have float values are allowed
                copied_dia = float(copied_dia_text)
            except ValueError:
                return
        else:
            copied_dia = self.ui.tools_table.item(sel_rows_list[0], 1).text()
            for row_idx in sel_rows_list[1:]:
                copied_dia_text = self.ui.tools_table.item(row_idx, 1).text()
                try:
                    # this is done to avoid selected rows that do not have real numbers in the tool diameter column
                    copied_test_dia = float(copied_dia_text)
                    copied_dia += ','
                    copied_dia += str(copied_test_dia)
                except ValueError:
                    continue

        self.app.clipboard.setText(str(copied_dia))
        self.app.inform.emit('[success] %s' % _("Copied to clipboard."))

    def ui_connect(self):
        """
        Will connect all signals in the Excellon UI that needs to be connected

        :return:    None
        :rtype:
        """

        # selective plotting
        for row in range(self.ui.tools_table.rowCount() - 2):
            self.ui.tools_table.cellWidget(row, 5).clicked.connect(self.on_plot_cb_click_table)
        self.ui.plot_cb.stateChanged.connect(self.on_plot_cb_click)

        # rows selected
        self.ui.tools_table.clicked.connect(self.on_row_selection_change)

    def ui_disconnect(self):
        """
        Will disconnect all signals in the Excellon UI that needs to be disconnected

        :return:    None
        :rtype:
        """
        # selective plotting
        for row in range(self.ui.tools_table.rowCount()):
            try:
                self.ui.tools_table.cellWidget(row, 5).clicked.disconnect()
            except (TypeError, AttributeError):
                pass
        try:
            self.ui.plot_cb.stateChanged.disconnect()
        except (TypeError, AttributeError):
            pass

        # rows selected
        try:
            self.ui.tools_table.clicked.disconnect()
        except (TypeError, AttributeError):
            pass

    def on_row_selection_change(self):
        """
        Called when the user clicks on a row in Tools Table

        :return:    None
        :rtype:
        """
        self.ui_disconnect()

        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        if not sel_rows:
            self.ui.tooldia_entry.setDisabled(True)
            self.ui.generate_milling_button.setDisabled(True)
            self.ui.slot_tooldia_entry.setDisabled(True)
            self.ui.generate_milling_slots_button.setDisabled(True)
            self.ui_connect()
            return
        else:
            self.ui.tooldia_entry.setDisabled(False)
            self.ui.generate_milling_button.setDisabled(False)
            self.ui.slot_tooldia_entry.setDisabled(False)
            self.ui.generate_milling_slots_button.setDisabled(False)

            has_drills = None
            has_slots = None
            for row in sel_rows:
                row_dia = self.app.dec_format(float(self.ui.tools_table.item(row, 1).text()), self.decimals)

                for tt in self.tools:
                    tool_dia = self.app.dec_format(float(self.tools[tt]['tooldia']), self.decimals)
                    if tool_dia == row_dia:
                        # find if we have drills:
                        if 'drills' in self.tools[tt] and self.tools[tt]['drills']:
                            has_drills = True

                        # find if we have slots
                        if 'slots' in self.tools[tt] and self.tools[tt]['slots']:
                            has_slots = True

            if has_drills is None:
                self.ui.tooldia_entry.setDisabled(True)
                self.ui.generate_milling_button.setDisabled(True)
            else:
                self.ui.tooldia_entry.setDisabled(False)
                self.ui.generate_milling_button.setDisabled(False)

            if has_slots is None:
                self.ui.slot_tooldia_entry.setDisabled(True)
                self.ui.generate_milling_slots_button.setDisabled(True)
            else:
                self.ui.slot_tooldia_entry.setDisabled(False)
                self.ui.generate_milling_slots_button.setDisabled(False)

        self.ui_connect()

    def on_toggle_rows(self):
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        # subtract the last 2 rows that show the total and are always displayed but not selected
        if len(sel_rows) == self.ui.tools_table.rowCount() - 2:
            self.ui.tools_table.clearSelection()
        else:
            self.ui.tools_table.selectAll()

        self.on_row_selection_change()

    def get_selected_tools_list(self):
        """
        Returns the keys to the self.tools dictionary corresponding
        to the selections on the tool list in the appGUI.

        :return:    List of tools.
        :rtype:     list
        """
        rows = set()
        for item in self.ui.tools_table.selectedItems():
            rows.add(item.row())

        tool_ids = []
        for row in rows:
            tool_ids.append(int(self.ui.tools_table.item(row, 0).text()))
        return tool_ids
        # return [x.text() for x in self.ui.tools_table.selectedItems()]

    def get_selected_tools_table_items(self):
        """
        Returns a list of lists, each list in the list is made out of row elements

        :return:    List of table_tools items.
        :rtype:     list
        """
        table_tools_items = []
        for x in self.ui.tools_table.selectedItems():
            # from the columnCount we subtract a value of 1 which represent the last column (plot column)
            # which does not have text
            txt = ''
            elem = []

            for column in range(0, self.ui.tools_table.columnCount() - 1):
                try:
                    txt = self.ui.tools_table.item(x.row(), column).text()
                except AttributeError:
                    try:
                        txt = self.ui.tools_table.cellWidget(x.row(), column).currentText()
                    except AttributeError:
                        pass
                elem.append(txt)
            table_tools_items.append(deepcopy(elem))
            # table_tools_items.append([self.ui.tools_table.item(x.row(), column).text()
            #                           for column in range(0, self.ui.tools_table.columnCount() - 1)])
        for item in table_tools_items:
            item[0] = str(item[0])
        return table_tools_items

    def on_table_visibility_toggle(self, state):
        self.ui.tools_table.show() if state else self.ui.tools_table.hide()

    def on_milling_button_clicked(self):
        self.app.milling_tool.run(toggle=True)

    def export_excellon(self, whole, fract, e_zeros=None, form='dec', factor=1, slot_type='routing'):
        """
        Returns two values, first is a boolean , if 1 then the file has slots and second contain the Excellon code

        :param whole:       Integer part digits
        :type whole:        int
        :param fract:       Fractional part digits
        :type fract:        int
        :param e_zeros:     Excellon zeros suppression: LZ or TZ
        :type e_zeros:      str
        :param form:        Excellon format: 'dec',
        :type form:         str
        :param factor:      Conversion factor
        :type factor:       float
        :param slot_type:   How to treat slots: "routing" or "drilling"
        :type slot_type:    str
        :return:            A tuple: (has_slots, Excellon_code) -> (bool, str)
        :rtype:             tuple
        """

        excellon_parts = []

        # 'ndec' formats a coordinate without the decimal point, by the Excellon CNC-7 rules. The sign stays in front.
        # LZ: the leading zeros are written and the reader adds the trailing zeros, so the whole part is
        # padded to 'whole' digits (the forced '+' sign makes the padding width the same for both signs).
        # TZ: the trailing zeros are written and the reader counts 'fract' decimals from the right, so there
        # is no padding (a trailing padding would multiply the value).
        # All the lines of a tool are formatted with one '%' operation; the dot and the '+' sign are removed after.
        if form != 'dec' and e_zeros == 'LZ':
            num_fmt = '%%+0%d.%df' % (whole + 1 + (fract + 1 if fract > 0 else 0), fract)
        else:
            num_fmt = '%%.%df' % fract

        def lines_code(line_fmt, coords):
            # coords: one row of values for each line, in the order of the line_fmt fields
            if len(coords) == 0:
                return ''
            text = (line_fmt * len(coords)) % tuple((coords * factor).ravel().tolist())
            if form == 'dec':
                return text
            text = text.replace('.', '')
            return text.replace('+', '') if e_zeros == 'LZ' else text

        def point_coords(points):
            coords = shapely.get_coordinates(points)
            if len(coords) != len(points):
                raise ValueError('only Point geometry can be exported')
            return coords

        drill_fmt = 'X%sY%s\n' % (num_fmt, num_fmt)
        if slot_type == 'routing':
            slot_fmt = 'G00X%sY%s\nM15\nG01X%sY%s\nM16\n' % (num_fmt, num_fmt, num_fmt, num_fmt)
        elif slot_type == 'drilling':
            slot_fmt = 'X%sY%sG85X%sY%s\nG05\n' % (num_fmt, num_fmt, num_fmt, num_fmt)
        else:
            slot_fmt = None

        # store here if the file has slots, return 1 if any slots, 0 if only drills
        slots_in_file = 0

        # find if we have drills:
        has_drills = None
        for tt in self.tools:
            if 'drills' in self.tools[tt] and self.tools[tt]['drills']:
                has_drills = True
                break
        # find if we have slots:
        has_slots = None
        for tt in self.tools:
            if 'slots' in self.tools[tt] and self.tools[tt]['slots']:
                has_slots = True
                slots_in_file = 1
                break

        # drills processing
        if has_drills:
            for tool in self.tools:
                excellon_parts.append('T0%s\n' % str(tool) if int(tool) < 10 else 'T%s\n' % str(tool))

                try:
                    excellon_parts.append(lines_code(drill_fmt, point_coords(self.tools[tool]['drills'])))
                except Exception as e:
                    self.app.log.error('ExcellonObject.export_excellon() drills "%s" -> %s' % (
                        'dec' if form == 'dec' else e_zeros, str(e)))

        # slots processing
        if has_slots:
            for tool in self.tools:
                excellon_parts.append('G05\n')

                if int(tool) < 10:
                    excellon_parts.append('T0' + str(tool) + '\n')
                else:
                    excellon_parts.append('T' + str(tool) + '\n')

                slots = self.tools[tool]['slots']
                if slot_fmt is None or not slots:
                    continue
                try:
                    starts = point_coords([slot[0] for slot in slots])
                    stops = point_coords([slot[1] for slot in slots])
                    excellon_parts.append(lines_code(slot_fmt, np.hstack((starts, stops))))
                except Exception as err:
                    self.app.log.error('ExcellonObject.export_excellon() slots "%s" -> %s' % (
                        'dec' if form == 'dec' else e_zeros, str(err)))

        if not has_drills and not has_slots:
            self.app.log.debug("ExcellonObject.export_excellon() --> Excellon Object is empty: no drills, no slots.")
            return 'fail'

        return slots_in_file, ''.join(excellon_parts)

    def _milling_tool_keys(self, tools):
        """Resolve the tools argument ("all", a list or None = the selected rows) to tool keys."""
        if tools is None:
            tools = self.get_selected_tools_list()
        if tools == "all":
            tools = [k for k, _v in sorted(((k, v['tooldia']) for k, v in self.tools.items()), key=lambda t: t[1])]
            self.app.log.debug("Tools 'all' and sorted are: %s" % str(tools))
        return tools

    def _create_milling_geometry(self, outname, geo_init, plot, use_thread):
        """Create the milling Geometry object, on the app worker when use_thread is set (GUI buttons).

        Returns (success, msg); with use_thread the result is not known yet, so success is reported."""
        if use_thread:
            def geo_thread(a_obj):
                a_obj.app_obj.new_object("geometry", outname, geo_init, plot=plot)

            # Create a promise with the name of the object that will be created
            self.app.collection.promise(outname)

            # Send to worker
            self.app.worker_task.emit({'fcn': geo_thread, 'params': [self.app]})
            return True, ""

        result = self.app.app_obj.new_object("geometry", outname, geo_init, plot=plot)
        if result == 'fail' or result is False:
            return False, 'Failed to create milling geometry.'
        return True, ""

    def _milling_steps(self, radius):
        """The segments of a full circle of a milling path of this radius: the preferences "Circles" of Geometry."""
        return object_circle_steps(self.app.options, 'geometry', radius, self.units)

    @staticmethod
    def _milling_multidepth(app_obj):
        return app_obj.options.get(
            "tools_mill_multidepth",
            app_obj.defaults.get("tools_mill_multidepth",
                                 app_obj.defaults.factory_defaults.get("tools_mill_multidepth")))

    def generate_milling_drills(self, tools=None, outname=None, tooldia=None, plot=False, use_thread=False):
        """
        Will generate an Geometry Object allowing to cut a drill hole instead of drilling it.

        It takes its options from parameters or otherwise from the object's options and returns a
        (success, msg) tuple as feedback for shell operations. The geometry comes from MillingGen.

        :param tools:       A list of tools where the drills are to be milled or a string: "all"
        :param outname:     the name of the resulting Geometry object
        :param tooldia:     the tool diameter to be used in creation of the milling path (Geometry Object)
        :param plot:        if to plot the resulting object
        :param use_thread:  if to create the Geometry object on the app worker (result is not awaited)
        :return:            Success/failure condition tuple (bool, str).
        """
        from appPlugins.ToolMilling import MillingGen

        tools = self._milling_tool_keys(tools)

        if outname is None:
            outname = self.obj_options["name"] + "_mill"

        if tooldia is None:
            tooldia = self.ui.tooldia_entry.get_value()

        if len(tools) == 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Please select one or more tools from the list and try again."))
            return False, "Error: No tools."

        # all the tools with drills that are too small, in 1 message
        too_small = MillingGen.milling_drills_too_small(self.tools, tools, tooldia, self.decimals)
        if too_small:
            mseg = '[ERROR_NOTCL] %s %s: %s' % (_("Milling tool for DRILLS is larger than hole size. Cancelled."),
                                                _("Tool"),
                                                ', '.join(str(tool) for tool in too_small))
            self.app.inform.emit(mseg)
            return False, "Error: Milling tool is larger than hole."

        def geo_init(geo_obj, app_obj):
            assert geo_obj.kind == 'geometry', "Initializer expected a GeometryObject, got %s" % type(geo_obj)

            geo_obj.obj_options['type'] = 'Excellon Geometry'
            geo_obj.obj_options["tools_mill_tooldia"] = str(tooldia)
            # the CNC job reads tools_mill_multidepth
            geo_obj.obj_options["tools_mill_multidepth"] = self._milling_multidepth(app_obj)
            geo_obj.solid_geometry = MillingGen.milling_drills_geometry(
                self.tools, tools, tooldia, steps=self._milling_steps, decimals=self.decimals)

            if not geo_obj.solid_geometry:
                return "fail"

        return self._create_milling_geometry(outname, geo_init, plot, use_thread)

    def generate_milling_slots(self, tools=None, outname=None, tooldia=None, plot=False, use_thread=False):
        """
        Will generate an Geometry Object allowing to cut/mill a slot hole.

        It takes its options from parameters or otherwise from the object's options and returns a
        (success, msg) tuple as feedback for shell operations. The geometry comes from MillingGen.

        :param tools:       A list of tools where the slots are to be milled or a string: "all"
        :param outname:     the name of the resulting Geometry object
        :param tooldia:     the tool diameter to be used in creation of the milling path (Geometry Object)
        :param plot:        if to plot the resulting object
        :param use_thread:  if to create the Geometry object on the app worker (result is not awaited)
        :return:            Success/failure condition tuple (bool, str).
        """
        from appPlugins.ToolMilling import MillingGen

        tools = self._milling_tool_keys(tools)

        if outname is None:
            outname = self.obj_options["name"] + "_mill_slot"

        if tooldia is None:
            tooldia = float(self.obj_options["slot_tooldia"])

        if len(tools) == 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Please select one or more tools from the list and try again."))
            return False, "Error: No tools."

        use_tools = []
        for tool in tools:
            if not self.tools[tool]['slots']:
                # No slots for this tool
                continue
            if MillingGen.milling_bit_too_large(self.tools[tool]["tooldia"], tooldia, self.decimals):
                self.app.inform.emit('[ERROR_NOTCL] %s' %
                                     _("Milling tool for SLOTS is larger than hole size. Cancelled."))
                return False, "Error: Milling tool is larger than hole."
            use_tools.append(tool)
        self.app.log.debug("Milling slots using tools: %s" % use_tools)

        def geo_init(geo_obj, app_obj):
            assert geo_obj.kind == 'geometry', "Initializer expected a GeometryObject, got %s" % type(geo_obj)

            geo_obj.obj_options['type'] = 'Excellon Geometry'
            geo_obj.obj_options["tools_mill_tooldia"] = str(tooldia)
            geo_obj.obj_options["tools_mill_multidepth"] = self._milling_multidepth(app_obj)
            geo_obj.solid_geometry = MillingGen.milling_slots_geometry(
                self.tools, use_tools, tooldia, self.decimals, self._milling_steps, line_string=LineString)
            if not geo_obj.solid_geometry:
                self.app.log.debug("FAIL: No geometry generated.")
                return "fail"

        return self._create_milling_geometry(outname, geo_init, plot, use_thread)

    def on_generate_milling_button_click(self):
        self.app.defaults.report_usage("excellon_on_create_milling_drills button")
        self.read_form()
        self.generate_milling_drills(use_thread=True, plot=True)

    def on_generate_milling_slots_button_click(self):
        self.app.defaults.report_usage("excellon_on_create_milling_slots_button")
        self.read_form()
        self.generate_milling_slots(use_thread=True, plot=True)

    def convert_units(self, units):
        self.app.log.debug("ExcellonObject.convert_units()")

        Excellon.convert_units(self, units)

        # factor = Excellon.convert_units(self, units)
        # self.obj_options['drillz'] = float(self.obj_options['drillz']) * factor
        # self.obj_options['travelz'] = float(self.obj_options['travelz']) * factor
        # self.obj_options['feedrate'] = float(self.obj_options['feedrate']) * factor
        # self.obj_options['feedrate_rapid'] = float(self.obj_options['feedrate_rapid']) * factor
        # self.obj_options['toolchangez'] = float(self.obj_options['toolchangez']) * factor
        #
        # if self.app.options["excellon_toolchangexy"] == '':
        #     self.obj_options['toolchangexy'] = "0.0, 0.0"
        # else:
        #     coords_xy = [float(eval(coord)) for coord in self.app.options["excellon_toolchangexy"].split(",")]
        #     if len(coords_xy) < 2:
        #         self.app.inform.emit('[ERROR] %s' % _("The Toolchange X,Y field in Edit -> Preferences has to be "
        #                                               "in the format (x, y) \n"
        #                                               "but now there is only one value, not two. "))
        #         return 'fail'
        #     coords_xy[0] *= factor
        #     coords_xy[1] *= factor
        #     self.obj_options['toolchangexy'] = "%f, %f" % (coords_xy[0], coords_xy[1])
        #
        # if self.obj_options['startz'] is not None:
        #     self.obj_options['startz'] = float(self.obj_options['startz']) * factor
        # self.obj_options['endz'] = float(self.obj_options['endz']) * factor

    def on_solid_cb_click(self):
        if self.muted_ui:
            return
        self.read_form_item('solid')
        self.plot()

    def on_multicolored_cb_click(self, val):
        if self.muted_ui:
            return
        self.read_form_item('multicolored')
        self.plot()
        if not val:
            self.build_ui()

    def on_autoload_db_toggled(self, state):
        self.app.options["excellon_autoload_db"] = True if state else False

    def on_plot_cb_click(self):
        if self.muted_ui:
            return
        # self.plot()
        self.read_form_item('plot')

        self.ui_disconnect()
        cb_flag = self.ui.plot_cb.isChecked()
        for row in range(self.ui.tools_table.rowCount() - 2):
            table_cb = self.ui.tools_table.cellWidget(row, 5)
            table_cb.setChecked(True) if cb_flag else table_cb.setChecked(False)

        self.ui_connect()
        self.on_plot_cb_click_table()

    def on_plot_cb_click_table(self):
        self.ui_disconnect()

        # the plot checkbox of each tool, by tool id; the 2 total rows at the end have no id and no checkbox
        plot_cb_by_tool = {}
        for row in range(self.ui.tools_table.rowCount()):
            id_item = self.ui.tools_table.item(row, 0)
            plot_cb = self.ui.tools_table.cellWidget(row, 5)
            try:
                tool_id = int(float(id_item.text()))
            except (AttributeError, ValueError):
                continue
            if plot_cb is not None:
                plot_cb_by_tool[tool_id] = plot_cb

        for tool_key in self.tools:
            plot_cb = plot_cb_by_tool.get(int(tool_key))
            if plot_cb is None:
                # the table has no row for this tool: keep its visibility
                continue
            try:
                self.shapes.update_visibility(plot_cb.isChecked(), indexes=self.shape_indexes_dict[tool_key])
            except KeyError:
                # this tool has no plotted shapes
                pass
        self.shapes.redraw()
        self.ui_connect()

    def plot_transformed(self, matrix, kind=None):
        """
        Scale and skew move the drills and slots but keep the tool diameters, so the holes stay circles: only a rigid
        transform (rotate, mirror, offset) can map the plotted shapes. Any other matrix plots the object again.
        """
        a, b, d, e = matrix[:4]
        if abs(a * a + d * d - 1.0) > 1e-9 or abs(b * b + e * e - 1.0) > 1e-9 or abs(a * b + d * e) > 1e-9:
            if self.deleted:
                return False
            self.plot(kind=kind) if kind is not None else self.plot()
            return False
        return super().plot_transformed(matrix, kind=kind)

    def plot(self, visible=None, kind=None):

        # the plot state is read from the options (the UI widgets mirror them and are not needed to plot)
        multicolored = self.obj_options['multicolored']

        # Does all the required setup and returns False
        # if the 'ptint' option is set to False.
        if not FlatCAMObj.plot(self):
            return

        random_color = self.random_color

        # this stays for compatibility reasons, in case we try to open old projects
        try:
            __ = iter(self.solid_geometry)
        except TypeError:
            self.solid_geometry = [self.solid_geometry]

        if visible is None:
            visible = self.obj_options['plot']

        # the shape indexes of a previous plot are not valid anymore
        self.shape_indexes_dict.clear()

        try:
            # Plot Excellon (All polygons?)
            if self.obj_options['solid']:
                # Collect all shapes into one batch, tracking tool boundaries
                batch = []
                tool_ranges = {}  # tool -> (start_index, end_index) in batch

                for tool in self.tools:
                    # set the color here so we have one color for each tool
                    geo_color = random_color()
                    if multicolored:
                        self.tools[tool]['multicolor'] = geo_color
                    else:
                        self.tools[tool]['multicolor'] = None

                    batch_start = len(batch)
                    for geo in self.tools[tool]["solid_geometry"]:
                        batch.append({
                            'shape': geo,
                            'color': geo_color if multicolored else self.outline_color,
                            'face_color': geo_color if multicolored else self.fill_color,
                            'layer': 2
                        })
                    tool_ranges[tool] = (batch_start, len(batch))

                if batch:
                    keys = self.add_shapes_batch(batch, visible=visible)
                    # Map returned keys back to tools
                    for tool, (start, end) in tool_ranges.items():
                        self.shape_indexes_dict[tool] = list(keys[start:end])
            else:
                # Non-solid mode: plot outlines only (fewer shapes, keep individual add)
                for tool in self.tools:
                    for geo in self.tools[tool]['solid_geometry']:
                        idx = self.add_shape(shape=geo.exterior, color='red', visible=visible)
                        try:
                            self.shape_indexes_dict[tool].append(idx)
                        except KeyError:
                            self.shape_indexes_dict[tool] = [idx]
                        for ints in geo.interiors:
                            idx = self.add_shape(shape=ints, color='orange', visible=visible)
                            try:
                                self.shape_indexes_dict[tool].append(idx)
                            except KeyError:
                                self.shape_indexes_dict[tool] = [idx]

            self.shapes.redraw()
            self.mark_full_plot()
        except (ObjectDeleted, AttributeError) as e:
            self.app.log.debug("ExcellonObject.plot() -> %s" % str(e))
            self.shapes.clear(update=True)

        # the build of the tools table needs the UI
        if multicolored and self.ui is not None:
            self.multicolored_build_sig.emit()

    def on_multicolored_build(self):
        self.build_ui()

    @staticmethod
    def _tool_data_equal(data_a, data_b):
        """
        True if two tool ``data`` dicts hold the same tool parameters: the keys that start with 'tools_'
        (tools_drill_*, tools_mill_*). The tool data is a copy of the object options, so it also holds per-object keys
        (name, the bounds xmin ... ymax, the file format and zeros, plot, units ...) that differ between source
        objects and say nothing about how the tool is used; they are not compared. A missing data counts as {}.
        A comparison that raises (e.g. a numpy array value) counts as "not equal", so the tools stay separate.
        """
        a = {k: v for k, v in (data_a or {}).items() if str(k).startswith('tools_')}
        b = {k: v for k, v in (data_b or {}).items() if str(k).startswith('tools_')}
        try:
            return bool(a == b)
        except Exception:
            return False

    def from_dict(self, d):
        FlatCAMObj.from_dict(self, d)
        # JSON has no tuples: a slot (start, end) comes back from a project file as a list [start, end]
        for tool in self.tools.values():
            if 'slots' in tool:
                tool['slots'] = [tuple(slot) for slot in tool['slots']]

    @staticmethod
    def merge(exc_list, exc_final, decimals=None, fuse_tools=True, log=None, inform=None):
        """
        Merge Excellon objects found in exc_list parameter into exc_final object.
        Options are always copied from source .

        With fuse_tools=False every tool of every source object is kept as its own tool (ids 1..n in source order).

        With fuse_tools=True two tools are fused into one only when both conditions hold:
        - their diameters are equal after rounding to ``decimals``;
        - their tool parameters are equal: the ``data`` keys that start with 'tools_' (see _tool_data_equal). The
          per-object keys that the data also holds (name, bounds, file format, plot ...) are not compared.
        Tools of the same diameter with different data stay separate tools, each keeping its own data, drills and
        slots (no silent metadata loss). Tools with a ``data`` that cannot be compared with == stay separate.
        The result tools are numbered 1..n in the order of their first appearance.

        If only one object is in exc_list parameter then this function will copy that object in the exc_final

        :param exc_list:    List or one object of ExcellonObject Objects to join.
        :type exc_list:     list
        :param exc_final:   Destination ExcellonObject object.
        :type exc_final:    class
        :param decimals:    The number of decimals to be used for diameters
        :type decimals:     int
        :param fuse_tools:  If True will try to fuse tools of the same diameter for the Excellon objects
        :type fuse_tools:   bool
        :param log:         the logging object used
        :param inform:      optional callable that gets the shell message of the duplicate and overlap check
        :return:            None
        """

        if exc_final.tools is None:
            exc_final.tools = {}

        if decimals is None:
            decimals = 4
        decimals_exc = decimals

        try:
            flattened_list = list(itertools.chain(*exc_list))
        except TypeError:
            flattened_list = exc_list

        new_tools = {}
        total_geo = []
        toolid = 0
        tool_sources = {}
        for src_index, exc in enumerate(flattened_list):
            # copy options of the current excellon obj to the final excellon obj
            # only the last object options will survive
            for option in exc.obj_options:
                if option != 'name':
                    try:
                        exc_final.obj_options[option] = deepcopy(exc.obj_options[option])
                    except Exception:
                        if log:
                            log.warning("Failed to copy option.", option)

            for tool in exc.tools:
                toolid += 1
                new_tools[toolid] = deepcopy(exc.tools[tool])
                tool_sources[toolid] = src_index

            exc_final.tools = deepcopy(new_tools)
            # add the zeros and units to the exc_final object
            exc_final.zeros = deepcopy(exc.zeros)
            exc_final.units = deepcopy(exc.units)
            total_geo += exc.solid_geometry

        exc_final.solid_geometry = deepcopy(total_geo)

        # duplicates and overlaps between the holes of different source objects
        if len(flattened_list) > 1 and exc_final.tools:
            removed, conflicts = dedupe_joined_holes(exc_final.tools, tool_sources, decimals_exc)
            if removed:
                msg = _("Excellon join: duplicate holes removed") + ": %d" % removed
                if log:
                    log.info(msg)
                if inform:
                    inform('[success] %s' % msg)
            if conflicts:
                where = ", ".join("(%.*f, %.*f)" % (decimals_exc, x, decimals_exc, y) for x, y in conflicts[:5])
                msg = _("Excellon join: holes that overlap or duplicate with another diameter") + \
                    ": %d. %s: %s%s" % (len(conflicts), _("First"), where, " ..." if len(conflicts) > 5 else "")
                if log:
                    log.warning(msg)
                if inform:
                    inform('[WARNING_NOTCL] %s' % msg)

        if exc_final.tools and fuse_tools:
            fused_tools_dict = {}
            for tool_dict in exc_final.tools.values():
                current_tooldia = float('%.*f' % (decimals_exc, tool_dict['tooldia']))

                target = None
                for f_tool in fused_tools_dict.values():
                    if f_tool['tooldia'] == current_tooldia and \
                            ExcellonObject._tool_data_equal(f_tool.get('data'), tool_dict.get('data')):
                        target = f_tool
                        break

                if target is not None:
                    if tool_dict.get('drills'):
                        target.setdefault('drills', []).extend(tool_dict['drills'])
                    if tool_dict.get('slots'):
                        target.setdefault('slots', []).extend(tool_dict['slots'])
                    if tool_dict.get('solid_geometry'):
                        target.setdefault('solid_geometry', []).extend(tool_dict['solid_geometry'])
                else:
                    tool_dict['tooldia'] = current_tooldia
                    fused_tools_dict[len(fused_tools_dict) + 1] = tool_dict

            exc_final.tools = fused_tools_dict

        # create the geometry for the exc_final object
        exc_final.create_geometry()
