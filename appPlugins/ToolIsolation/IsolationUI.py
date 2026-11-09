# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     5/25/2020                                      #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from defaults import AppDefaults
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    FCComboBox2, RadioSet, FCDoubleSpinner, FCSpinner, FCTable, OptionalInputSection

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class IsoUI:
    pluginName = _("Isolation")

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
              "toolpaths to cut around polygons.")
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
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        # #############################################################################################################
        # Source Object for Isolation
        # #############################################################################################################
        self.obj_combo_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.obj_combo_label.setToolTip(_("Gerber object for isolation routing."))
        self.tools_box.addWidget(self.obj_combo_label)

        # #############################################################################################################
        # ################################ The object to be isolated ##################################################
        # #############################################################################################################
        self.object_combo = FCComboBox()
        self.object_combo.setModel(self.app.collection)
        self.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        # self.object_combo.setCurrentIndex(1)
        self.object_combo.is_last = True

        self.tools_box.addWidget(self.object_combo)

        # Isolate all the Gerber objects selected in the Project tab
        self.multi_objects_cb = FCCheckBox(_("Selected objects"))
        self.multi_objects_cb.setToolTip(
            _("Isolate all the Gerber objects selected in the Project tab, each with the same tools.")
        )
        self.tools_box.addWidget(self.multi_objects_cb)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # self.tools_box.addWidget(separator_line)

        # #############################################################################################################
        # Tool Table Frame
        # #############################################################################################################
        self.tools_table_label = FCLabel('%s' % _("Tools Table"), color='green', bold=True)
        self.tools_table_label.setToolTip(
            _("Tools pool from which the algorithm\n"
              "will pick the ones used for copper clearing.")
        )
        self.tools_box.addWidget(self.tools_table_label)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        # Grid Layout
        tool_grid = GLay(v_spacing=5, h_spacing=3)
        tt_frame.setLayout(tool_grid)

        self.tools_table = FCTable(drag_drop=True)
        tool_grid.addWidget(self.tools_table, 0, 0, 1, 2)

        self.tools_table.setColumnCount(4)
        # 3rd column is reserved (and hidden) for the tool ID
        self.tools_table.setHorizontalHeaderLabels(['#', _('Diameter'), '', ''])
        self.tools_table.setColumnHidden(2, True)
        self.tools_table.setColumnHidden(3, True)
        self.tools_table.setSortingEnabled(False)
        # self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.tools_table.horizontalHeaderItem(0).setToolTip(
            _("This is the Tool Number.\n"
              "Isolation routing will start with the tool with the biggest \n"
              "diameter, continuing until there are no more tools.\n"
              "Only tools that create Isolation geometry will still be present\n"
              "in the resulting geometry. This is because with some tools\n"
              "this function will not be able to create routing geometry.")
        )
        self.tools_table.horizontalHeaderItem(1).setToolTip(
            _("Tool Diameter. Its value\n"
              "is the cut width into the material."))

        # Tool order
        self.order_label = FCLabel('%s:' % _('Tool order'))
        self.order_label.setToolTip(_("This set the way that the tools in the tools table are used.\n"
                                      "'Default' --> means that the used order is the one in the tool table\n"
                                      "'Forward' --> means that the tools will be ordered from small to big\n"
                                      "'Reverse' --> means that the tools will ordered from big to small\n\n"
                                      "WARNING: using rest machining will automatically set the order\n"
                                      "in reverse and disable this control."))

        self.iso_order_combo = FCComboBox2()
        self.iso_order_combo.addItems([_('Default'), _('Forward'), _('Reverse')])

        tool_grid.addWidget(self.order_label, 2, 0)
        tool_grid.addWidget(self.iso_order_combo, 2, 1)

        # #############################################################
        # ############### Tool adding #################################
        # #############################################################
        self.add_tool_frame = QtWidgets.QFrame()
        self.add_tool_frame.setContentsMargins(0, 0, 0, 0)
        tool_grid.addWidget(self.add_tool_frame, 4, 0, 1, 2)

        new_tool_grid = GLay(v_spacing=5, h_spacing=3)
        new_tool_grid.setContentsMargins(0, 0, 0, 0)
        self.add_tool_frame.setLayout(new_tool_grid)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        new_tool_grid.addWidget(separator_line, 0, 0, 1, 3)

        self.tool_sel_label = FCLabel('%s' % _('Add from DB'), bold=True)
        new_tool_grid.addWidget(self.tool_sel_label, 2, 0, 1, 3)

        # ### Tool Diameter ####
        self.new_tooldia_lbl = FCLabel('%s: ' % _('Tool Dia'))
        self.new_tooldia_lbl.setToolTip(
            _("Diameter for the new tool")
        )
        new_tool_grid.addWidget(self.new_tooldia_lbl, 4, 0)

        # Tool diameter entry
        self.new_tooldia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.new_tooldia_entry.set_precision(self.decimals)
        self.new_tooldia_entry.set_range(-10000.0000, 10000.0000)
        self.new_tooldia_entry.setObjectName("i_new_tooldia")

        # Find Optimal Tooldia
        self.find_optimal_button = QtWidgets.QToolButton()
        self.find_optimal_button.setText(_('Optimal'))
        self.find_optimal_button.setIcon(QtGui.QIcon(self.app.resource_location + '/open_excellon32.png'))
        self.find_optimal_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.find_optimal_button.setToolTip(
            _("Find a tool diameter that is guaranteed\n"
              "to do a complete isolation.")
        )

        new_tool_grid.addWidget(self.new_tooldia_entry, 4, 1)
        new_tool_grid.addWidget(self.find_optimal_button, 4, 2)

        # #############################################################################################################
        # ################################    Button Grid   ###########################################################
        # #############################################################################################################
        button_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[1, 0])
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
        tool_param_grid = GLay(v_spacing=5, h_spacing=3)
        tp_frame.setLayout(tool_param_grid)

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
        self.tool_shape_combo.setObjectName('i_tool_shape')
        self.tool_shape_combo.addItems(["C1", "C2", "C3", "C4", "B", "V", "L"])

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        idx = int(self.app.options.get(
            'tools_iso_tool_shape',
            app_defaults.get('tools_iso_tool_shape', AppDefaults.factory_defaults.get('tools_iso_tool_shape'))))
        # protection against having this translated or loading a project with translated values
        if idx == -1:
            self.tool_shape_combo.setCurrentIndex(0)
        else:
            self.tool_shape_combo.setCurrentIndex(idx)

        tool_param_grid.addWidget(self.tool_shape_label, 0, 0)
        tool_param_grid.addWidget(self.tool_shape_combo, 0, 1)

        # V-Shape Frame
        self.v_frame = QtWidgets.QFrame()
        self.v_frame.setContentsMargins(0, 0, 0, 0)
        tool_param_grid.addWidget(self.v_frame, 2, 0, 1, 2)

        v_grid = GLay(v_spacing=5, h_spacing=3)
        v_grid.setContentsMargins(0, 0, 0, 0)
        self.v_frame.setLayout(v_grid)

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
        self.tipdia_entry.setObjectName("i_tipdia")

        v_grid.addWidget(self.tipdialabel, 0, 0)
        v_grid.addWidget(self.tipdia_entry, 0, 1)

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
        self.tipangle_entry.setObjectName("i_tipangle")

        v_grid.addWidget(self.tipanglelabel, 2, 0)
        v_grid.addWidget(self.tipangle_entry, 2, 1)

        # Cut Z
        self.cutzlabel = FCLabel('%s:' % _('Cut Z'))
        self.cutzlabel.setToolTip(
            _("Cutting depth (negative)\n"
              "below the copper surface."))

        self.cutz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutz_entry.set_precision(self.decimals)

        self.cutz_entry.set_range(-10000.0000, 10000.0000)

        self.cutz_entry.setSingleStep(0.1)
        self.cutz_entry.setObjectName("i_cutz")

        v_grid.addWidget(self.cutzlabel, 4, 0)
        v_grid.addWidget(self.cutz_entry, 4, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        v_grid.addWidget(separator_line, 6, 0, 1, 2)

        self.v_frame.hide()

        # Passes
        passlabel = FCLabel('%s:' % _('Passes'))
        passlabel.setToolTip(
            _("Width of the isolation gap in\n"
              "number (integer) of tool widths.")
        )
        self.passes_entry = FCSpinner(callback=self.confirmation_message_int)
        self.passes_entry.set_range(1, 9999)
        self.passes_entry.setObjectName("i_passes")

        tool_param_grid.addWidget(passlabel, 6, 0)
        tool_param_grid.addWidget(self.passes_entry, 6, 1)

        # Pad Passes
        padpasslabel = FCLabel('%s:' % _('Pad Passes'))
        padpasslabel.setToolTip(
            _("Width of the extra isolation gap for pads only,\n"
              "in number (integer) of tool widths.")
        )
        self.pad_passes_entry = FCSpinner()
        self.pad_passes_entry.set_range(0, 999)
        self.pad_passes_entry.setObjectName("i_pad_passes")

        tool_param_grid.addWidget(padpasslabel, 8, 0)
        tool_param_grid.addWidget(self.pad_passes_entry, 8, 1)

        # Overlap Entry
        overlabel = FCLabel('%s:' % _('Overlap'))
        overlabel.setToolTip(
            _("How much (percentage) of the tool width to overlap each tool pass.")
        )
        self.iso_overlap_entry = FCDoubleSpinner(suffix='%', callback=self.confirmation_message)
        self.iso_overlap_entry.set_precision(self.decimals)
        self.iso_overlap_entry.setWrapping(True)
        self.iso_overlap_entry.set_range(0.0000, 99.9999)
        self.iso_overlap_entry.setSingleStep(0.1)
        self.iso_overlap_entry.setObjectName("i_overlap")

        tool_param_grid.addWidget(overlabel, 10, 0)
        tool_param_grid.addWidget(self.iso_overlap_entry, 10, 1)

        # Milling Type Radio Button
        self.milling_type_label = FCLabel('%s:' % _('Milling Type'))
        self.milling_type_label.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )

        self.milling_type_radio = RadioSet([{'label': _('Climb'), 'value': 'cl'},
                                            {'label': _('Conventional'), 'value': 'cv'}])
        self.milling_type_radio.setToolTip(
            _("Milling type:\n"
              "- climb / best for precision milling and to reduce tool usage\n"
              "- conventional / useful when there is no backlash compensation")
        )
        self.milling_type_radio.setObjectName("i_milling_type")

        tool_param_grid.addWidget(self.milling_type_label, 12, 0)
        tool_param_grid.addWidget(self.milling_type_radio, 12, 1)

        # Isolation Type
        self.iso_type_label = FCLabel('%s:' % _('Isolation Type'))
        self.iso_type_label.setToolTip(
            _("Choose how the isolation will be executed:\n"
              "- 'Full' -> complete isolation of polygons\n"
              "- 'Ext' -> will isolate only on the outside\n"
              "- 'Int' -> will isolate only on the inside\n"
              "'Exterior' isolation is almost always possible\n"
              "(with the right tool) but 'Interior'\n"
              "isolation can be done only when there is an opening\n"
              "inside of the polygon (e.g polygon is a 'doughnut' shape).")
        )
        self.iso_type_radio = RadioSet([{'label': _('Full'), 'value': 'full'},
                                        {'label': _('Ext'), 'value': 'ext'},
                                        {'label': _('Int'), 'value': 'int'}])
        self.iso_type_radio.setObjectName("i_iso_type")

        tool_param_grid.addWidget(self.iso_type_label, 14, 0)
        tool_param_grid.addWidget(self.iso_type_radio, 14, 1)

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

        # Rest Machining
        self.rest_cb = FCCheckBox('%s' % _("Rest Machining"))
        self.rest_cb.setObjectName("i_rest")
        self.rest_cb.setToolTip(
            _("If checked, use 'rest machining'.\n"
              "Copper features will be processed starting with the biggest selected tool.\n"
              "What cannot be processed will be passed to the next bigger tool and so on,\n"
              "until either there are no longer selected tools or all the copper features are processed.")
        )

        gen_grid.addWidget(self.rest_cb, 0, 0)

        # Force isolation even if the interiors are not isolated
        self.forced_rest_iso_cb = FCCheckBox(_("Forced Rest"))
        self.forced_rest_iso_cb.setToolTip(
            _("When checked the isolation will be done with the current tool even if\n"
              "interiors of a polygon (holes in the polygon) could not be isolated.\n"
              "Works when 'rest machining' is used.")
        )

        gen_grid.addWidget(self.forced_rest_iso_cb, 0, 1)

        # Combine All Passes
        self.combine_passes_cb = FCCheckBox(label=_('Combine'))
        self.combine_passes_cb.setToolTip(
            _("Combine all passes into one object")
        )
        self.combine_passes_cb.setObjectName("i_combine")

        gen_grid.addWidget(self.combine_passes_cb, 2, 0, 1, 2)

        # Clear the copper left between the passes
        self.clear_leftovers_cb = FCCheckBox(label=_('Clear leftovers'))
        self.clear_leftovers_cb.setToolTip(
            _("Where the passes around two features meet, the last band can be too\n"
              "narrow for one more pass and wider than what the last pass cuts:\n"
              "strips and triangles of copper are left, however many passes are made.\n"
              "When checked, one more path along each of them clears them.\n"
              "Full isolation with combined passes (not rest machining).")
        )
        self.clear_leftovers_cb.setObjectName("i_clear_leftovers")

        gen_grid.addWidget(self.clear_leftovers_cb, 3, 0, 1, 2)

        # Check Tool validity
        self.valid_cb = FCCheckBox(label=_('Check validity'))
        self.valid_cb.setToolTip(
            _("If checked then the tools diameters are verified\n"
              "if they will provide a complete isolation.")
        )
        self.valid_cb.setObjectName("i_check")

        gen_grid.addWidget(self.valid_cb, 4, 0, 1, 2)

        # Simplification Tolerance
        self.simplify_cb = FCCheckBox('%s' % _("Simplify"))
        self.simplify_cb.setToolTip(
            _("All points in the simplified object will be\n"
              "within the tolerance distance of the original geometry.")
        )
        self.sim_tol_entry = FCDoubleSpinner()
        self.sim_tol_entry.set_precision(self.decimals)
        self.sim_tol_entry.setSingleStep(10 ** -self.decimals)
        self.sim_tol_entry.set_range(0.0000, 10000.0000)

        self.sois = OptionalInputSection(self.simplify_cb, [self.sim_tol_entry])

        gen_grid.addWidget(self.simplify_cb, 6, 0)
        gen_grid.addWidget(self.sim_tol_entry, 6, 1)

        # Exception Areas
        self.except_cb = FCCheckBox(label=_('Except'))
        self.except_cb.setToolTip(_("When the isolation geometry is generated,\n"
                                    "by checking this, the area of the object below\n"
                                    "will be subtracted from the isolation geometry."))
        self.except_cb.setObjectName("i_except")
        gen_grid.addWidget(self.except_cb, 8, 0)

        # Type of object to be excepted
        self.type_excobj_radio = RadioSet([{'label': _("Geometry"), 'value': 'geometry'},
                                           {'label': _("Gerber"), 'value': 'gerber'}])
        self.type_excobj_radio.setToolTip(
            _("Specify the type of object to be excepted from isolation.\n"
              "It can be of type: Gerber or Geometry.\n"
              "What is selected here will dictate the kind\n"
              "of objects that will populate the 'Object' combobox.")
        )

        gen_grid.addWidget(self.type_excobj_radio, 8, 1)

        # The object to be excepted
        self.exc_obj_combo = FCComboBox()
        self.exc_obj_combo.setToolTip(_("Object whose area will be removed from isolation geometry."))

        # set the model for the Area Exception comboboxes
        self.exc_obj_combo.setModel(self.app.collection)
        self.exc_obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.exc_obj_combo.is_last = True
        self.exc_obj_combo.obj_type = "gerber"

        gen_grid.addWidget(self.exc_obj_combo, 10, 0, 1, 2)

        self.e_ois = OptionalInputSection(self.except_cb,
                                          [
                                              self.type_excobj_radio,
                                              self.exc_obj_combo
                                          ])

        # Isolation Scope
        self.select_label = FCLabel('%s:' % _("Selection"))
        self.select_label.setToolTip(
            _("Isolation scope. Choose what to isolate:\n"
              "- 'All' -> Isolate all the polygons in the object\n"
              "- 'Area Selection' -> Isolate polygons within a selection area.\n"
              "- 'Polygon Selection' -> Isolate a selection of polygons.\n"
              "- 'Reference Object' - will process the area specified by another object.")
        )
        self.select_combo = FCComboBox2()
        self.select_combo.addItems(
            [_("All"), _("Area Selection"), _("Polygon Selection"), _("Reference Object")]
        )
        self.select_combo.setObjectName("i_selection")

        gen_grid.addWidget(self.select_label, 12, 0)
        gen_grid.addWidget(self.select_combo, 12, 1)

        # Reference Type
        self.reference_combo_type_label = FCLabel('%s:' % _("Type"))

        self.reference_combo_type = FCComboBox2()
        self.reference_combo_type.addItems([_("Gerber"), _("Excellon"), _("Geometry")])

        gen_grid.addWidget(self.reference_combo_type_label, 14, 0)
        gen_grid.addWidget(self.reference_combo_type, 14, 1)

        self.reference_combo = FCComboBox()
        self.reference_combo.setModel(self.app.collection)
        self.reference_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.reference_combo.is_last = True

        gen_grid.addWidget(self.reference_combo, 16, 0, 1, 2)

        self.reference_combo.hide()
        self.reference_combo_type.hide()
        self.reference_combo_type_label.hide()

        # Polygon interiors selection
        self.poly_int_cb = FCCheckBox(_("Interiors"))
        self.poly_int_cb.setToolTip(
            _("When checked the user can select interiors of a polygon.\n"
              "(holes in the polygon).")
        )

        gen_grid.addWidget(self.poly_int_cb, 18, 0)

        self.poly_int_cb.hide()

        # Select All/None
        sel_hlay = QtWidgets.QHBoxLayout()
        self.sel_all_btn = FCButton(_("Select All"))
        self.sel_all_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/select_all.png'))

        self.sel_all_btn.setToolTip(
            _("Select all available.")
        )
        self.clear_all_btn = FCButton(_("Deselect All"))
        self.clear_all_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/deselect_all32.png'))

        self.clear_all_btn.setToolTip(
            _("Clear the selection.")
        )

        self.sel_all_btn.hide()
        self.clear_all_btn.hide()

        sel_hlay.addWidget(self.sel_all_btn)
        sel_hlay.addWidget(self.clear_all_btn)
        gen_grid.addLayout(sel_hlay, 20, 0, 1, 2)

        # Area Selection shape
        self.area_shape_label = FCLabel('%s:' % _("Shape"))
        self.area_shape_label.setToolTip(
            _("The kind of selection shape used for area selection.")
        )

        self.area_shape_radio = RadioSet([{'label': _("Square"), 'value': 'square'},
                                          {'label': _("Polygon"), 'value': 'polygon'}])

        gen_grid.addWidget(self.area_shape_label, 22, 0)
        gen_grid.addWidget(self.area_shape_radio, 22, 1)

        self.area_shape_label.hide()
        self.area_shape_radio.hide()

        # Boundary: strategy-specific enclosure around an object
        self.boundary_cb = FCCheckBox(label=_('Boundary'))
        self.boundary_cb.setToolTip(_("Use the area of a Gerber or Geometry outline object (e.g. Edge Cuts) as a boundary.\n"
                                      "Voronoi uses its convex hull and cuts its compensated outer perimeter\n"
                                      "to enclose complete islands, with extra working room for the full cutter.\n"
                                      "Offset uses the area enclosed by the outline (the concave hull of all its\n"
                                      "shapes if the object has flashes inside the outline): paths outside it are cut off,\n"
                                      "and passes stop when one is completely outside it."))
        self.boundary_cb.setObjectName("i_boundary")
        gen_grid.addWidget(self.boundary_cb, 24, 0)

        # Type of the boundary object
        self.type_boundary_radio = RadioSet([{'label': _("Geometry"), 'value': 'geometry'},
                                             {'label': _("Gerber"), 'value': 'gerber'}])
        self.type_boundary_radio.setToolTip(
            _("The type of the object whose hull is the boundary.\n"
              "What is selected here will dictate the kind\n"
              "of objects that will populate the 'Object' combobox.")
        )
        gen_grid.addWidget(self.type_boundary_radio, 24, 1)

        # The boundary object
        self.boundary_obj_combo = FCComboBox()
        self.boundary_obj_combo.setToolTip(_("Boundary object: convex hull for Voronoi, enclosed area (concave hull if it has flashes) for Offset."))
        self.boundary_obj_combo.setModel(self.app.collection)
        self.boundary_obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.boundary_obj_combo.is_last = True
        self.boundary_obj_combo.obj_type = "gerber"
        gen_grid.addWidget(self.boundary_obj_combo, 26, 0, 1, 2)

        # Also cut along the boundary
        self.trace_boundary_cb = FCCheckBox(label=_('Trace the boundary'))
        self.trace_boundary_cb.setToolTip(_("Also add one path along the boundary outline."))
        self.trace_boundary_cb.setObjectName("i_trace_boundary")
        gen_grid.addWidget(self.trace_boundary_cb, 28, 0, 1, 2)

        self.boundary_ois = OptionalInputSection(self.boundary_cb,
                                                 [
                                                     self.type_boundary_radio,
                                                     self.boundary_obj_combo,
                                                     self.trace_boundary_cb
                                                 ])

        # Isolation strategy
        self.strategy_label = FCLabel('%s:' % _("Strategy"))
        self.strategy_label.setToolTip(
            _("Offset: buffer the copper with the tool radius (the classic isolation).\n"
              "Voronoi: cut only the borders between different copper bodies, from sampled boundaries.\n"
              "Voronoi needs Boundary, All copper, Full isolation and one positive pass.\n"
              "Rest machining, exclusions, pad passes and leftovers are not supported.\n"
              "Boundary tracing is ignored; no contour is made around a single body."))
        self.strategy_combo = RadioSet([{'label': _("Offset"), 'value': 'offset'},
                                        {'label': _("Voronoi"), 'value': 'voronoi'}])
        self.strategy_combo.set_value('offset')
        self.strategy_combo.setObjectName("i_strategy")
        self.strategy_combo.setToolTip(self.strategy_label.toolTip())
        gen_grid.addWidget(self.strategy_label, 30, 0)
        gen_grid.addWidget(self.strategy_combo, 30, 1)

        # Sampling step of the Voronoi strategy
        self.voronoi_spacing_label = FCLabel('%s:' % _("Voronoi spacing"))
        self.voronoi_spacing_label.setToolTip(
            _("Sampling step along the copper boundaries for the Voronoi strategy,\n"
              "in the application units (mm or inch). This bounds sample intervals, not the toolpath error.\n"
              "The run stops with a message when the 20000 sample-site budget is exceeded;\n"
              "increase the spacing instead of lowering the requested accuracy."))
        self.voronoi_spacing_entry = FCDoubleSpinner()
        self.voronoi_spacing_entry.set_precision(self.decimals)
        self.voronoi_spacing_entry.set_range(0.00001, 10000.0000)
        self.voronoi_spacing_entry.setSingleStep(0.01)
        self.voronoi_spacing_entry.setObjectName("i_voronoi_spacing")
        gen_grid.addWidget(self.voronoi_spacing_label, 32, 0)
        gen_grid.addWidget(self.voronoi_spacing_entry, 32, 1)
        self.voronoi_pads_cb = FCCheckBox(_("Pad isolation"))
        self.voronoi_pads_cb.setObjectName("i_voronoi_pads")
        self.voronoi_pads_cb.set_value(False)
        self.voronoi_pads_cb.setToolTip(_(
            "After Voronoi generation, add partial cuts around flashed pad outlines with the same cutter and depth.\n"
            "Original trace and polygon connections stay intact; pads inside pours may have no safe room.\n"
            "Region-only and negative images are skipped. This is not solder mask or guaranteed thermal relief.\n"
            "The combined paths use normal CNC path ordering, not a separate machining phase."))
        gen_grid.addWidget(self.voronoi_pads_cb, 34, 0, 1, 2)
        self.strategy_combo.activated_custom.connect(self.on_strategy_changed)
        self.boundary_cb.stateChanged.connect(self.on_strategy_changed)
        self.on_strategy_changed()

        GLay.set_common_column_size([tool_grid, new_tool_grid, tool_param_grid, v_grid, gen_grid], 0)

        # #############################################################################################################
        # Generate Geometry object
        # #############################################################################################################
        gen_hlay = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(gen_hlay)

        self.generate_iso_button = FCButton("%s" % _("Generate Geometry"), bold=True)
        self.generate_iso_button.setIcon(QtGui.QIcon(self.app.resource_location + '/geometry32.png'))
        self.generate_iso_button.setToolTip(
            _("Create a Geometry object with toolpaths to cut \n"
              "isolation outside, inside or on both sides of the\n"
              "object. For a Gerber object outside means outside\n"
              "of the Gerber feature and inside means inside of\n"
              "the Gerber feature, if possible at all. This means\n"
              "that only if the Gerber feature has openings inside, they\n"
              "will be isolated. If what is wanted is to cut isolation\n"
              "inside the actual Gerber feature, use a negative tool\n"
              "diameter above.")
        )
        gen_hlay.addWidget(self.generate_iso_button, stretch=1)

        # Milling Plugin shortcut
        self.milling_button = QtWidgets.QToolButton()
        self.milling_button.setIcon(QtGui.QIcon(self.app.resource_location + '/milling_tool32.png'))
        self.milling_button.setToolTip(
            _("Generate a CNCJob by milling a Geometry.")
        )
        gen_hlay.addWidget(self.milling_button)

        self.create_buffer_button = FCButton(_('Buffer Solid Geometry'))
        self.create_buffer_button.setToolTip(
            _("This button is shown only when the Gerber file\n"
              "is loaded without buffering.\n"
              "Clicking this will create the buffered geometry\n"
              "required for isolation.")
        )
        self.tools_box.addWidget(self.create_buffer_button)

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

    def on_strategy_changed(self, *args):
        voronoi = self.strategy_combo.get_value() == 'voronoi'
        self.trace_boundary_cb.setEnabled(self.boundary_cb.get_value() and not voronoi)
        self.voronoi_spacing_entry.setEnabled(voronoi)
        self.voronoi_spacing_label.setEnabled(voronoi)
        self.voronoi_pads_cb.setEnabled(voronoi)

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
