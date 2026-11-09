
from PyQt6 import QtWidgets, QtCore, QtGui      # noqa
from PyQt6.QtCore import Qt     # noqa

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
    FCCheckBox
)

import logging

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class PaintUI:
    """
    UI class for the Paint plugin in FlatCAM.

    This class handles the user interface for creating toolpaths that cover only the copper pattern.
    It provides controls for tool selection, painting parameters, and execution of the paint operation.

    Attributes:
        pluginName (str): The display name of the plugin
        app: The main application instance
        decimals (int): Number of decimal places for numeric values
        layout: The main layout where the UI is built
        tools_frame: Main frame containing all UI elements
        tools_box: Vertical layout box for organizing UI components
        title_box: Horizontal layout for the title section
    """
    pluginName = _("Paint")

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
            _("Create a Geometry object with toolpaths\n"
              "that cover only the copper pattern.")
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
            _("Specify the type of object to be painted.\n"
              "It can be of type: Gerber or Geometry.\n"
              "What is selected here will dictate the kind\n"
              "of objects that will populate the 'Object' combobox.")
        )
        self.type_obj_combo_label.setMinimumWidth(60)

        self.type_obj_radio = RadioSet([{'label': "Geometry", 'value': 'geometry'},
                                        {'label': "Gerber", 'value': 'gerber'}], compact=True)

        obj_grid.addWidget(self.type_obj_combo_label, 0, 0)
        obj_grid.addWidget(self.type_obj_radio, 0, 1)

        # #############################################################################################################
        # The object to be painted
        # #############################################################################################################
        self.obj_combo = FCComboBox()
        self.obj_combo.setModel(self.app.collection)
        self.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.obj_combo.is_last = False

        obj_grid.addWidget(self.obj_combo, 2, 0, 1, 2)

        # Paint all the Geometry / Gerber objects selected in the Project tab
        self.multi_objects_cb = FCCheckBox(_("Selected objects"))
        self.multi_objects_cb.setObjectName("p_multi_objects")
        self.multi_objects_cb.setToolTip(
            _("Paint all the Geometry and Gerber objects selected in the Project tab, each with the same tools.\n"
              "Not used by 'Polygon Selection', which paints the object in the combobox.")
        )
        obj_grid.addWidget(self.multi_objects_cb, 4, 0, 1, 2)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # obj_grid.addWidget(separator_line, 5, 0, 1, 2)

        # #############################################################################################################
        # Tool Table Frame
        # #############################################################################################################
        # ### Tools ## ##
        self.tools_table_label = FCLabel('%s' % _("Tools Table"), color='green', bold=True)
        self.tools_table_label.setToolTip(
            _("Tools pool from which the algorithm\n"
              "will pick the ones used for painting.")
        )
        self.tools_box.addWidget(self.tools_table_label)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        # Grid Layout
        tool_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(tool_grid)

        self.tools_table = FCTable(drag_drop=True)
        # self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        tool_grid.addWidget(self.tools_table, 2, 0, 1, 2)

        self.tools_table.setColumnCount(4)
        self.tools_table.setHorizontalHeaderLabels(['#', _('Diameter'), _('Shape'), ''])
        self.tools_table.setColumnHidden(3, True)
        # self.tools_table.setSortingEnabled(False)
        # self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        self.tools_table.horizontalHeaderItem(0).setToolTip(
            _("This is the Tool Number.\n"
              "Painting will start with the tool with the biggest diameter,\n"
              "continuing until there are no more tools.\n"
              "Only tools that create painting geometry will still be present\n"
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

        # Tool Order
        self.order_label = FCLabel('%s:' % _('Tool order'), bold=True)
        self.order_label.setToolTip(_("This set the way that the tools in the tools table are used.\n"
                                      "'Default' --> means that the used order is the one in the tool table\n"
                                      "'Forward' --> means that the tools will be ordered from small to big\n"
                                      "'Reverse' --> means that the tools will ordered from big to small\n\n"
                                      "WARNING: using rest machining will automatically set the order\n"
                                      "in reverse and disable this control."))

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
        new_tool_grid.addWidget(separator_line, 0, 0, 1, 2)

        self.tool_sel_label = FCLabel('%s' % _('Add from DB'), bold=True)
        new_tool_grid.addWidget(self.tool_sel_label, 2, 0, 1, 2)

        # ### Tool Diameter ####
        self.new_tooldia_lbl = FCLabel('%s:' % _('Tool Dia'))
        self.new_tooldia_lbl.setToolTip(
            _("Diameter for the new tool to add in the Tool Table.\n"
              "If the tool is V-shape type then this value is automatically\n"
              "calculated from the other parameters.")
        )
        self.new_tooldia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.new_tooldia_entry.set_precision(self.decimals)
        self.new_tooldia_entry.set_range(0.0001, 10000.0000)
        self.new_tooldia_entry.setObjectName('p_tool_dia')

        new_tool_grid.addWidget(self.new_tooldia_lbl, 4, 0)
        new_tool_grid.addWidget(self.new_tooldia_entry, 4, 1)

        # #############################################################################################################
        # ################################    Button Grid   ###########################################################
        # #############################################################################################################
        button_grid = GLay(v_spacing=5, h_spacing=3)
        button_grid.setColumnStretch(0, 1)
        button_grid.setColumnStretch(1, 0)
        new_tool_grid.addLayout(button_grid, 6, 0, 1, 2)

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
            _(
                "The data used for creating GCode.\n"
                "Each tool store it's own set of such data."
            )
        )
        self.tools_box.addWidget(self.tool_data_label)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        param_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(param_grid)

        # Overlap
        ovlabel = FCLabel('%s:' % _('Overlap'))
        ovlabel.setToolTip(
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
        self.overlap_entry.setObjectName('p_overlap')

        param_grid.addWidget(ovlabel, 0, 0)
        param_grid.addWidget(self.overlap_entry, 0, 1)

        # Offset
        self.offset_label = FCLabel('%s:' % _('Offset'))
        self.offset_label.setToolTip(
            _("Distance by which to avoid\n"
              "the edges of the polygon to\n"
              "be painted.")
        )
        self.offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.offset_entry.set_precision(self.decimals)
        self.offset_entry.set_range(-10000.0000, 10000.0000)
        self.offset_entry.setObjectName('p_offset')

        param_grid.addWidget(self.offset_label, 2, 0)
        param_grid.addWidget(self.offset_entry, 2, 1)

        # Method
        methodlabel = FCLabel('%s:' % _('Method'))
        methodlabel.setToolTip(
            _("Algorithm for painting:\n"
              "- Standard: Fixed step inwards.\n"
              "- Seed-based: Outwards from seed.\n"
              "- Line-based: Parallel lines.\n"
              "- Laser-lines: Active only for Gerber objects.\n"
              "Will create lines that follow the traces.\n"
              "- Combo: In case of failure a new method will be picked from the above\n"
              "in the order specified.\n"
              "- Crosshatch: Two perpendicular families of parallel lines.")
        )

        self.method_combo = FCComboBox2()
        self.method_combo.addItems(
            [_("Standard"), _("Seed"), _("Lines"), _("Laser_lines"), _("Combo"), _("Crosshatch")]
        )
        idx = self.method_combo.findText(_("Laser_lines"))
        self.method_combo.model().item(idx).setEnabled(False)  # type: ignore[union-attr]

        self.method_combo.setObjectName('p_method')

        param_grid.addWidget(methodlabel, 4, 0)
        param_grid.addWidget(self.method_combo, 4, 1)

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
        self.crosshatch_angle_entry.setObjectName('p_crosshatch_angle')

        param_grid.addWidget(self.crosshatch_angle_label, 5, 0)
        param_grid.addWidget(self.crosshatch_angle_entry, 5, 1)

        # Connect lines
        self.connect_cb = FCCheckBox('%s' % _("Connect"))
        self.connect_cb.setObjectName('p_connect')
        self.connect_cb.setToolTip(
            _("Draw lines between resulting\n"
              "segments to minimize tool lifts.")
        )

        self.contour_cb = FCCheckBox('%s' % _("Contour"))
        self.contour_cb.setObjectName('p_contour')
        self.contour_cb.setToolTip(
            _("Cut around the perimeter of the polygon\n"
              "to trim rough edges.")
        )

        param_grid.addWidget(self.connect_cb, 6, 0)
        param_grid.addWidget(self.contour_cb, 6, 1)

        self.final_cleanup_cb = FCCheckBox(_("Final paint"))
        self.final_cleanup_cb.setObjectName('p_final_cleanup')
        self.final_cleanup_cb.setToolTip(_(
            "After painting, use the smallest selected tool with Final paint enabled\n"
            "to paint reachable gaps. May overlap previous passes, but keeps the whole\n"
            "tool inside the selected boundary and margins. Default: Off."))
        param_grid.addWidget(self.final_cleanup_cb, 7, 0, 1, 2)

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
        self.gen_param_label.setToolTip(_("Parameters that are common for all tools."))
        self.tools_box.addWidget(self.gen_param_label)

        gp_frame = FCFrame()
        self.tools_box.addWidget(gp_frame)

        gen_grid = GLay(v_spacing=5, h_spacing=3)
        gp_frame.setLayout(gen_grid)

        # Rest machining
        self.rest_cb = FCCheckBox('%s' % _("Rest Machining"))
        self.rest_cb.setObjectName('p_rest_machining')
        self.rest_cb.setToolTip(
            _("If checked, use 'rest machining'.\n"
              "Copper features will be processed starting with the biggest selected tool.\n"
              "What cannot be processed will be passed to the next bigger tool and so on,\n"
              "until either there are no longer selected tools or all the copper features are processed.")
        )
        gen_grid.addWidget(self.rest_cb, 0, 0, 1, 2)

        # Rest Offset
        self.rest_offset_label = FCLabel('%s:' % _('Offset'))
        self.rest_offset_label.setToolTip(
            _("Distance by which to avoid\n"
              "the edges of the polygon to\n"
              "be painted.")
        )
        self.rest_offset_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.rest_offset_entry.set_precision(self.decimals)
        self.rest_offset_entry.set_range(-10000.0000, 10000.0000)

        gen_grid.addWidget(self.rest_offset_label, 2, 0)
        gen_grid.addWidget(self.rest_offset_entry, 2, 1)

        # Polygon selection
        selectlabel = FCLabel('%s:' % _('Selection'))
        selectlabel.setToolTip(
            _("Selection of area to be processed.\n"
              "- 'Polygon Selection' - left mouse click to add/remove polygons to be processed.\n"
              "- 'Area Selection' - left mouse click to start selection of the area to be processed.\n"
              "Keeping a modifier key pressed (CTRL or SHIFT) will allow to add multiple areas.\n"
              "- 'All Polygons' - the process will start after click.\n"
              "- 'Reference Object' - will process the area specified by another object.")
        )

        self.select_method_combo = FCComboBox2()
        self.select_method_combo.addItems(
            [_("All"), _("Polygon Selection"), _("Area Selection"), _("Reference Object")]
        )
        self.select_method_combo.setObjectName('p_selection')

        gen_grid.addWidget(selectlabel, 4, 0)
        gen_grid.addWidget(self.select_method_combo, 4, 1)

        # Type of Reference Object
        self.reference_type_label = FCLabel('%s:' % _("Type"))
        self.reference_type_label.setToolTip(
            _("The type of FlatCAM object to be used as paint reference.\n"
              "It can be Gerber, Excellon or Geometry.")
        )
        self.reference_type_combo = FCComboBox2()
        self.reference_type_combo.addItems([_("Gerber"), _("Excellon"), _("Geometry")])

        gen_grid.addWidget(self.reference_type_label, 6, 0)
        gen_grid.addWidget(self.reference_type_combo, 6, 1)

        # Reference Object
        self.reference_combo = FCComboBox()
        self.reference_combo.setModel(self.app.collection)
        self.reference_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.reference_combo.is_last = True

        gen_grid.addWidget(self.reference_combo, 8, 0, 1, 2)

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

        gen_grid.addWidget(self.area_shape_label, 10, 0)
        gen_grid.addWidget(self.area_shape_radio, 10, 1)

        self.area_shape_label.hide()
        self.area_shape_radio.hide()

        # Check Tool validity
        self.valid_cb = FCCheckBox(label=_('Check validity'))
        self.valid_cb.setObjectName("p_check")
        self.valid_cb.setToolTip(
            _("If checked then the tools diameters are verified against the smallest\n"
              "gap between the copper features of a Gerber source object.\n"
              "The run stops if no selected tool fits; it only warns if some tools are too big.")
        )
        gen_grid.addWidget(self.valid_cb, 12, 0, 1, 2)

        GLay.set_common_column_size([obj_grid, tool_grid, new_tool_grid, param_grid, gen_grid], 0)

        # #############################################################################################################
        # Preview Button
        self.preview_button = FCButton(_("Preview"), bold=True)
        self.preview_button.setToolTip(
            _("Draw the area that would be painted on the canvas.\n"
              "Nothing is created. For Polygon Selection and Area Selection\n"
              "the polygons or zones of the last Generate are used.")
        )
        self.tools_box.addWidget(self.preview_button)

        # #############################################################################################################
        # Generate Paint Geometry Button
        self.generate_button = FCButton(_('Generate Geometry'), bold=True)
        self.generate_button.setIcon(QtGui.QIcon(self.app.resource_location + '/geometry32.png'))
        self.generate_button.setToolTip(
            _("Create a Geometry Object which paints the polygons.")
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

        # #################################### FINISHED GUI ###########################
        # #############################################################################

    def on_rest_machining_check(self, state):
        if state:
            self.order_combo.set_value(2)     # Reverse
            self.order_label.setDisabled(True)
            self.order_combo.setDisabled(True)

            self.offset_label.hide()
            self.offset_entry.hide()
            self.rest_offset_label.show()
            self.rest_offset_entry.show()
        else:
            self.order_label.setDisabled(False)
            self.order_combo.setDisabled(False)

            self.offset_label.show()
            self.offset_entry.show()
            self.rest_offset_label.hide()
            self.rest_offset_entry.hide()

    def on_method_changed(self, val=None):
        enabled = self.method_combo.get_value() == 5  # Crosshatch
        self.crosshatch_angle_label.setEnabled(enabled)
        self.crosshatch_angle_entry.setEnabled(enabled)

    def on_selection(self):
        sel_combo = self.select_method_combo.get_value()

        if sel_combo == 3:  # _("Reference Object")
            self.reference_combo.show()
            self.reference_type_combo.show()
            self.reference_type_label.show()
        else:
            self.reference_combo.hide()
            self.reference_type_combo.hide()
            self.reference_type_label.hide()

        if sel_combo == 1:  # _("Polygon Selection")
            # disable rest-machining for single polygon painting
            # self.ui.rest_cb.set_value(False)
            # self.ui.rest_cb.setDisabled(True)
            pass

        if sel_combo == 2:  # _("Area Selection") index 2 in combobox (FCComboBox2() returns index instead of text)
            # disable rest-machining for area painting
            # self.ui.rest_cb.set_value(False)
            # self.ui.rest_cb.setDisabled(True)

            self.area_shape_label.show()
            self.area_shape_radio.show()
        else:   # All = index 0 in combobox
            # Beginner level hides the tool editing controls and has no tool table context menu
            if self.level.isChecked():
                self.new_tooldia_entry.setDisabled(False)
                self.search_and_add_btn.setDisabled(False)
                self.deltool_btn.setDisabled(False)
                if sel_combo == 1:  # no tool table context menu for the Polygon Selection method
                    self.tools_table.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
                else:
                    self.tools_table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

            self.area_shape_label.hide()
            self.area_shape_radio.hide()

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
