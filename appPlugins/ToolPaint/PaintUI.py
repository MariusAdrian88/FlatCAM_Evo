
from PyQt6 import QtWidgets, QtCore, QtGui      # noqa
from PyQt6.QtCore import Qt     # noqa

from appGUI.GUIElements import (
    FCLabel,
    FCFrame,
    GLay,
    RadioSet,
    FCComboBox,
    FCButton,
    FCCheckBox
)
from appPlugins.Common.clearing import ui_sections
from appPlugins.Common.clearing.methods import PAINT_METHODS

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
        tool_grid = ui_sections.build_tool_table(
            self, self.tools_box,
            label_tooltip=_("Tools pool from which the algorithm\n"
                            "will pick the ones used for painting."),
            number_tooltip=_("This is the Tool Number.\n"
                             "Painting will start with the tool with the biggest diameter,\n"
                             "continuing until there are no more tools.\n"
                             "Only tools that create painting geometry will still be present\n"
                             "in the resulting geometry. This is because with some tools\n"
                             "this function will not be able to create painting geometry."),
            table_row=2, order_label_bold=True, sorting_off=False)

        # ###################### ADD A NEW TOOL ########################################
        new_tool_grid = ui_sections.build_add_tool(
            self, tool_grid, span=2,
            dia_tooltip=_("Diameter for the new tool to add in the Tool Table.\n"
                          "If the tool is V-shape type then this value is automatically\n"
                          "calculated from the other parameters."),
            dia_object_name='p_tool_dia', dia_min=0.0001)

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

        ui_sections.build_overlap(self, param_grid, 0, 'p_', precision=3)
        offset_tooltip = _("Distance by which to avoid\n"
                           "the edges of the polygon to\n"
                           "be painted.")
        ui_sections.build_offset(
            self, param_grid, 2, rest=False, choice=False, min_value=-10000.0, max_value=10000.0,
            precision=self.decimals, entry_object_name='p_offset', tooltip=offset_tooltip
        )
        ui_sections.build_method(
            self, param_grid, 4, 'p_', PAINT_METHODS,
            tooltip=_("Algorithm for painting:\n"
                      "- Standard: Fixed step inwards.\n"
                      "- Seed-based: Outwards from seed.\n"
                      "- Line-based: Parallel lines.\n"
                      "- Laser-lines: Active only for Gerber objects.\n"
                      "Will create lines that follow the traces.\n"
                      "- Combo: In case of failure a new method will be picked from the above\n"
                      "in the order specified.\n"
                      "- Crosshatch: Two perpendicular families of parallel lines.")
        )
        idx = self.method_combo.findText(_("Laser_lines"))
        self.method_combo.model().item(idx).setEnabled(False)  # type: ignore[union-attr]

        ui_sections.build_connect_contour(self, param_grid, 6, 'p_')
        ui_sections.build_direction(self, param_grid, 8, 'p_')
        ui_sections.build_final_cleanup(
            self, param_grid, 7, 'p_', text=_("Final paint"),
            tooltip=_("After painting, use the smallest selected tool with Final paint enabled\n"
                      "to paint reachable gaps. May overlap previous passes, but keeps the whole\n"
                      "tool inside the selected boundary and margins. Default: Off.")
        )

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

        ui_sections.build_rest(
            self, gen_grid, 'p_',
            tooltip=_("If checked, use 'rest machining'.\n"
                      "Copper features will be processed starting with the biggest selected tool.\n"
                      "What cannot be processed will be passed to the next bigger tool and so on,\n"
                      "until either there are no longer selected tools or all the copper features are processed.")
        )
        ui_sections.build_offset(
            self, gen_grid, 2, rest=True, choice=False, min_value=-10000.0, max_value=10000.0,
            precision=self.decimals, tooltip=offset_tooltip
        )
        ui_sections.build_selection(
            self, gen_grid, 4, 'p_',
            [_("All"), _("Polygon Selection"), _("Area Selection"), _("Reference Object")],
            tooltip=_("Selection of area to be processed.\n"
                      "- 'Polygon Selection' - left mouse click to add/remove polygons to be processed.\n"
                      "- 'Area Selection' - left mouse click to start selection of the area to be processed.\n"
                      "Keeping a modifier key pressed (CTRL or SHIFT) will allow to add multiple areas.\n"
                      "- 'All Polygons' - the process will start after click.\n"
                      "- 'Reference Object' - will process the area specified by another object."),
            reference_tooltip=_("The type of FlatCAM object to be used as paint reference.\n"
                                "It can be Gerber, Excellon or Geometry.")
        )
        ui_sections.build_validity(
            self, gen_grid, 12, 'p_',
            tooltip=_("If checked then the tools diameters are verified against the smallest\n"
                      "gap between the copper features of a Gerber source object.\n"
                      "The run stops if no selected tool fits; it only warns if some tools are too big.")
        )

        # #############################################################################################################
        # Exclusion areas Frame
        # #############################################################################################################
        excl_grid = ui_sections.build_exclusion(
            self, self.tools_box,
            label_tooltip=_("Zones drawn on the canvas that are left out of the painting."),
            check_tooltip=_("When checked, the cutter keeps its radius away from the zones below,\n"
                            "for the Generate and the Preview buttons.")
        )

        GLay.set_common_column_size([obj_grid, tool_grid, new_tool_grid, param_grid, gen_grid, excl_grid], 0)

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
