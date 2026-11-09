
from PyQt6 import QtWidgets, QtCore, QtGui      # noqa

from appGUI.GUIElements import (
    FCLabel,
    FCFrame,
    GLay,
    RadioSet,
    FCComboBox,
    FCTable,
    FCComboBox2,
    FCDoubleSpinner,
    FCButton,
    FCCheckBox,
    OptionalInputSection,
)

import logging

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class NccUI:

    pluginName = _("NCC")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(
            _("Create a Geometry object with\n"
              "toolpaths to cover the space outside the copper pattern.")
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

        # #############################################################################################################
        # Source Object for Paint Frame
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

        # #############################################################################################################
        # Type of object to be painted
        # #############################################################################################################
        self.type_obj_combo_label = FCLabel('%s:' % _("Type"))
        self.type_obj_combo_label.setToolTip(
            _("Specify the type of object to be cleared of excess copper.\n"
              "It can be of type: Gerber or Geometry.\n"
              "What is selected here will dictate the kind\n"
              "of objects that will populate the 'Object' combobox.")
        )
        self.type_obj_combo_label.setMinimumWidth(60)

        self.type_obj_radio = RadioSet([{'label': _("Geometry"), 'value': 'geometry'},
                                        {'label': _("Gerber"), 'value': 'gerber'}], compact=True)

        obj_grid.addWidget(self.type_obj_combo_label, 0, 0)
        obj_grid.addWidget(self.type_obj_radio, 0, 1)

        # #############################################################################################################
        # The object to be copper cleared
        # #############################################################################################################
        self.obj_combo = FCComboBox()
        self.obj_combo.setModel(self.app.collection)
        self.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.obj_combo.is_last = True

        obj_grid.addWidget(self.obj_combo, 2, 0, 1, 2)

        self.multi_cb = FCCheckBox(_("All selected objects"))
        self.multi_cb.setObjectName("n_multi_objects")
        self.multi_cb.setToolTip(
            _("When checked, every Gerber or Geometry object selected in the Project tab\n"
              "is cleared with the same tools and parameters, each into its own new object.\n"
              "Area Selection works with one object only.\n"
              "When unchecked, only the object chosen above is cleared.")
        )
        obj_grid.addWidget(self.multi_cb, 3, 0, 1, 2)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # obj_grid.addWidget(separator_line, 4, 0, 1, 2)

        # #############################################################################################################
        # Tool Table Frame
        # #############################################################################################################
        # ### Tools ## ##
        self.tools_table_label = FCLabel('%s' % _("Tools Table"), color='green', bold=True)
        self.tools_table_label.setToolTip(
            _("Tools pool from which the algorithm\n"
              "will pick the ones used for copper clearing.")
        )
        self.tools_box.addWidget(self.tools_table_label)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        tool_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(tool_grid)

        # Tools Table
        self.tools_table = FCTable(drag_drop=True)
        # self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        tool_grid.addWidget(self.tools_table, 0, 0, 1, 2)

        self.tools_table.setColumnCount(4)
        # 3rd column is reserved (and hidden) for the tool ID
        self.tools_table.setHorizontalHeaderLabels(['#', _('Diameter'), _('Shape'), ''])
        self.tools_table.setColumnHidden(3, True)
        self.tools_table.setSortingEnabled(False)
        # self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        self.tools_table.horizontalHeaderItem(0).setToolTip(
            _("This is the Tool Number.\n"
              "Non copper clearing will start with the tool with the biggest \n"
              "diameter, continuing until there are no more tools.\n"
              "Only tools that create NCC clearing geometry will still be present\n"
              "in the resulting geometry. This is because with some tools\n"
              "this function will not be able to create painting geometry.")
        )
        self.tools_table.horizontalHeaderItem(1).setToolTip(
            _("Tool Diameter. Its value\n"
              "is the cut width into the material."))

        self.tools_table.horizontalHeaderItem(2).setToolTip(
            _("Tool Shape. \n"
              "Can be:\n"
              "C1 ... C4 = circular tool with x flutes\n"
              "B = ball tip milling tool\n"
              "V = v-shape milling tool\n"
              "L = laser"))

        # Tool order
        self.order_label = FCLabel('%s:' % _('Tool order'))
        self.order_label.setToolTip(
            _("This set the way that the tools in the tools table are used.\n"
              "'Default' --> means that the used order is the one in the tool table\n"
              "'Forward' --> means that the tools will be ordered from small to big\n"
              "'Reverse' --> means that the tools will ordered from big to small\n\n"
              "WARNING: using rest machining will automatically set the order\n"
              "in reverse and disable this control.")
        )

        # self.order_combo = RadioSet([{'label': _('No'), 'value': 'no'},
        #                              {'label': _('Forward'), 'value': 'fwd'},
        #                              {'label': _('Reverse'), 'value': 'rev'}])
        self.order_combo = FCComboBox2()
        self.order_combo.addItems([_('Default'), _('Forward'), _('Reverse')])

        tool_grid.addWidget(self.order_label, 4, 0)
        tool_grid.addWidget(self.order_combo, 4, 1)

        # ##############################################################################
        # ###################### ADD A NEW TOOL ########################################
        # ##############################################################################
        self.add_tool_frame = QtWidgets.QFrame()
        self.add_tool_frame.setContentsMargins(0, 0, 0, 0)
        tool_grid.addWidget(self.add_tool_frame, 6, 0, 1, 2)

        new_tool_grid = GLay(v_spacing=5, h_spacing=3)
        new_tool_grid.setContentsMargins(0, 0, 0, 0)
        self.add_tool_frame.setLayout(new_tool_grid)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        new_tool_grid.addWidget(separator_line, 0, 0, 1, 3)

        # #############################################################
        # ############### Tool selection ##############################
        # #############################################################
        self.tool_sel_label = FCLabel('%s' % _('Add from DB'), bold=True)
        new_tool_grid.addWidget(self.tool_sel_label, 2, 0, 1, 3)

        # ### Tool Diameter ####
        self.new_tooldia_lbl = FCLabel('%s:' % _('Tool Dia'))
        self.new_tooldia_lbl.setToolTip(
            _("Diameter for the new tool")
        )
        new_tool_grid.addWidget(self.new_tooldia_lbl, 4, 0)

        # nt_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[1, 0])
        # nt_grid.setContentsMargins(0, 0, 0, 0)
        # new_tool_grid.addLayout(nt_grid, 4, 1)

        self.new_tooldia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.new_tooldia_entry.set_precision(self.decimals)
        self.new_tooldia_entry.set_range(-10000.0000, 10000.0000)
        self.new_tooldia_entry.setObjectName(_("Tool Dia"))

        new_tool_grid.addWidget(self.new_tooldia_entry, 4, 1)

        # Find Optimal Tooldia
        self.find_optimal_button = QtWidgets.QToolButton()
        self.find_optimal_button.setText(_('Optimal'))
        self.find_optimal_button.setIcon(QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'))
        self.find_optimal_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.find_optimal_button.setToolTip(
            _("Find a tool diameter that is guaranteed\n"
              "to do a complete isolation.")
        )
        new_tool_grid.addWidget(self.find_optimal_button, 4, 2)

        # #############################################################################################################
        # ################################    Button Grid   ###########################################################
        # #############################################################################################################
        button_grid = GLay(v_spacing=5, h_spacing=3)
        button_grid.setColumnStretch(0, 1)
        button_grid.setColumnStretch(1, 0)
        new_tool_grid.addLayout(button_grid, 6, 0, 1, 3)

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
        # Parameters Frame
        # #############################################################################################################
        self.tool_data_label = FCLabel(
            "<b>%s: <font color='#0000FF'>%s %d</font></b>" % (_('Parameters for'), _("Tool"), int(1)))
        self.tool_data_label.setToolTip(
            _("The data used for creating GCode.\n"
              "Each tool store it's own set of such data.")
        )
        self.tools_box.addWidget(self.tool_data_label)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        par_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(par_grid)

        # Operation
        self.op_label = FCLabel('%s:' % _('Operation'))
        self.op_label.setToolTip(
            _("The 'Operation' can be:\n"
              "- Isolation -> will ensure that the non-copper clearing is always complete.\n"
              "If it's not successful then the non-copper clearing will fail, too.\n"
              "- Clear -> the regular non-copper clearing.")
        )

        self.op_radio = RadioSet([
            {"label": _("Clear"), "value": "clear"},
            {"label": _("Isolation"), "value": "iso"}
        ], orientation='horizontal', compact=True)
        self.op_radio.setObjectName("n_operation")

        par_grid.addWidget(self.op_label, 0, 0)
        par_grid.addWidget(self.op_radio, 0, 1)

        # Milling Type Radio Button
        self.milling_type_label = FCLabel('%s:' % _('Milling Type'))
        self.milling_type_label.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )

        self.milling_type_radio = RadioSet([{'label': _('Climb'), 'value': 'cl'},
                                            {'label': _('Conventional'), 'value': 'cv'}], compact=True)
        self.milling_type_radio.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )
        self.milling_type_radio.setObjectName("n_milling_type")

        self.milling_type_label.setEnabled(False)
        self.milling_type_radio.setEnabled(False)

        par_grid.addWidget(self.milling_type_label, 2, 0)
        par_grid.addWidget(self.milling_type_radio, 2, 1)

        # Overlap Entry
        self.overlap_label = FCLabel('%s:' % _('Overlap'))
        self.overlap_label.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.\n"
              "Adjust the value starting with lower values\n"
              "and increasing it if areas that should be processed are still \n"
              "not processed.\n"
              "Lower values = faster processing, faster execution on CNC.\n"
              "Higher values = slow processing and slow execution on CNC\n"
              "due of too many paths.")
        )
        self.overlap_entry = FCDoubleSpinner(callback=self.confirmation_message, suffix='%')
        self.overlap_entry.set_precision(self.decimals)
        self.overlap_entry.setWrapping(True)
        self.overlap_entry.setRange(0.000, 99.9999)
        self.overlap_entry.setSingleStep(0.1)
        self.overlap_entry.setObjectName("n_overlap")

        par_grid.addWidget(self.overlap_label, 4, 0)
        par_grid.addWidget(self.overlap_entry, 4, 1)

        # Method
        self.method_label = FCLabel('%s:' % _('Method'))
        self.method_label.setToolTip(
            _("Algorithm for copper clearing:\n"
              "- Standard: Fixed step inwards.\n"
              "- Seed-based: Outwards from seed.\n"
              "- Line-based: Parallel lines.")
        )
        # self.method_radio = RadioSet([
        #     {"label": _("Standard"), "value": "standard"},
        #     {"label": _("Seed-based"), "value": "seed"},
        #     {"label": _("Straight lines"), "value": "lines"}
        # ], orientation='vertical', compact=True)

        self.method_combo = FCComboBox2()
        self.method_combo.addItems(
            [_("Standard"), _("Seed"), _("Lines"), _("Combo"), _("Crosshatch")]
        )
        self.method_combo.setObjectName("n_method")

        par_grid.addWidget(self.method_label, 6, 0)
        par_grid.addWidget(self.method_combo, 6, 1)

        # Crosshatch angle
        self.crosshatch_angle_label = FCLabel('%s:' % _('Angle'))
        self.crosshatch_angle_label.setToolTip(
            _("Direction of the first family of lines of the Crosshatch method, in degrees.\n"
              "The second family is at 90 degrees from it.")
        )
        self.crosshatch_angle_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.crosshatch_angle_entry.set_precision(self.decimals)
        self.crosshatch_angle_entry.setWrapping(True)
        self.crosshatch_angle_entry.setRange(0.0000, 179.9999)
        self.crosshatch_angle_entry.setSingleStep(45)
        self.crosshatch_angle_entry.setObjectName("n_crosshatch_angle")

        par_grid.addWidget(self.crosshatch_angle_label, 7, 0)
        par_grid.addWidget(self.crosshatch_angle_entry, 7, 1)

        # Margin
        self.margin_label = FCLabel('%s:' % _('Margin'))
        self.margin_label.setToolTip(
            _("Bounding box margin.")
        )
        self.margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.margin_entry.set_precision(self.decimals)
        self.margin_entry.set_range(-10000.0000, 10000.0000)
        self.margin_entry.setObjectName("n_margin")

        par_grid.addWidget(self.margin_label, 8, 0)
        par_grid.addWidget(self.margin_entry, 8, 1)

        # Connect lines
        self.connect_cb = FCCheckBox('%s' % _("Connect"))
        self.connect_cb.setObjectName("n_connect")

        self.connect_cb.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )
        par_grid.addWidget(self.connect_cb, 10, 0)

        # Contour
        self.contour_cb = FCCheckBox('%s' % _("Contour"))
        self.contour_cb.setObjectName("n_contour")

        self.contour_cb.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.")
        )
        par_grid.addWidget(self.contour_cb, 10, 1)

        self.final_cleanup_cb = FCCheckBox(_("Final cleanup"))
        self.final_cleanup_cb.setObjectName("n_final_cleanup")
        self.final_cleanup_cb.setToolTip(
            _("After clearing, use the smallest selected tool with Final cleanup enabled\n"
              "to remove reachable residual copper. May cross cleared areas, but preserves\n"
              "copper clearance and the clearing boundary. Default: Off.")
        )
        par_grid.addWidget(self.final_cleanup_cb, 11, 0, 1, 2)

        # ## Offset choice
        self.offset_choice_cb = FCCheckBox('%s' % _("Offset"))
        self.offset_choice_cb.setObjectName("n_offset")

        self.offset_choice_cb.setToolTip(
            _("If used, it will add an offset to the copper features.\n"
              "The copper clearing will finish to a distance\n"
              "from the copper features.")
        )
        par_grid.addWidget(self.offset_choice_cb, 12, 0)

        # ## Offset Entry
        self.offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.offset_entry.set_range(0.00, 10.00)
        self.offset_entry.set_precision(4)
        self.offset_entry.setWrapping(True)
        self.offset_entry.setObjectName("n_offset_value")

        units = self.app.app_units.upper()
        if units == 'MM':
            self.offset_entry.setSingleStep(0.1)
        else:
            self.offset_entry.setSingleStep(0.01)

        par_grid.addWidget(self.offset_entry, 12, 1)

        self.ois_offset = OptionalInputSection(self.offset_choice_cb, [self.offset_entry])

        # #############################################################################################################
        # Apply All Parameters Button
        # #############################################################################################################
        self.apply_param_to_all = FCButton(_("Apply parameters to all tools"))
        self.apply_param_to_all.setIcon(QtGui.QIcon(self.app.resource_location + '/param_all32.png'))
        self.apply_param_to_all.setToolTip(
            _("The parameters in the current form will be applied\n"
              "on all the tools from the Tool Table.")
        )
        self.tools_box.addWidget(self.apply_param_to_all)

        # #############################################################################################################
        # General Parameters Frame
        # #############################################################################################################
        # General Parameters
        self.gen_param_label = FCLabel('%s' % _("Common Parameters"), color='indigo', bold=True)
        self.gen_param_label.setToolTip(
            _("Parameters that are common for all tools.")
        )
        self.tools_box.addWidget(self.gen_param_label)

        gp_frame = FCFrame()
        self.tools_box.addWidget(gp_frame)

        gen_grid = GLay(v_spacing=5, h_spacing=3)
        gp_frame.setLayout(gen_grid)

        # the order that was set before Rest Machining forced "Reverse"
        self.order_before_rest = None

        # Rest Machining
        self.rest_cb = FCCheckBox('%s' % _("Rest Machining"))
        self.rest_cb.setObjectName("n_rest_machining")

        self.rest_cb.setToolTip(
            "%s\n%s" % (
                _("If checked, use 'rest machining'.\n"
                  "Copper features will be processed starting with the biggest selected tool.\n"
                  "What cannot be processed will be passed to the next smaller tool and so on,\n"
                  "until either there are no longer selected tools or all the copper features are processed."),
                _("Only tools selected for copper clearing will be used.")
            )
        )

        gen_grid.addWidget(self.rest_cb, 0, 0, 1, 2)

        # Rest Margin
        self.rest_margin_label = FCLabel('%s:' % _('Margin'))
        self.rest_margin_label.setToolTip(
            _("Bounding box margin.")
        )
        self.rest_margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.rest_margin_entry.set_precision(self.decimals)
        self.rest_margin_entry.set_range(-10000.0000, 10000.0000)
        self.rest_margin_entry.setObjectName("n_rest_margin")

        gen_grid.addWidget(self.rest_margin_label, 2, 0)
        gen_grid.addWidget(self.rest_margin_entry, 2, 1)

        # Rest Connect lines
        self.rest_connect_cb = FCCheckBox('%s' % _("Connect"))
        self.rest_connect_cb.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )
        gen_grid.addWidget(self.rest_connect_cb, 4, 0)

        # Rest Contour
        self.rest_contour_cb = FCCheckBox('%s' % _("Contour"))
        self.rest_contour_cb.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.")
        )
        gen_grid.addWidget(self.rest_contour_cb, 4, 1)

        # ## Rest Offset choice
        self.rest_offset_choice_cb = FCCheckBox('%s' % _("Offset"))
        self.rest_offset_choice_cb.setToolTip(
            _("If used, it will add an offset to the copper features.\n"
              "The copper clearing will finish to a distance\n"
              "from the copper features.")
        )
        gen_grid.addWidget(self.rest_offset_choice_cb, 6, 0)

        # ## Rest Offset Entry
        self.rest_offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.rest_offset_entry.set_range(0.00, 10.00)
        self.rest_offset_entry.set_precision(4)
        self.rest_offset_entry.setWrapping(True)

        units = self.app.app_units.upper()
        if units == 'MM':
            self.rest_offset_entry.setSingleStep(0.1)
        else:
            self.rest_offset_entry.setSingleStep(0.01)

        gen_grid.addWidget(self.rest_offset_entry, 6, 1)

        self.rest_ois_offset = OptionalInputSection(self.rest_offset_choice_cb, [self.rest_offset_entry])

        # Reference Selection Combo
        self.select_method_combo = FCComboBox2()
        self.select_method_combo.addItems(
            [_("Itself"), _("Area Selection"), _("Reference Object")]
        )
        self.select_method_combo.setObjectName("n_selection")

        self.select_method_label = FCLabel('%s:' % _("Selection"))
        self.select_method_label.setToolTip(
            _("Selection of area to be processed.\n"
              "- 'Itself' - the processing extent is based on the object that is processed.\n "
              "- 'Area Selection' - left mouse click to start selection of the area to be processed.\n"
              "- 'Reference Object' - will process the area specified by another object.")
        )
        gen_grid.addWidget(self.select_method_label, 8, 0)
        gen_grid.addWidget(self.select_method_combo, 8, 1)

        # Reference Type
        self.reference_type_label = FCLabel('%s:' % _("Type"))
        self.reference_type_label.setToolTip(
            _("The type of FlatCAM object to be used as non copper clearing reference.\n"
              "It can be Gerber, Excellon or Geometry.")
        )
        self.reference_type_combo = FCComboBox2()
        self.reference_type_combo.addItems([_("Gerber"), _("Excellon"), _("Geometry")])

        gen_grid.addWidget(self.reference_type_label, 10, 0)
        gen_grid.addWidget(self.reference_type_combo, 10, 1)

        self.reference_combo = FCComboBox()
        self.reference_combo.setModel(self.app.collection)
        self.reference_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.reference_combo.is_last = True

        gen_grid.addWidget(self.reference_combo, 12, 0, 1, 2)

        self.reference_combo.hide()
        self.reference_type_combo.hide()
        self.reference_type_label.hide()

        # Area Selection shape
        self.area_shape_label = FCLabel('%s:' % _("Shape"))
        self.area_shape_label.setToolTip(
            _("The kind of selection shape used for area selection.")
        )

        self.area_shape_radio = RadioSet([{'label': _("Square"), 'value': 'square'},
                                          {'label': _("Polygon"), 'value': 'polygon'}], compact=True)

        gen_grid.addWidget(self.area_shape_label, 14, 0)
        gen_grid.addWidget(self.area_shape_radio, 14, 1)

        self.area_shape_label.hide()
        self.area_shape_radio.hide()

        # Check Tool validity
        self.valid_cb = FCCheckBox(label=_('Check validity'))
        self.valid_cb.setToolTip(
            _("If checked then the tools diameters are verified\n"
              "if they will provide a complete isolation.")
        )
        self.valid_cb.setObjectName("n_check")

        gen_grid.addWidget(self.valid_cb, 16, 0, 1, 2)

        # #############################################################################################################
        # Exclusion areas Frame
        # #############################################################################################################
        self.excl_label = FCLabel('%s' % _("Exclusion areas"), color='red', bold=True)
        self.excl_label.setToolTip(
            _("Zones drawn on the canvas that are left out of the copper clearing.")
        )
        self.tools_box.addWidget(self.excl_label)

        excl_frame = FCFrame()
        self.tools_box.addWidget(excl_frame)

        excl_grid = GLay(v_spacing=5, h_spacing=3)
        excl_frame.setLayout(excl_grid)

        self.excl_cb = FCCheckBox(_("Use exclusion areas"))
        self.excl_cb.setObjectName("n_exclusion")
        self.excl_cb.setToolTip(
            _("When checked, the zones below are removed from the area to clear,\n"
              "for the Generate and the Preview buttons.")
        )
        excl_grid.addWidget(self.excl_cb, 0, 0, 1, 2)

        self.excl_shape_label = FCLabel('%s:' % _("Shape"))
        self.excl_shape_label.setToolTip(_("The kind of shape used to draw an exclusion zone."))
        self.excl_shape_radio = RadioSet([{'label': _("Square"), 'value': 'square'},
                                          {'label': _("Polygon"), 'value': 'polygon'}], compact=True)
        excl_grid.addWidget(self.excl_shape_label, 2, 0)
        excl_grid.addWidget(self.excl_shape_radio, 2, 1)

        self.excl_count_label = FCLabel('%s:' % _("Zones"))
        self.excl_count_label.setToolTip(_("The number of exclusion zones drawn."))
        self.excl_count_value = FCLabel('0')
        excl_grid.addWidget(self.excl_count_label, 4, 0)
        excl_grid.addWidget(self.excl_count_value, 4, 1)

        self.excl_add_button = FCButton(_("Add area"))
        self.excl_add_button.setToolTip(
            _("Draw exclusion zones on the canvas.\n"
              "Left click to draw, right click or Escape to finish.")
        )
        self.excl_delete_button = FCButton(_("Delete all"))
        self.excl_delete_button.setToolTip(_("Remove all the exclusion zones."))
        excl_grid.addWidget(self.excl_add_button, 6, 0)
        excl_grid.addWidget(self.excl_delete_button, 6, 1)

        GLay.set_common_column_size([obj_grid, tool_grid, new_tool_grid, par_grid, gen_grid, excl_grid], 0)

        # #############################################################################################################
        # Preview Button
        self.preview_button = FCButton(_("Preview"), bold=True)
        self.preview_button.setToolTip(
            _("Draw the area that would be cleared on the canvas.\n"
              "Nothing is created. For Area Selection the zones drawn by the last Generate are used.")
        )
        self.tools_box.addWidget(self.preview_button)

        # #############################################################################################################
        # Generate NCC Geometry Button
        self.generate_button = FCButton(_('Generate Geometry'), bold=True)
        self.generate_button.setIcon(QtGui.QIcon(self.app.resource_location + '/geometry32.png'))
        self.generate_button.setToolTip(
            _("Create the Geometry Object\n"
              "for non-copper routing.")
        )
        self.tools_box.addWidget(self.generate_button)

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

    def parameters_ui(self, val):
        if val == 'iso':
            self.milling_type_label.setEnabled(True)
            self.milling_type_radio.setEnabled(True)

            self.overlap_label.setEnabled(False)
            self.overlap_entry.setEnabled(False)
            self.method_label.setEnabled(False)
            self.method_combo.setEnabled(False)
            self.crosshatch_angle_label.setEnabled(False)
            self.crosshatch_angle_entry.setEnabled(False)
            self.margin_label.setEnabled(False)
            self.margin_entry.setEnabled(False)
            self.connect_cb.setEnabled(False)
            self.contour_cb.setEnabled(False)
            self.final_cleanup_cb.setEnabled(False)
            self.offset_choice_cb.setEnabled(False)
            self.offset_entry.setEnabled(False)
        else:
            self.milling_type_label.setEnabled(False)
            self.milling_type_radio.setEnabled(False)

            self.overlap_label.setEnabled(True)
            self.overlap_entry.setEnabled(True)
            self.method_label.setEnabled(True)
            self.method_combo.setEnabled(True)
            self.margin_label.setEnabled(True)
            self.margin_entry.setEnabled(True)
            self.connect_cb.setEnabled(True)
            self.contour_cb.setEnabled(True)
            self.final_cleanup_cb.setEnabled(True)
            self.offset_choice_cb.setEnabled(True)
            self.offset_entry.setEnabled(True)
            self.on_method_changed()

    def on_method_changed(self, val=None):
        enabled = self.method_combo.get_value() == 4 and self.op_radio.get_value() != 'iso'
        self.crosshatch_angle_label.setEnabled(enabled)
        self.crosshatch_angle_entry.setEnabled(enabled)

    def on_selection(self):
        sel_combo = self.select_method_combo.get_value()

        if sel_combo == 0:  # itself
            self.reference_combo.hide()
            self.reference_type_combo.hide()
            self.reference_type_label.hide()
            self.area_shape_label.hide()
            self.area_shape_radio.hide()

            # disable rest-machining for area painting
            self.rest_cb.setDisabled(False)
        elif sel_combo == 1:    # area selection
            self.reference_combo.hide()
            self.reference_type_combo.hide()
            self.reference_type_label.hide()
            self.area_shape_label.show()
            self.area_shape_radio.show()

            # disable rest-machining for area painting
            # self.rest_cb.set_value(False)
            # self.rest_cb.setDisabled(True)
        else:
            self.reference_combo.show()
            self.reference_type_combo.show()
            self.reference_type_label.show()
            self.area_shape_label.hide()
            self.area_shape_radio.hide()

            # disable rest-machining for area painting
            self.rest_cb.setDisabled(False)

    def on_rest_machining_check(self, state):
        if state:
            # rest machining goes from the biggest tool down: remember the order to restore it when unchecked
            if self.order_combo.isEnabled():
                self.order_before_rest = self.order_combo.get_value()
            self.order_combo.set_value(2)   # "Reverse"
            self.order_label.setDisabled(True)
            self.order_combo.setDisabled(True)

            self.margin_label.hide()
            self.margin_entry.hide()
            self.connect_cb.hide()
            self.contour_cb.hide()
            self.offset_choice_cb.hide()
            self.offset_entry.hide()

            self.rest_margin_label.show()
            self.rest_margin_entry.show()
            self.rest_connect_cb.show()
            self.rest_contour_cb.show()
            self.rest_offset_choice_cb.show()
            self.rest_offset_entry.show()

        else:
            self.order_label.setDisabled(False)
            self.order_combo.setDisabled(False)
            if self.order_before_rest is not None:
                self.order_combo.set_value(self.order_before_rest)
                self.order_before_rest = None

            self.margin_label.show()
            self.margin_entry.show()
            self.connect_cb.show()
            self.contour_cb.show()
            self.offset_choice_cb.show()
            self.offset_entry.show()

            self.rest_margin_label.hide()
            self.rest_margin_entry.hide()
            self.rest_connect_cb.hide()
            self.rest_contour_cb.hide()
            self.rest_offset_choice_cb.hide()
            self.rest_offset_entry.hide()

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
