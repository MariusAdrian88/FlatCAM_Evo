
from PyQt6 import QtWidgets, QtCore, QtGui      # noqa

from appGUI.GUIElements import (
    FCLabel,
    FCFrame,
    GLay,
    RadioSet,
    FCComboBox,
    FCDoubleSpinner,
    FCButton,
    FCCheckBox,
)
from appPlugins.Common.clearing import ui_sections
from appPlugins.Common.clearing.methods import NCC_METHODS

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
        tool_grid = ui_sections.build_tool_table(
            self, self.tools_box,
            label_tooltip=_("Tools pool from which the algorithm\n"
                            "will pick the ones used for copper clearing."),
            number_tooltip=_("This is the Tool Number.\n"
                             "Non copper clearing will start with the tool with the biggest \n"
                             "diameter, continuing until there are no more tools.\n"
                             "Only tools that create NCC clearing geometry will still be present\n"
                             "in the resulting geometry. This is because with some tools\n"
                             "this function will not be able to create painting geometry."),
            table_row=0, order_label_bold=False, sorting_off=True)

        # ###################### ADD A NEW TOOL ########################################
        new_tool_grid = ui_sections.build_add_tool(
            self, tool_grid, span=3, dia_tooltip=_("Diameter for the new tool"),
            dia_object_name=_("Tool Dia"), dia_min=-10000.0)

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

        ui_sections.build_overlap(self, par_grid, 4, 'n_', precision=self.decimals, label_attr='overlap_label')
        ui_sections.build_method(
            self, par_grid, 6, 'n_', NCC_METHODS, label_attr='method_label',
            tooltip=_("Algorithm for copper clearing:\n"
                      "- Standard: Fixed step inwards.\n"
                      "- Seed-based: Outwards from seed.\n"
                      "- Line-based: Parallel lines.")
        )
        self._build_margin(par_grid, 8, '', "n_margin")
        ui_sections.build_connect_contour(self, par_grid, 10, 'n_')
        ui_sections.build_direction(self, par_grid, 9, 'n_')
        ui_sections.build_final_cleanup(
            self, par_grid, 11, 'n_', text=_("Final cleanup"),
            tooltip=_("After clearing, use the smallest selected tool with Final cleanup enabled\n"
                      "to remove reachable residual copper. May cross cleared areas, but preserves\n"
                      "copper clearance and the clearing boundary. Default: Off.")
        )
        ui_sections.build_offset(
            self, par_grid, 12, rest=False, choice=True, min_value=0.0, max_value=10.0, precision=4, wrapping=True,
            step_by_units=True, check_object_name="n_offset", entry_object_name="n_offset_value",
            tooltip=_("If used, it will add an offset to the copper features.\n"
                      "The copper clearing will finish to a distance\n"
                      "from the copper features.")
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

        ui_sections.build_rest(
            self, gen_grid, 'n_',
            tooltip="%s\n%s" % (
                _("If checked, use 'rest machining'.\n"
                  "Copper features will be processed starting with the biggest selected tool.\n"
                  "What cannot be processed will be passed to the next smaller tool and so on,\n"
                  "until either there are no longer selected tools or all the copper features are processed."),
                _("Only tools selected for copper clearing will be used.")
            )
        )
        self._build_margin(gen_grid, 2, 'rest_', "n_rest_margin")
        ui_sections.build_connect_contour(self, gen_grid, 4, 'n_', rest=True)
        ui_sections.build_offset(
            self, gen_grid, 6, rest=True, choice=True, min_value=0.0, max_value=10.0, precision=4, wrapping=True,
            step_by_units=True,
            tooltip=_("If used, it will add an offset to the copper features.\n"
                      "The copper clearing will finish to a distance\n"
                      "from the copper features.")
        )
        ui_sections.build_selection(
            self, gen_grid, 8, 'n_',
            [_("Itself"), _("Area Selection"), _("Reference Object"), _("Polygon Selection")],
            label_attr='select_method_label',
            tooltip=_("Selection of area to be processed.\n"
                      "- 'Itself' - the processing extent is based on the object that is processed.\n "
                      "- 'Area Selection' - left mouse click to start selection of the area to be processed.\n"
                      "- 'Reference Object' - will process the area specified by another object.\n"
                      "- 'Polygon Selection' - left click adds / removes a copper-free region, right click to start."),
            reference_tooltip=_("The type of FlatCAM object to be used as non copper clearing reference.\n"
                                "It can be Gerber, Excellon or Geometry.")
        )
        ui_sections.build_validity(
            self, gen_grid, 16, 'n_',
            tooltip=_("If checked then the tools diameters are verified\n"
                      "if they will provide a complete isolation.")
        )

        # #############################################################################################################
        # Exclusion areas Frame
        # #############################################################################################################
        excl_grid = ui_sections.build_exclusion(
            self, self.tools_box, check_object_name="n_exclusion",
            check_tooltip=_("Zones drawn on the canvas that are left out of the copper clearing.\n"
                            "When checked, the zones below are removed from the area to clear,\n"
                            "for the Generate and the Preview buttons.")
        )

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

    def _build_margin(self, grid, row, head, object_name):
        """The bounding box margin of the tool (``head`` '') or of the rest machining ('rest_')."""
        label = FCLabel('%s:' % _('Margin'))
        label.setToolTip(
            _("Bounding box margin.")
        )
        entry = FCDoubleSpinner(callback=self.confirmation_message)
        entry.set_precision(self.decimals)
        entry.set_range(-10000.0000, 10000.0000)
        entry.setObjectName(object_name)
        setattr(self, head + 'margin_label', label)
        setattr(self, head + 'margin_entry', entry)
        grid.addWidget(label, row, 0)
        grid.addWidget(entry, row, 1)

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
            self.clear_dir_label.setEnabled(False)
            self.clear_dir_radio.setEnabled(False)
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
            self.clear_dir_label.setEnabled(True)
            self.clear_dir_radio.setEnabled(True)
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
        elif sel_combo == 3:    # polygon selection
            self.reference_combo.hide()
            self.reference_type_combo.hide()
            self.reference_type_label.hide()
            self.area_shape_label.hide()
            self.area_shape_radio.hide()
            self.rest_cb.setDisabled(False)
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
