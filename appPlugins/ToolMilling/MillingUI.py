# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     6/15/2020                                      #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    FCComboBox2, RadioSet, FCDoubleSpinner, FCSpinner, NumericalEvalTupleEntry, FCTable, \
    OptionalInputSection, OptionalHideInputSection, opposite_direction
from defaults import AppDefaults

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class MillingUI:

    pluginName = _("Milling")

    def __init__(self, layout, app, name):
        self.app = app
        self.decimals = self.app.decimals
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.layout = layout

        self.title_box = QtWidgets.QHBoxLayout()
        self.layout.addLayout(self.title_box)

        # ## Title
        title_label = FCLabel("%s" % name, size=16, bold=True)
        # title_label.setStyleSheet("""
        #                         QLabel
        #                         {
        #                             font-size: 16px;
        #                             font-weight: bold;
        #                         }
        #                         """)
        title_label.setToolTip(
            _("Create CNCJob with toolpaths for milling either Geometry or drill holes.")
        )

        self.title_box.addWidget(title_label)

        # App Level label
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(
            _(
                "Beginner Mode - many parameters are hidden.\n"
                "Advanced Mode - full control.\n"
                "Permanent change is done in 'Preferences' menu."
            )
        )
        # self.level.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        # #############################################################################################################
        # Source Object for Milling Frame
        # #############################################################################################################
        self.obj_combo_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.obj_combo_label.setToolTip(
            _("Source object for milling operation.")
        )
        self.tools_box.addWidget(self.obj_combo_label)

        obj_frame = FCFrame()
        self.tools_box.addWidget(obj_frame)

        # Grid Layout
        obj_grid = GLay(v_spacing=5, h_spacing=3)
        obj_frame.setLayout(obj_grid)

        self.target_label = FCLabel('%s:' % _("Type"))
        self.target_label.setToolTip(
            _("Object for milling operation.")
        )

        self.target_radio = RadioSet(
            [
                {'label': _('Geometry'), 'value': 'geo'},
                {'label': _('Excellon'), 'value': 'exc'}
            ],
            compact=True)

        obj_grid.addWidget(self.target_label, 0, 0)
        obj_grid.addWidget(self.target_radio, 0, 1)

        # ################################################
        # ##### The object to be milled #################
        # ################################################
        self.object_combo = FCComboBox()
        self.object_combo.setModel(self.app.collection)
        self.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        # self.object_combo.setCurrentIndex(1)
        self.object_combo.is_last = True

        obj_grid.addWidget(self.object_combo, 2, 0, 1, 2)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # obj_grid.addWidget(separator_line, 4, 0, 1, 2)

        # #############################################################################################################
        # Tool Table Frame
        # #############################################################################################################

        # Grid Layout
        tool_title_grid = GLay(v_spacing=5, h_spacing=3)
        self.tools_box.addLayout(tool_title_grid)

        self.tools_table_label = FCLabel('%s' % _("Tools Table"), color='green', bold=True)
        self.tools_table_label.setToolTip(
            _("Tools in the object used for milling.")
        )
        tool_title_grid.addWidget(self.tools_table_label, 0, 0)

        # Plot CB
        self.plot_cb = FCCheckBox(_('Plot Object'))
        self.plot_cb.setToolTip(_("Plot (show) this object."))
        self.plot_cb.setLayoutDirection(opposite_direction())
        tool_title_grid.addWidget(self.plot_cb, 0, 1)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        # Grid Layout
        tool_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(tool_grid)

        # ################################################
        # ########## Excellon Tool Table #################
        # ################################################
        self.tools_table_mill_exc = FCTable(drag_drop=True)
        self.tools_table_mill_exc.setRowCount(2)
        tool_grid.addWidget(self.tools_table_mill_exc, 0, 0, 1, 2)

        self.tools_table_mill_exc.setColumnCount(5)
        self.tools_table_mill_exc.setColumnHidden(3, True)
        self.tools_table_mill_exc.setSortingEnabled(False)

        self.tools_table_mill_exc.setHorizontalHeaderLabels(['#', _('Diameter'), _('Drills'), '', _('Slots')])
        self.tools_table_mill_exc.horizontalHeaderItem(0).setToolTip(
            _("This is the Tool Number.\n"
              "When ToolChange is checked, on toolchange event this value\n"
              "will be showed as a T1, T2 ... Tn in the Machine Code.\n\n"
              "Here the tools are selected for G-code generation."))
        self.tools_table_mill_exc.horizontalHeaderItem(1).setToolTip(
            _("Tool Diameter. Its value\n"
              "is the cut width into the material."))
        self.tools_table_mill_exc.horizontalHeaderItem(2).setToolTip(
            _("The number of Drill holes. Holes that are drilled with\n"
              "a drill bit."))
        self.tools_table_mill_exc.horizontalHeaderItem(4).setToolTip(
            _("The number of Slot holes. Holes that are created by\n"
              "milling them with an endmill bit."))

        # #############################################################################################################
        # This should not be done this, it's the job of the build_mill_ui() from the Milling class
        # #############################################################################################################
        # add a last row with the Total number of drills
        empty_1 = QtWidgets.QTableWidgetItem('')
        empty_1.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        empty_1_1 = QtWidgets.QTableWidgetItem('')
        empty_1_1.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

        label_tot_drill_count = QtWidgets.QTableWidgetItem(_('Total Drills'))
        label_tot_drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        tot_drill_count = QtWidgets.QTableWidgetItem('%d' % 0)
        tot_drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.tools_table_mill_exc.setItem(0, 0, empty_1)
        self.tools_table_mill_exc.setItem(0, 1, label_tot_drill_count)
        self.tools_table_mill_exc.setItem(0, 2, tot_drill_count)  # Total number of drills
        self.tools_table_mill_exc.setItem(0, 4, empty_1_1)

        font = QtGui.QFont()
        font.setBold(True)

        for k in [1, 2]:
            self.tools_table_mill_exc.item(0, k).setForeground(QtGui.QColor(127, 0, 255))
            self.tools_table_mill_exc.item(0, k).setFont(font)

        # add a last row with the Total number of slots
        empty_2 = QtWidgets.QTableWidgetItem('')
        empty_2.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        empty_2_1 = QtWidgets.QTableWidgetItem('')
        empty_2_1.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

        label_tot_slot_count = QtWidgets.QTableWidgetItem(_('Total Slots'))
        tot_slot_count = QtWidgets.QTableWidgetItem('%d' % 0)
        label_tot_slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        tot_slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.tools_table_mill_exc.setItem(1, 0, empty_2)
        self.tools_table_mill_exc.setItem(1, 1, label_tot_slot_count)
        self.tools_table_mill_exc.setItem(1, 2, empty_2_1)
        self.tools_table_mill_exc.setItem(1, 4, tot_slot_count)  # Total number of slots

        for kl in [1, 2, 4]:
            self.tools_table_mill_exc.item(1, kl).setFont(font)
            self.tools_table_mill_exc.item(1, kl).setForeground(QtGui.QColor(0, 70, 255))

        self.tools_table_mill_exc.resizeColumnsToContents()
        self.tools_table_mill_exc.resizeRowsToContents()

        vertical_header = self.tools_table_mill_exc.verticalHeader()
        vertical_header.hide()
        self.tools_table_mill_exc.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.tools_table_mill_exc.horizontalHeader()
        self.tools_table_mill_exc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)

        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.tools_table_mill_exc.setSortingEnabled(False)

        self.tools_table_mill_exc.setMinimumHeight(self.tools_table_mill_exc.getHeight())
        self.tools_table_mill_exc.setMaximumHeight(self.tools_table_mill_exc.getHeight())

        # Tool order
        self.order_label = FCLabel('%s:' % _('Tool order'))
        self.order_label.setToolTip(_("This set the way that the tools in the tools table are used.\n"
                                      "'Default' --> the order from the file\n"
                                      "'Forward' --> tools will be ordered from small to big\n"
                                      "'Reverse' --> tools will ordered from big to small."))

        self.order_combo = FCComboBox2()
        self.order_combo.addItems([_('Default'), _('Forward'), _('Reverse')])

        tool_grid.addWidget(self.order_label, 2, 0)
        tool_grid.addWidget(self.order_combo, 2, 1)

        # ************************************************************************
        # ************** Geometry Tool Table *************************************
        # ************************************************************************

        # Tool Table for Geometry
        self.tools_table_mill_geo = FCTable(drag_drop=True)
        self.tools_table_mill_geo.setRowCount(0)
        self.tools_table_mill_geo.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.tools_table_mill_geo.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        tool_grid.addWidget(self.tools_table_mill_geo, 4, 0, 1, 2)

        self.tools_table_mill_geo.setColumnCount(5)
        self.tools_table_mill_geo.setColumnWidth(0, 20)
        self.tools_table_mill_geo.setHorizontalHeaderLabels(['#', _('Dia'), '', '', 'P'])
        self.tools_table_mill_geo.setColumnHidden(2, True)
        self.tools_table_mill_geo.setColumnHidden(3, True)

        self.tools_table_mill_geo.horizontalHeaderItem(0).setToolTip(
            _(
                "This is the Tool Number.\n"
                "When ToolChange is checked, on toolchange event this value\n"
                "will be showed as a T1, T2 ... Tn")
        )
        self.tools_table_mill_geo.horizontalHeaderItem(1).setToolTip(
            _("Tool Diameter. Its value\n"
              "is the cut width into the material."))

        self.tools_table_mill_geo.horizontalHeaderItem(4).setToolTip(
            _(
                "Plot column. It is visible only for MultiGeo geometries, meaning geometries that holds the geometry\n"
                "data into the tools. For those geometries, deleting the tool will delete the geometry data also,\n"
                "so be WARNED. From the checkboxes on each row it can be enabled/disabled the plot on canvas\n"
                "for the corresponding tool."
            ))

        # Hide the Tools Table on start
        self.tools_table_mill_exc.hide()
        self.tools_table_mill_geo.hide()
        self.order_label.hide()
        self.order_combo.hide()

        # #############################################################################################################
        # ADD TOOLS FOR GEOMETRY OBJECT
        # #############################################################################################################
        self.add_tool_frame = QtWidgets.QFrame()
        self.add_tool_frame.setContentsMargins(0, 0, 0, 0)
        tool_grid.addWidget(self.add_tool_frame, 6, 0, 1, 2)

        new_tool_grid = GLay(v_spacing=5, h_spacing=3)
        new_tool_grid.setContentsMargins(0, 0, 0, 0)
        self.add_tool_frame.setLayout(new_tool_grid)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        new_tool_grid.addWidget(separator_line, 0, 0, 1, 2)

        self.tool_sel_label = FCLabel('%s' % _("Add from DB"), bold=True)
        new_tool_grid.addWidget(self.tool_sel_label, 2, 0, 1, 2)

        self.addtool_entry_lbl = FCLabel('%s:' % _('Tool Dia'))
        self.addtool_entry_lbl.setToolTip(
            _("Diameter for the new tool")
        )
        self.addtool_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.addtool_entry.set_precision(self.decimals)
        self.addtool_entry.set_range(0.00001, 10000.0000)
        self.addtool_entry.setSingleStep(0.1)
        self.addtool_entry.setObjectName("mill_cnctooldia")

        new_tool_grid.addWidget(self.addtool_entry_lbl, 3, 0)
        new_tool_grid.addWidget(self.addtool_entry, 3, 1)

        # #############################################################################################################
        # ################################    Button Grid   ###########################################################
        # #############################################################################################################
        button_grid = GLay(v_spacing=5, h_spacing=3)
        button_grid.setColumnStretch(0, 1)
        button_grid.setColumnStretch(1, 0)
        new_tool_grid.addLayout(button_grid, 5, 0, 1, 2)

        self.search_and_add_btn = FCButton(_('Search and Add'))
        self.search_and_add_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/plus16.png'))
        self.search_and_add_btn.setToolTip(
            _("Add a new tool to the Tool Table\n"
              "with the diameter specified above.\n"
              "This is done by a background search\n"
              "in the Tools Database. If nothing is found\n"
              "in the Tools DB then a default tool is added.")
        )

        button_grid.addWidget(self.search_and_add_btn, 0, 0)

        self.addtool_from_db_btn = FCButton(_('Pick from DB'))
        self.addtool_from_db_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/search_db32.png'))
        self.addtool_from_db_btn.setToolTip(
            _("Add a new tool to the Tool Table\n"
              "from the Tools Database.\n"
              "Tools database administration in in:\n"
              "Menu: Options -> Tools Database")
        )

        button_grid.addWidget(self.addtool_from_db_btn, 1, 0)

        self.deltool_btn = FCButton()
        self.deltool_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/trash16.png'))
        self.deltool_btn.setToolTip(
            _("Delete a selection of tools in the Tool Table\n"
              "by first selecting a row in the Tool Table.")
        )
        self.deltool_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)

        button_grid.addWidget(self.deltool_btn, 0, 1, 2, 1)
        # #############################################################################################################

        self.add_tool_frame.hide()

        # #############################################################################################################
        # ALL Parameters Frame
        # #############################################################################################################
        self.tool_data_label = FCLabel(
            "<b>%s: <font color='#0000FF'>%s %d</font></b>" % (_('Parameters for'), _("Tool"), int(1)))
        self.tool_data_label.setToolTip(
            _(
                "The data used for creating GCode.\n"
                "Each tool store it's own set of such data."
            )
        )
        self.tools_box.addWidget(self.tool_data_label)

        self.param_frame = QtWidgets.QFrame()
        self.param_frame.setContentsMargins(0, 0, 0, 0)
        self.tools_box.addWidget(self.param_frame)

        self.tool_params_box = QtWidgets.QVBoxLayout()
        self.tool_params_box.setContentsMargins(0, 0, 0, 0)
        self.param_frame.setLayout(self.tool_params_box)

        # #############################################################################################################
        # Tool Parameters Frame
        # #############################################################################################################

        tp_frame = FCFrame()
        self.tool_params_box.addWidget(tp_frame)

        # Grid Layout
        param_grid = GLay(v_spacing=5, h_spacing=3)
        tp_frame.setLayout(param_grid)

        # Milling Type
        self.mill_type_label = FCLabel('%s:' % _('Milling Type'))
        self.mill_type_label.setToolTip(
            _("Milling type:\n"
              "- Drills -> will mill the drills associated with this tool\n"
              "- Slots -> will mill the slots associated with this tool\n"
              "- Both -> will mill both drills and mills or whatever is available")
        )
        self.milling_type_radio = RadioSet(
            [
                {'label': _('Drills'), 'value': 'drills'},
                {'label': _("Slots"), 'value': 'slots'},
                {'label': _("Both"), 'value': 'both'},
            ]
        )
        self.milling_type_radio.setObjectName("milling_type")

        param_grid.addWidget(self.mill_type_label, 0, 0)
        param_grid.addWidget(self.milling_type_radio, 1, 0, 1, 2)

        # Milling Diameter
        self.mill_dia_label = FCLabel('%s:' % _('Milling Diameter'))
        self.mill_dia_label.setToolTip(
            _("The diameter of the tool who will do the milling")
        )

        self.mill_dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.mill_dia_entry.set_precision(self.decimals)
        self.mill_dia_entry.set_range(0.0000, 10000.0000)
        self.mill_dia_entry.setObjectName("milling_dia")

        param_grid.addWidget(self.mill_dia_label, 2, 0)
        param_grid.addWidget(self.mill_dia_entry, 2, 1)

        self.mill_type_label.hide()
        self.milling_type_radio.hide()
        self.mill_dia_label.hide()
        self.mill_dia_entry.hide()

        # Offset Type
        self.offset_type_lbl = FCLabel('%s:' % _('Offset Type'))
        self.offset_type_lbl.setToolTip(
            _(
                "The value for the Offset can be:\n"
                "- Path -> There is no offset, the tool cut will be done through the geometry line.\n"
                "- In(side) -> The tool cut will follow the geometry inside. It will create a 'pocket'.\n"
                "- Out(side) -> The tool cut will follow the geometry line on the outside.\n"
                "- Custom -> The tool will cut at an chosen offset."
            ))

        self.offset_type_combo = FCComboBox2()
        self.offset_type_combo.addItems(
            [_("Path"), _("In"), _("Out"), _("Custom")]
        )
        self.offset_type_combo.setObjectName('mill_offset_type')

        param_grid.addWidget(self.offset_type_lbl, 4, 0)
        param_grid.addWidget(self.offset_type_combo, 4, 1)

        # Tool Offset
        self.offset_label = FCLabel('%s:' % _('Custom'))
        self.offset_label.setToolTip(
            _(
                "The value to offset the cut when \n"
                "the Offset type selected is 'Custom'.\n"
                "The value can be positive for 'outside'\n"
                "cut and negative for 'inside' cut."
            )
        )

        self.offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.offset_entry.set_precision(self.decimals)
        self.offset_entry.set_range(-10000.0000, 10000.0000)
        self.offset_entry.setObjectName("mill_offset")

        self.offset_label.hide()
        self.offset_entry.hide()

        param_grid.addWidget(self.offset_label, 6, 0)
        param_grid.addWidget(self.offset_entry, 6, 1)

        # Overlap
        self.ovlabel = FCLabel('%s:' % _('Overlap'))
        self.ovlabel.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.\n"
              "Adjust the value starting with lower values\n"
              "and increasing it if areas that should be processed are still \n"
              "not processed.\n"
              "Lower values = faster processing, faster execution on CNC.\n"
              "Higher values = slow processing and slow execution on CNC\n"
              "due of too many paths.")
        )
        self.overlap_entry = FCDoubleSpinner(callback=self.confirmation_message, suffix='%')
        self.overlap_entry.set_precision(3)
        self.overlap_entry.setWrapping(True)
        self.overlap_entry.setRange(0.0000, 99.9999)
        self.overlap_entry.setSingleStep(0.1)
        self.overlap_entry.setObjectName('milling_overlap')

        param_grid.addWidget(self.ovlabel, 8, 0)
        param_grid.addWidget(self.overlap_entry, 8, 1)

        # Connect lines
        self.connect_cb = FCCheckBox('%s' % _("Connect"))
        self.connect_cb.setObjectName('milling_connect')
        self.connect_cb.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )

        param_grid.addWidget(self.connect_cb, 10, 0, 1, 2)

        self.ovlabel.hide()
        self.overlap_entry.hide()
        self.connect_cb.hide()

        self.offset_separator_line = QtWidgets.QFrame()
        self.offset_separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.offset_separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        param_grid.addWidget(self.offset_separator_line, 12, 0, 1, 2)

        # Tool Type
        self.tool_shape_label = FCLabel('%s:' % _('Shape'))
        self.tool_shape_label.setToolTip(
            _("Tool Shape. \n"
              "Can be:\n"
              "C1 ... C4 = circular tool with x flutes\n"
              "B = ball tip milling tool\n"
              "V = v-shape milling tool\n"
              "L = laser")
        )

        self.tool_shape_combo = FCComboBox2(policy=False)
        self.tool_shape_combo.setObjectName('mill_tool_shape')
        self.tool_shape_combo.addItems(["C1", "C2", "C3", "C4", "B", "V", "L"])

        idx = int(self.app.options.get(
            'tools_mill_tool_shape',
            app_defaults.get('tools_mill_tool_shape', AppDefaults.factory_defaults.get('tools_mill_tool_shape'))))
        # protection against having this translated or loading a project with translated values
        if idx == -1:
            self.tool_shape_combo.setCurrentIndex(0)
        else:
            self.tool_shape_combo.setCurrentIndex(idx)

        param_grid.addWidget(self.tool_shape_label, 14, 0)
        param_grid.addWidget(self.tool_shape_combo, 14, 1)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # param_grid.addWidget(separator_line, 12, 0, 1, 2)

        # Job Type
        self.job_type_lbl = FCLabel('%s:' % _('Job'))
        self.job_type_lbl.setToolTip(
            _(
                "- Isolation -> informative - lower Feedrate as it uses a milling bit with a fine tip.\n"
                "- Roughing  -> informative - lower Feedrate and multiDepth cut.\n"
                "- Finishing -> informative - higher Feedrate, without multiDepth.\n"
                "- Polish -> adds a painting sequence over the whole area of the object"
            )
        )

        self.job_type_combo = FCComboBox2()
        self.job_type_combo.addItems(
            [_('Roughing'), _('Finishing'), _('Isolation'), _('Polishing')]
        )
        self.job_type_combo.setObjectName('mill_job_type')

        param_grid.addWidget(self.job_type_lbl, 16, 0)
        param_grid.addWidget(self.job_type_combo, 16, 1)

        # Polish Margin
        self.polish_margin_lbl = FCLabel('%s:' % _('Margin'))
        self.polish_margin_lbl.setToolTip(
            _("Bounding box margin.")
        )
        self.polish_margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.polish_margin_entry.set_precision(self.decimals)
        self.polish_margin_entry.set_range(-10000.0000, 10000.0000)
        self.polish_margin_entry.setObjectName("mill_polish_margin")

        param_grid.addWidget(self.polish_margin_lbl, 18, 0)
        param_grid.addWidget(self.polish_margin_entry, 18, 1)

        # Polish Overlap
        self.polish_over_lbl = FCLabel('%s:' % _('Overlap'))
        self.polish_over_lbl.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.")
        )
        self.polish_over_entry = FCDoubleSpinner(suffix='%', callback=self.confirmation_message)
        self.polish_over_entry.set_precision(self.decimals)
        self.polish_over_entry.setWrapping(True)
        self.polish_over_entry.set_range(0.0000, 99.9999)
        self.polish_over_entry.setSingleStep(0.1)
        self.polish_over_entry.setObjectName("mill_polish_overlap")

        param_grid.addWidget(self.polish_over_lbl, 20, 0)
        param_grid.addWidget(self.polish_over_entry, 20, 1)

        # Polish Method
        self.polish_method_lbl = FCLabel('%s:' % _('Method'))
        self.polish_method_lbl.setToolTip(
            _("Algorithm for polishing:\n"
              "- Standard: Fixed step inwards.\n"
              "- Seed-based: Outwards from seed.\n"
              "- Line-based: Parallel lines.")
        )

        self.polish_method_combo = FCComboBox2()
        self.polish_method_combo.addItems(
            [_("Standard"), _("Seed"), _("Lines")]
        )
        self.polish_method_combo.setObjectName('mill_polish_method')

        param_grid.addWidget(self.polish_method_lbl, 22, 0)
        param_grid.addWidget(self.polish_method_combo, 22, 1)

        self.polish_margin_lbl.hide()
        self.polish_margin_entry.hide()
        self.polish_over_lbl.hide()
        self.polish_over_entry.hide()
        self.polish_method_lbl.hide()
        self.polish_method_combo.hide()

        self.job_separator_line = QtWidgets.QFrame()
        self.job_separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.job_separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        param_grid.addWidget(self.job_separator_line, 24, 0, 1, 2)

        # Tip Dia
        self.tipdialabel = FCLabel('%s:' % _('V-Tip Dia'))
        self.tipdialabel.setToolTip(
            _(
                "The tip diameter for V-Shape Tool"
            )
        )
        self.tipdia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tipdia_entry.set_precision(self.decimals)
        self.tipdia_entry.set_range(0.00001, 10000.0000)
        self.tipdia_entry.setSingleStep(0.1)
        self.tipdia_entry.setObjectName("mill_tipdia")

        param_grid.addWidget(self.tipdialabel, 26, 0)
        param_grid.addWidget(self.tipdia_entry, 26, 1)

        # Tip Angle
        self.tipanglelabel = FCLabel('%s:' % _('V-Tip Angle'))
        self.tipanglelabel.setToolTip(
            _(
                "The tip angle for V-Shape Tool.\n"
                "In degree."
            )
        )
        self.tipangle_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tipangle_entry.set_precision(self.decimals)
        self.tipangle_entry.set_range(1.0, 180.0)
        self.tipangle_entry.setSingleStep(1)
        self.tipangle_entry.setObjectName("mill_tipangle")

        param_grid.addWidget(self.tipanglelabel, 28, 0)
        param_grid.addWidget(self.tipangle_entry, 28, 1)

        self.tipdialabel.hide()
        self.tipdia_entry.hide()
        self.tipanglelabel.hide()
        self.tipangle_entry.hide()

        # Cut Z
        self.cutzlabel = FCLabel('%s:' % _('Cut Z'))
        self.cutzlabel.setToolTip(
            _("Cutting depth (negative)\n"
              "below the copper surface."))

        self.cutz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutz_entry.set_precision(self.decimals)

        self.cutz_entry.set_range(-10000.0000, 10000.0000)

        self.cutz_entry.setSingleStep(0.1)
        self.cutz_entry.setObjectName("mill_cutz")

        param_grid.addWidget(self.cutzlabel, 30, 0)
        param_grid.addWidget(self.cutz_entry, 30, 1)

        # Multi-Depth
        self.mpass_cb = FCCheckBox('%s:' % _("Multi-Depth"))
        self.mpass_cb.setToolTip(
            _(
                "Use multiple passes to limit\n"
                "the cut depth in each pass. Will\n"
                "cut multiple times until Cut Z is\n"
                "reached."
            )
        )
        self.mpass_cb.setObjectName("mill_multidepth")

        self.maxdepth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.maxdepth_entry.set_precision(self.decimals)
        self.maxdepth_entry.set_range(0, 10000.0000)
        self.maxdepth_entry.setSingleStep(0.1)

        self.maxdepth_entry.setToolTip(_("Depth of each pass (positive)."))
        self.maxdepth_entry.setObjectName("mill_depthperpass")

        self.mis_mpass_geo = OptionalInputSection(self.mpass_cb, [self.maxdepth_entry])

        param_grid.addWidget(self.mpass_cb, 32, 0)
        param_grid.addWidget(self.maxdepth_entry, 32, 1)

        # Travel Z (z_move)
        self.travelzlabel = FCLabel('%s:' % _('Travel Z'))
        self.travelzlabel.setToolTip(
            _("Tool height when travelling\n"
              "across the XY plane.")
        )

        self.travelz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.travelz_entry.set_precision(self.decimals)

        self.travelz_entry.set_range(-10000.0000, 10000.0000)

        self.travelz_entry.setSingleStep(0.1)
        self.travelz_entry.setObjectName("mill_travelz")

        param_grid.addWidget(self.travelzlabel, 34, 0)
        param_grid.addWidget(self.travelz_entry, 34, 1)

        # Feedrate X-Y
        self.frxylabel = FCLabel('%s:' % _('Feedrate X-Y'))
        self.frxylabel.setToolTip(
            _("Cutting speed in the XY\n"
              "plane in units per minute")
        )
        self.xyfeedrate_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.xyfeedrate_entry.set_precision(self.decimals)
        self.xyfeedrate_entry.set_range(0, 10000.0000)
        self.xyfeedrate_entry.setSingleStep(0.1)
        self.xyfeedrate_entry.setObjectName("mill_feedratexy")

        param_grid.addWidget(self.frxylabel, 36, 0)
        param_grid.addWidget(self.xyfeedrate_entry, 36, 1)

        # Feedrate Z
        self.frzlabel = FCLabel('%s:' % _('Feedrate Z'))
        self.frzlabel.setToolTip(
            _("Tool speed while drilling\n"
              "(in units per minute).\n"
              "So called 'Plunge' feedrate.\n"
              "This is for linear move G01.")
        )
        self.feedrate_z_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feedrate_z_entry.set_precision(self.decimals)
        self.feedrate_z_entry.set_range(0.0, 910000.0000)
        self.feedrate_z_entry.setSingleStep(0.1)
        self.feedrate_z_entry.setObjectName("mill_feedratez")

        param_grid.addWidget(self.frzlabel, 38, 0)
        param_grid.addWidget(self.feedrate_z_entry, 38, 1)

        # Rapid Feedrate
        self.feedrate_rapid_label = FCLabel('%s:' % _('Feedrate Rapids'))
        self.feedrate_rapid_label.setToolTip(
            _("Tool speed while drilling\n"
              "(in units per minute).\n"
              "This is for the rapid move G00.\n"
              "It is useful only for Marlin,\n"
              "ignore for any other cases.")
        )
        self.feedrate_rapid_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feedrate_rapid_entry.set_precision(self.decimals)
        self.feedrate_rapid_entry.set_range(0.0, 910000.0000)
        self.feedrate_rapid_entry.setSingleStep(0.1)
        self.feedrate_rapid_entry.setObjectName("mill_fr_rapid")

        param_grid.addWidget(self.feedrate_rapid_label, 40, 0)
        param_grid.addWidget(self.feedrate_rapid_entry, 40, 1)

        # default values is to hide
        self.feedrate_rapid_label.hide()
        self.feedrate_rapid_entry.hide()

        # Cut over 1st point in path
        self.extracut_cb = FCCheckBox('%s:' % _('Re-cut'))
        self.extracut_cb.setToolTip(
            _("In order to remove possible\n"
              "copper leftovers where first cut\n"
              "meet with last cut, we generate an\n"
              "extended cut over the first cut section.")
        )
        self.extracut_cb.setObjectName("mill_extracut")

        self.e_cut_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.e_cut_entry.set_range(0, 99999)
        self.e_cut_entry.set_precision(self.decimals)
        self.e_cut_entry.setSingleStep(0.1)
        self.e_cut_entry.setWrapping(True)
        self.e_cut_entry.setToolTip(
            _("In order to remove possible\n"
              "copper leftovers where first cut\n"
              "meet with last cut, we generate an\n"
              "extended cut over the first cut section.")
        )
        self.e_cut_entry.setObjectName("mill_extracut_length")

        self.ois_recut = OptionalInputSection(self.extracut_cb, [self.e_cut_entry])

        self.extracut_cb.hide()
        self.e_cut_entry.hide()

        param_grid.addWidget(self.extracut_cb, 42, 0)
        param_grid.addWidget(self.e_cut_entry, 42, 1)

        # Spindlespeed
        self.spindle_label = FCLabel('%s:' % _('Spindle speed'))
        self.spindle_label.setToolTip(
            _("Speed of the spindle\n"
              "in RPM (optional)")
        )

        self.spindlespeed_entry = FCSpinner(callback=self.confirmation_message_int)
        self.spindlespeed_entry.set_range(0, 1000000)
        self.spindlespeed_entry.set_step(100)
        self.spindlespeed_entry.setObjectName("mill_spindlespeed")

        param_grid.addWidget(self.spindle_label, 44, 0)
        param_grid.addWidget(self.spindlespeed_entry, 44, 1)

        # Laser power minimum
        self.las_min_pwr_label = FCLabel('%s:' % _('Min Power'))
        self.las_min_pwr_label.setToolTip(
            "%s\n%s" % (
                _("The laser power when the laser is travelling."),
                _("Above 0 the laser is not turned off between the cuts:\n"
                  "the 'laser OFF' lines set this power instead.\n"
                  "Keep it 0 to turn the laser off between the cuts.")
            )
        )

        self.las_min_pwr_entry = FCSpinner(callback=self.confirmation_message_int)
        self.las_min_pwr_entry.set_range(0, 1000000)
        self.las_min_pwr_entry.set_step(1)
        self.las_min_pwr_entry.setObjectName("mill_minpower")

        param_grid.addWidget(self.las_min_pwr_label, 46, 0)
        param_grid.addWidget(self.las_min_pwr_entry, 46, 1)
        self.las_min_pwr_label.hide()
        self.las_min_pwr_entry.hide()

        # Laser Turn ON Code
        self.laser_turn_on_lbl = FCLabel('%s:' % _('Turn ON Code'))
        self.laser_turn_on_lbl.setToolTip(
            _("The Gode that will be executed to turn the laser on.")
        )

        self.laser_turn_on_combo = FCComboBox()
        self.laser_turn_on_combo.addItems(["M03", "M04"])
        self.laser_turn_on_combo.setObjectName("mill_laser_turn_on")

        param_grid.addWidget(self.laser_turn_on_lbl, 48, 0)
        param_grid.addWidget(self.laser_turn_on_combo, 48, 1)
        self.laser_turn_on_lbl.hide()
        self.laser_turn_on_combo.hide()

        # Dwell
        self.dwell_cb = FCCheckBox('%s:' % _('Dwell'))
        self.dwell_cb.setToolTip(
            _("Pause to allow the spindle to reach its\n"
              "speed before cutting.")
        )
        self.dwell_cb.setObjectName("mill_dwell")

        self.dwelltime_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.dwelltime_entry.set_precision(self.decimals)
        self.dwelltime_entry.set_range(0.0, 10000.0000)
        self.dwelltime_entry.setSingleStep(0.1)

        self.dwelltime_entry.setToolTip(
            _("Number of time units for spindle to dwell.")
        )
        self.dwelltime_entry.setObjectName("mill_dwelltime")

        param_grid.addWidget(self.dwell_cb, 50, 0)
        param_grid.addWidget(self.dwelltime_entry, 50, 1)

        self.ois_dwell = OptionalInputSection(self.dwell_cb, [self.dwelltime_entry])

        # ##############################################################################################################
        # Apply to All Parameters Button
        # ##############################################################################################################
        self.apply_param_to_all = FCButton(_("Apply parameters to all tools"))
        self.apply_param_to_all.setIcon(QtGui.QIcon(self.app.resource_location + '/param_all32.png'))
        self.apply_param_to_all.setToolTip(
            _("The parameters in the current form will be applied\n"
              "on all the tools from the Tool Table.")
        )
        self.tool_params_box.addWidget(self.apply_param_to_all)

        # #############################################################################################################
        # COMMON PARAMETERS
        # #############################################################################################################
        # General Parameters
        self.gen_param_label = FCLabel('%s' % _("Common Parameters"), color='indigo', bold=True)
        self.gen_param_label.setToolTip(
            _("Parameters that are common for all tools.")
        )
        self.tool_params_box.addWidget(self.gen_param_label)

        gp_frame = FCFrame()
        self.tool_params_box.addWidget(gp_frame)

        gen_grid = GLay(v_spacing=5, h_spacing=3)
        gp_frame.setLayout(gen_grid)

        # Tool change Z:
        self.toolchange_cb = FCCheckBox('%s:' % _("Tool change Z"))
        self.toolchange_cb.setToolTip(
            _("Include tool-change sequence\n"
              "in G-Code (Pause for tool change).")
        )
        self.toolchange_cb.setObjectName("mill_toolchange")

        self.toolchangez_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.toolchangez_entry.set_precision(self.decimals)
        self.toolchangez_entry.setToolTip(
            _("Z-axis position (height) for\n"
              "tool change.")
        )
        self.toolchangez_entry.set_range(-10000.0000, 10000.0000)
        self.toolchangez_entry.setObjectName("mill_toolchangez")

        self.toolchangez_entry.setSingleStep(0.1)

        gen_grid.addWidget(self.toolchange_cb, 0, 0)
        gen_grid.addWidget(self.toolchangez_entry, 0, 1)

        # Tool change X-Y
        self.toolchange_xy_label = FCLabel('%s:' % _('Toolchange X-Y'))
        self.toolchange_xy_label.setToolTip(
            _("Toolchange X,Y position.")
        )
        self.toolchangexy_entry = NumericalEvalTupleEntry(border_color='#0069A9')
        self.toolchangexy_entry.setObjectName("mill_toolchangexy")

        gen_grid.addWidget(self.toolchange_xy_label, 2, 0)
        gen_grid.addWidget(self.toolchangexy_entry, 2, 1)

        self.ois_tcz_e = OptionalInputSection(self.toolchange_cb,
                                              [
                                                  self.toolchangez_entry,
                                                  self.toolchange_xy_label,
                                                  self.toolchangexy_entry
                                              ])

        # End move Z:
        self.endz_label = FCLabel('%s:' % _("End move Z"))
        self.endz_label.setToolTip(
            _("Height of the tool after\n"
              "the last move at the end of the job.")
        )
        self.endz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.endz_entry.set_precision(self.decimals)
        self.endz_entry.set_range(-10000.0000, 10000.0000)
        self.endz_entry.setObjectName("mill_endz")

        self.endz_entry.setSingleStep(0.1)

        gen_grid.addWidget(self.endz_label, 4, 0)
        gen_grid.addWidget(self.endz_entry, 4, 1)

        # End Move X,Y
        self.endmove_xy_label = FCLabel('%s:' % _('End move X,Y'))
        self.endmove_xy_label.setToolTip(
            _("End move X,Y position. In format (x,y).\n"
              "If no value is entered then there is no move\n"
              "on X,Y plane at the end of the job.")
        )
        self.endxy_entry = NumericalEvalTupleEntry(border_color='#0069A9')
        self.endxy_entry.setPlaceholderText(_("X,Y coordinates"))
        self.endxy_entry.setObjectName("mill_endxy")

        gen_grid.addWidget(self.endmove_xy_label, 6, 0)
        gen_grid.addWidget(self.endxy_entry, 6, 1)

        # Probe depth
        self.pdepth_label = FCLabel('%s:' % _("Probe Z depth"))
        self.pdepth_label.setToolTip(
            _("The maximum depth that the probe is allowed\n"
              "to probe. Negative value, in current units.")
        )

        self.pdepth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.pdepth_entry.set_precision(self.decimals)
        self.pdepth_entry.set_range(-10000.0000, 10000.0000)
        self.pdepth_entry.setSingleStep(0.1)
        self.pdepth_entry.setObjectName("mill_depth_probe")

        gen_grid.addWidget(self.pdepth_label, 8, 0)
        gen_grid.addWidget(self.pdepth_entry, 8, 1)

        self.pdepth_label.hide()
        self.pdepth_entry.setVisible(False)

        # Probe plate thickness
        self.probe_plate_label = FCLabel('%s:' % _("Probe plate thickness"))
        self.probe_plate_label.setToolTip(
            _("The thickness of the touch plate that the probe touches on top of the material.\n"
              "After the probing, the tool tip is set to this height, so Z = 0 stays the top of the material.")
        )
        self.probe_plate_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.probe_plate_entry.set_precision(self.decimals)
        self.probe_plate_entry.set_range(0.0, 10000.0000)
        self.probe_plate_entry.setSingleStep(0.1)
        self.probe_plate_entry.setObjectName("mill_probe_plate")

        gen_grid.addWidget(self.probe_plate_label, 9, 0)
        gen_grid.addWidget(self.probe_plate_entry, 9, 1)

        self.probe_plate_label.hide()
        self.probe_plate_entry.setVisible(False)

        # Probe feedrate
        self.feedrate_probe_label = FCLabel('%s:' % _("Feedrate Probe"))
        self.feedrate_probe_label.setToolTip(
            _("The feedrate used while the probe is probing.")
        )

        self.feedrate_probe_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feedrate_probe_entry.set_precision(self.decimals)
        self.feedrate_probe_entry.set_range(0.0, 10000.0000)
        self.feedrate_probe_entry.setSingleStep(0.1)
        self.feedrate_probe_entry.setObjectName("mill_fr_probe")

        gen_grid.addWidget(self.feedrate_probe_label, 10, 0)
        gen_grid.addWidget(self.feedrate_probe_entry, 10, 1)

        self.feedrate_probe_label.hide()
        self.feedrate_probe_entry.setVisible(False)

        # Preprocessor Geometry selection
        pp_geo_label = FCLabel('%s:' % _("Preprocessor"))
        pp_geo_label.setToolTip(
            _("The preprocessor JSON file that dictates\n"
              "Gcode output for Geometry (Milling) Objects.")
        )
        self.pp_geo_name_cb = FCComboBox()
        self.pp_geo_name_cb.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.pp_geo_name_cb.setObjectName("mill_ppname_g")

        gen_grid.addWidget(pp_geo_label, 12, 0)
        gen_grid.addWidget(self.pp_geo_name_cb, 12, 1)

        # Allow Levelling
        self.allow_level_cb = FCCheckBox('%s' % _("Allow levelling"))
        self.allow_level_cb.setToolTip(
            _("Allow levelling by having segments size more than zero.")
        )
        self.allow_level_cb.setObjectName("mill_allow_level")

        gen_grid.addWidget(self.allow_level_cb, 14, 0, 1, 2)

        # Size of trace segment on X axis
        seg_x_label = FCLabel('%s:' % _("Segment X size"))
        seg_x_label.setToolTip(
            _("The size of the trace segment on the X axis.\n"
              "Useful for auto-leveling.\n"
              "A value of 0 means no segmentation on the X axis.")
        )
        self.seg_x_entry = FCDoubleSpinner()
        self.seg_x_entry.set_range(0, 99999)
        self.seg_x_entry.set_precision(self.decimals)
        self.seg_x_entry.setSingleStep(0.1)
        self.seg_x_entry.setWrapping(True)
        self.seg_x_entry.setObjectName("mill_seg_x")

        gen_grid.addWidget(seg_x_label, 16, 0)
        gen_grid.addWidget(self.seg_x_entry, 16, 1)

        # Size of trace segment on Y axis
        seg_y_label = FCLabel('%s:' % _("Segment Y size"))
        seg_y_label.setToolTip(
            _("The size of the trace segment on the Y axis.\n"
              "Useful for auto-leveling.\n"
              "A value of 0 means no segmentation on the Y axis.")
        )
        self.seg_y_entry = FCDoubleSpinner()
        self.seg_y_entry.set_range(0, 99999)
        self.seg_y_entry.set_precision(self.decimals)
        self.seg_y_entry.setSingleStep(0.1)
        self.seg_y_entry.setWrapping(True)
        self.seg_y_entry.setObjectName("mill_seg_y")

        gen_grid.addWidget(seg_y_label, 18, 0)
        gen_grid.addWidget(self.seg_y_entry, 18, 1)

        self.oih = OptionalHideInputSection(self.allow_level_cb,
                                            [seg_x_label, self.seg_x_entry, seg_y_label, self.seg_y_entry])

        # ------------------------------------------------------------------------------------------------------------
        # ------------------------- EXCLUSION AREAS ------------------------------------------------------------------
        # ------------------------------------------------------------------------------------------------------------

        # Exclusion Areas
        self.exclusion_cb = FCCheckBox('%s' % _("Add exclusion areas"))
        self.exclusion_cb.setToolTip(
            _(
                "Include exclusion areas.\n"
                "In those areas the travel of the tools\n"
                "is forbidden."
            )
        )
        self.exclusion_cb.setObjectName("mill_exclusion")

        gen_grid.addWidget(self.exclusion_cb, 20, 0, 1, 2)

        self.exclusion_frame = QtWidgets.QFrame()
        self.exclusion_frame.setContentsMargins(0, 0, 0, 0)
        gen_grid.addWidget(self.exclusion_frame, 22, 0, 1, 2)

        self.exclusion_box = QtWidgets.QVBoxLayout()
        self.exclusion_box.setContentsMargins(0, 0, 0, 0)
        self.exclusion_frame.setLayout(self.exclusion_box)

        self.exclusion_table = FCTable()
        self.exclusion_box.addWidget(self.exclusion_table)
        self.exclusion_table.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)

        self.exclusion_table.setColumnCount(4)
        self.exclusion_table.setColumnWidth(0, 20)
        self.exclusion_table.setHorizontalHeaderLabels(['#', _('Object'), _('Strategy'), _('Over Z')])

        self.exclusion_table.horizontalHeaderItem(0).setToolTip(_("This is the Area ID."))
        self.exclusion_table.horizontalHeaderItem(1).setToolTip(
            _("Type of the object where the exclusion area was added."))
        self.exclusion_table.horizontalHeaderItem(2).setToolTip(
            _("The strategy used for exclusion area. Go around the exclusion areas or over it."))
        self.exclusion_table.horizontalHeaderItem(3).setToolTip(
            _("If the strategy is to go over the area then this is the height at which the tool will go to avoid the "
              "exclusion area."))

        self.exclusion_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        grid_a1 = GLay(v_spacing=5, h_spacing=3)
        self.exclusion_box.addLayout(grid_a1)

        # Chose Strategy
        self.strategy_label = FCLabel('%s:' % _("Strategy"))
        self.strategy_label.setToolTip(_("The strategy followed when encountering an exclusion area.\n"
                                         "Can be:\n"
                                         "- Over -> when encountering the area, the tool will go to a set height\n"
                                         "- Around -> will avoid the exclusion area by going around the area"))
        self.strategy_radio = RadioSet([{'label': _('Over'), 'value': 'over'},
                                        {'label': _('Around'), 'value': 'around'}])
        self.strategy_radio.setObjectName("mill_strategy")

        grid_a1.addWidget(self.strategy_label, 1, 0)
        grid_a1.addWidget(self.strategy_radio, 1, 1)

        # Over Z
        self.over_z_label = FCLabel('%s:' % _("Over Z"))
        self.over_z_label.setToolTip(_("The height Z to which the tool will rise in order to avoid\n"
                                       "an interdiction area."))
        self.over_z_entry = FCDoubleSpinner()
        self.over_z_entry.set_range(-10000.0000, 10000.0000)
        self.over_z_entry.set_precision(self.decimals)
        self.over_z_entry.setObjectName("mill_overz")

        grid_a1.addWidget(self.over_z_label, 2, 0)
        grid_a1.addWidget(self.over_z_entry, 2, 1)

        # Button Add Area
        self.add_area_button = FCButton(_('Add Area:'))
        self.add_area_button.setToolTip(_("Add an Exclusion Area."))

        # Area Selection shape
        self.area_shape_radio = RadioSet([{'label': _("Square"), 'value': 'square'},
                                          {'label': _("Polygon"), 'value': 'polygon'}])
        self.area_shape_radio.setToolTip(
            _("The kind of selection shape used for area selection.")
        )
        self.area_shape_radio.setObjectName("mill_area_shape")

        grid_a1.addWidget(self.add_area_button, 4, 0)
        grid_a1.addWidget(self.area_shape_radio, 4, 1)

        h_lay_1 = QtWidgets.QHBoxLayout()
        self.exclusion_box.addLayout(h_lay_1)

        # Button Delete All Areas
        self.delete_area_button = FCButton(_('Delete All'))
        self.delete_area_button.setToolTip(_("Delete all exclusion areas."))

        # Button Delete Selected Areas
        self.delete_sel_area_button = FCButton(_('Delete Selected'))
        self.delete_sel_area_button.setToolTip(_("Delete all exclusion areas that are selected in the table."))

        h_lay_1.addWidget(self.delete_area_button)
        h_lay_1.addWidget(self.delete_sel_area_button)

        self.ois_exclusion_exc = OptionalHideInputSection(self.exclusion_cb, [self.exclusion_frame])
        # -------------------------- EXCLUSION AREAS END -------------------------------------------------------------
        # ------------------------------------------------------------------------------------------------------------

        # Path optimization (Advanced only; the plugin shows it)
        self.opt_algorithm_label = FCLabel('%s:' % _('Path optimization'))
        self.opt_algorithm_label.setToolTip(
            _("The order in which the paths of a tool are cut.\n"
              "- Rtree -> Rtree algorithm\n"
              "- MetaHeuristic -> Google OR-Tools with Guided Local Search, runs for the search time\n"
              "- Basic -> Google OR-Tools Basic algorithm, runs for the search time\n"
              "- TSA -> Travelling Salesman algorithm\n"
              "- None -> the paths in the order of the geometry\n"
              "\n"
              "MetaHeuristic and Basic are disabled when OR-Tools is not available.")
        )
        self.opt_algorithm_radio = RadioSet(
            [
                {'label': _('Rtree'), 'value': 'R'},
                {'label': _('MetaHeuristic'), 'value': 'M'},
                {'label': _('Basic'), 'value': 'B'},
                {'label': _('TSA'), 'value': 'T'},
                {'label': _('None'), 'value': 'N'}
            ], orientation='vertical', compact=True)
        self.opt_algorithm_radio.setObjectName("mill_opt_type")

        gen_grid.addWidget(self.opt_algorithm_label, 24, 0)
        gen_grid.addWidget(self.opt_algorithm_radio, 24, 1)

        # OR-Tools search time
        self.opt_time_label = FCLabel('%s:' % _('Search time'))
        self.opt_time_label.setToolTip(
            _("OR-Tools MetaHeuristic and Basic stop the path\n"
              "optimization after this time.\n"
              "In seconds. 0 means 3 seconds.")
        )
        self.opt_time_entry = FCSpinner(suffix='s')
        self.opt_time_entry.set_range(0, 999)
        self.opt_time_entry.setObjectName("mill_search_time")

        gen_grid.addWidget(self.opt_time_label, 26, 0)
        gen_grid.addWidget(self.opt_time_entry, 26, 1)

        from camlib import HAS_ORTOOLS
        if not HAS_ORTOOLS:
            self.opt_algorithm_radio.setOptionsDisabled([_('MetaHeuristic'), _('Basic')], True)

        for widget in (self.opt_algorithm_label, self.opt_algorithm_radio, self.opt_time_label, self.opt_time_entry):
            widget.hide()

        GLay.set_common_column_size(
            [obj_grid, tool_title_grid, tool_grid, new_tool_grid, param_grid, gen_grid], 0)

        # #############################################################################################################
        # Generate CNC Job object
        # #############################################################################################################
        self.generate_cnc_button = FCButton(_('Generate CNCJob object'), bold=True)
        self.generate_cnc_button.setIcon(QtGui.QIcon(self.app.resource_location + '/cnc16.png'))
        self.generate_cnc_button.setToolTip(
            _("Generate the CNC Job.\n"
              "If milling then an additional Geometry object will be created.\n"
              "Add / Select at least one tool in the tool-table.\n"
              "Click the # header to select all, or Ctrl + LMB\n"
              "for custom selection of tools.")
        )
        self.tool_params_box.addWidget(self.generate_cnc_button)

        self.tools_box.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.tools_box.addWidget(self.reset_button)
        # ############################ FINSIHED GUI ###################################
        # #############################################################################

    def on_pp_changed(self, current_pp, advanced):
        if "toolchange_probe" in current_pp.lower():
            self.pdepth_entry.setVisible(True)
            self.pdepth_label.show()

            self.probe_plate_entry.setVisible(True)
            self.probe_plate_label.show()

            self.feedrate_probe_entry.setVisible(True)
            self.feedrate_probe_label.show()
        else:
            self.pdepth_entry.setVisible(False)
            self.pdepth_label.hide()

            self.probe_plate_entry.setVisible(False)
            self.probe_plate_label.hide()

            self.feedrate_probe_entry.setVisible(False)
            self.feedrate_probe_label.hide()

        if 'marlin' in current_pp.lower():
            self.feedrate_rapid_label.show()
            self.feedrate_rapid_entry.show()
        else:
            self.feedrate_rapid_label.hide()
            self.feedrate_rapid_entry.hide()

        if 'laser' in current_pp.lower():
            self.cutzlabel.hide()
            self.cutz_entry.hide()

            try:
                self.mpass_cb.hide()
                self.maxdepth_entry.hide()
            except AttributeError:
                pass

            if 'laser_z' in current_pp.lower():
                self.travelzlabel.setText('%s:' % _("Focus Z"))
                self.travelzlabel.show()
                self.travelz_entry.show()

                self.endz_label.show()
                self.endz_entry.show()
            else:
                self.travelzlabel.hide()
                self.travelz_entry.hide()

                self.endz_label.hide()
                self.endz_entry.hide()

                try:
                    self.frzlabel.hide()
                    self.feedrate_z_entry.hide()
                except AttributeError:
                    pass

            self.dwell_cb.hide()
            self.dwelltime_entry.hide()

            self.spindle_label.setText('%s:' % _("Laser Power"))
            self.spindle_label.setToolTip(
                _("The laser power when the laser is cutting.")
            )

            self.las_min_pwr_label.show()
            self.las_min_pwr_entry.show()

            self.laser_turn_on_lbl.show()
            self.laser_turn_on_combo.show()
        else:
            self.cutzlabel.show()
            self.cutz_entry.show()

            # if in Advanced Mode
            if advanced:
                try:
                    self.mpass_cb.show()
                    self.maxdepth_entry.show()
                except AttributeError:
                    pass

            self.travelzlabel.setText('%s:' % _('Travel Z'))
            self.travelzlabel.setToolTip(
                _("Tool height when travelling\n"
                  "across the XY plane.")
            )

            self.travelzlabel.show()
            self.travelz_entry.show()

            self.endz_label.show()
            self.endz_entry.show()

            try:
                self.frzlabel.show()
                self.feedrate_z_entry.show()
            except AttributeError:
                pass

            self.spindle_label.setText('%s:' % _('Spindle speed'))
            self.spindle_label.setToolTip(
                _("Speed of the spindle\n"
                  "in RPM (optional)")
            )

            self.las_min_pwr_label.hide()
            self.las_min_pwr_entry.hide()

            self.laser_turn_on_lbl.hide()
            self.laser_turn_on_combo.hide()

            # if in Advanced Mode
            if advanced:
                self.dwell_cb.show()
                self.dwelltime_entry.show()

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
