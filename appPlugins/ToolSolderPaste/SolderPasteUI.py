# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCEntry, \
    FCDoubleSpinner, FCSpinner, FCTable
from appGUI.SolderPasteSettings import SolderPasteSettings

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class SolderUI:

    pluginName = _("SolderPaste")

    def __init__(self, layout, app, solder_class):
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
            _("A plugin to help dispense solder paste on the PCB pads using a CNC machine.")
        )
        self.title_box.addWidget(title_label)

        self.calibration = SolderPasteSettings(app)
        self.tools_box.addWidget(self.calibration)

        # #############################################################################################################
        # Source Object
        # #############################################################################################################
        self.object_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.object_label.setToolTip(_("Gerber Solderpaste object."))
        self.tools_box.addWidget(self.object_label)

        self.obj_combo = FCComboBox(callback=solder_class.on_rmb_combo)
        self.obj_combo.setModel(self.app.collection)
        self.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.obj_combo.is_last = True
        self.obj_combo.obj_type = "Gerber"

        self.tools_box.addWidget(self.obj_combo)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # obj_form_layout.addWidget(separator_line, 4, 0, 1, 2)

        # #############################################################################################################
        # Tool Table Frame
        # #############################################################################################################
        self.tools_table_label = FCLabel('%s' % _("Tools Table"), color='green', bold=True)
        self.tools_table_label.setToolTip(
            _("Tools pool from which the algorithm\n"
              "will pick the ones used for dispensing solder paste.")
        )
        self.tools_box.addWidget(self.tools_table_label)

        tt_frame = FCFrame()
        self.tools_box.addWidget(tt_frame)

        tool_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 0])
        tt_frame.setLayout(tool_grid)

        self.tools_table = FCTable()
        self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        tool_grid.addWidget(self.tools_table, 0, 0, 1, 4)

        self.tools_table.setColumnCount(3)
        self.tools_table.setHorizontalHeaderLabels(['#', _('Diameter'), ''])
        self.tools_table.setColumnHidden(2, True)
        self.tools_table.setSortingEnabled(False)
        # self.tools_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        self.tools_table.horizontalHeaderItem(0).setToolTip(
            _("This is the Tool Number.\n"
              "The solder dispensing will start with the tool with the biggest \n"
              "diameter, continuing until there are no more Nozzle tools.\n"
              "If there are no longer tools but there are still pads not covered\n "
              "with solder paste, the app will issue a warning message box.")
        )
        self.tools_table.horizontalHeaderItem(1).setToolTip(
            _("Tool Diameter. Its value\n"
              "is the width of the solder paste dispensed."))

        self.addtool_entry_lbl = FCLabel('%s:' % _('New Tool'), bold=True)
        self.addtool_entry_lbl.setToolTip(
            _("Diameter for the new tool to add in the Tool Table")
        )
        self.addtool_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.addtool_entry.set_range(0.0000001, 10000.0000)
        self.addtool_entry.set_precision(self.decimals)
        self.addtool_entry.setSingleStep(0.1)

        self.addtool_btn = QtWidgets.QToolButton()
        self.addtool_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/plus16.png'))
        self.addtool_btn.setToolTip(
            _("Add a new nozzle tool to the Tool Table\n"
              "with the diameter specified above.")
        )

        self.deltool_btn = QtWidgets.QToolButton()
        self.deltool_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/trash16.png'))
        self.deltool_btn.setToolTip(
            _("Delete a selection of tools in the Tool Table\n"
              "by first selecting a row in the Tool Table.")
        )

        tool_grid.addWidget(self.addtool_entry_lbl, 2, 0)
        tool_grid.addWidget(self.addtool_entry, 2, 1)
        tool_grid.addWidget(self.addtool_btn, 2, 2)
        tool_grid.addWidget(self.deltool_btn, 2, 3)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # grid0.addWidget(separator_line, 2, 0, 1, 4)

        # #############################################################################################################
        # General Parameters Frame
        # #############################################################################################################
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.param_label.setToolTip(
            _("Parameters used for this tool.")
        )
        self.tools_box.addWidget(self.param_label)

        par_frame = FCFrame()
        self.tools_box.addWidget(par_frame)

        param_grid = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(param_grid)

        # Margin
        self.margin_label = FCLabel('%s:' % _("Margin"))
        self.margin_label.setToolTip('%s %s' % (
            _("Offset from the boundary."),
            _("Fraction of tool diameter.")
        )
        )
        self.margin_entry = FCDoubleSpinner(suffix='%')
        self.margin_entry.set_range(-100.0000, 100.0000)
        self.margin_entry.set_precision(self.decimals)
        self.margin_entry.setSingleStep(0.1)

        param_grid.addWidget(self.margin_label, 0, 0)
        param_grid.addWidget(self.margin_entry, 0, 1)

        # Z travel
        self.z_travel_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.z_travel_entry.set_range(0.0000001, 10000.0000)
        self.z_travel_entry.set_precision(self.decimals)
        self.z_travel_entry.setSingleStep(0.1)

        self.z_travel_label = FCLabel('%s:' % _("Travel Z"))
        self.z_travel_label.setToolTip(
            _("The height (Z) for travel between pads\n"
              "(without dispensing solder paste).")
        )
        param_grid.addWidget(self.z_travel_label, 2, 0)
        param_grid.addWidget(self.z_travel_entry, 2, 1)

        # #############################################################################################################
        # Dispense Frame
        # #############################################################################################################
        self.disp_lbl = FCLabel('%s' % _("Dispense"), color='tomato', bold=True)
        self.tools_box.addWidget(self.disp_lbl)

        disp_frame = FCFrame()
        self.tools_box.addWidget(disp_frame)

        disp_grid = GLay(v_spacing=5, h_spacing=3)
        disp_frame.setLayout(disp_grid)

        # Z dispense start
        self.z_start_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.z_start_entry.set_range(0.0000001, 10000.0000)
        self.z_start_entry.set_precision(self.decimals)
        self.z_start_entry.setSingleStep(0.1)

        self.z_start_label = FCLabel('%s:' % _("Z Start"))
        self.z_start_label.setToolTip(
            _("The height (Z) when solder paste dispensing starts.")
        )
        disp_grid.addWidget(self.z_start_label, 0, 0)
        disp_grid.addWidget(self.z_start_entry, 0, 1)

        # Z dispense
        self.z_dispense_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.z_dispense_entry.set_range(0.0000001, 10000.0000)
        self.z_dispense_entry.set_precision(self.decimals)
        self.z_dispense_entry.setSingleStep(0.1)

        self.z_dispense_label = FCLabel('%s:' % _("Z Action"))
        self.z_dispense_label.setToolTip(
            _("The height (Z) when doing solder paste dispensing.")
        )
        disp_grid.addWidget(self.z_dispense_label, 2, 0)
        disp_grid.addWidget(self.z_dispense_entry, 2, 1)

        # Z dispense stop
        self.z_stop_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.z_stop_entry.set_range(0.0000001, 10000.0000)
        self.z_stop_entry.set_precision(self.decimals)
        self.z_stop_entry.setSingleStep(0.1)

        self.z_stop_label = FCLabel('%s:' % _("Z Stop"))
        self.z_stop_label.setToolTip(
            _("The height (Z) when solder paste dispensing stops.")
        )
        disp_grid.addWidget(self.z_stop_label, 4, 0)
        disp_grid.addWidget(self.z_stop_entry, 4, 1)

        # #############################################################################################################
        # Toolchange Frame
        # #############################################################################################################
        self.toolchnage_lbl = FCLabel('%s' % _("Tool change"), color='indigo', bold=True)
        self.tools_box.addWidget(self.toolchnage_lbl)

        tc_frame = FCFrame()
        self.tools_box.addWidget(tc_frame)

        tc_grid = GLay(v_spacing=5, h_spacing=3)
        tc_frame.setLayout(tc_grid)

        # X,Y Toolchange location
        self.xy_toolchange_entry = FCEntry()
        self.xy_toolchange_label = FCLabel('%s:' % "X-Y")
        self.xy_toolchange_label.setToolTip(
            _("The X,Y location for tool (nozzle) change.\n"
              "The format is (x, y) where x and y are real numbers.")
        )
        tc_grid.addWidget(self.xy_toolchange_label, 0, 0)
        tc_grid.addWidget(self.xy_toolchange_entry, 0, 1)

        # Z toolchange location
        self.z_toolchange_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.z_toolchange_entry.set_range(0.0000001, 10000.0000)
        self.z_toolchange_entry.set_precision(self.decimals)
        self.z_toolchange_entry.setSingleStep(0.1)

        self.z_toolchange_label = FCLabel('%s:' % "Z")
        self.z_toolchange_label.setToolTip(
            _("The height (Z) for tool (nozzle) change.")
        )
        tc_grid.addWidget(self.z_toolchange_label, 2, 0)
        tc_grid.addWidget(self.z_toolchange_entry, 2, 1)

        # #############################################################################################################
        # Feedrate Frame
        # #############################################################################################################
        fr_lbl = FCLabel('%s' % _("Feedrate"), color='red', bold=True)
        self.tools_box.addWidget(fr_lbl)

        fr_frame = FCFrame()
        self.tools_box.addWidget(fr_frame)

        fr_grid = GLay(v_spacing=5, h_spacing=3)
        fr_frame.setLayout(fr_grid)

        # Feedrate X-Y
        self.frxy_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.frxy_entry.set_range(0.0000, 910000.0000)
        self.frxy_entry.set_precision(self.decimals)
        self.frxy_entry.setSingleStep(0.1)

        self.frxy_label = FCLabel('%s:' % "X-Y")
        self.frxy_label.setToolTip(
            _("Feedrate (speed) while moving on the X-Y plane.")
        )
        fr_grid.addWidget(self.frxy_label, 0, 0)
        fr_grid.addWidget(self.frxy_entry, 0, 1)

        # Feedrate Rapids
        self.frapids_lbl = FCLabel('%s:' % _("Feedrate Rapids"))
        self.frapids_lbl.setToolTip(
            _("Feedrate while moving as fast as possible.")
        )

        self.fr_rapids_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.fr_rapids_entry.set_range(0.0000, 10000.0000)
        self.fr_rapids_entry.set_precision(self.decimals)
        self.fr_rapids_entry.setSingleStep(0.1)

        fr_grid.addWidget(self.frapids_lbl, 2, 0)
        fr_grid.addWidget(self.fr_rapids_entry, 2, 1)

        # Feedrate Z
        self.frz_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.frz_entry.set_range(0.0000, 910000.0000)
        self.frz_entry.set_precision(self.decimals)
        self.frz_entry.setSingleStep(0.1)

        self.frz_label = FCLabel('%s:' % "Z")
        self.frz_label.setToolTip(
            _("Feedrate (speed) while moving vertically\n"
              "(on Z plane).")
        )
        fr_grid.addWidget(self.frz_label, 4, 0)
        fr_grid.addWidget(self.frz_entry, 4, 1)

        # Feedrate Z Dispense
        self.frz_dispense_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.frz_dispense_entry.set_range(0.0000, 910000.0000)
        self.frz_dispense_entry.set_precision(self.decimals)
        self.frz_dispense_entry.setSingleStep(0.1)

        self.frz_dispense_label = FCLabel('%s:' % _("Z Dispense"))
        self.frz_dispense_label.setToolTip(
            _("Feedrate (speed) while moving up vertically\n"
              "to Dispense position (on Z plane).")
        )
        fr_grid.addWidget(self.frz_dispense_label, 6, 0)
        fr_grid.addWidget(self.frz_dispense_entry, 6, 1)

        # #############################################################################################################
        # Spindle Forward Frame
        # #############################################################################################################
        sp_fw_lbl = FCLabel('%s' % _("Forward"), color='blue', bold=True)
        self.tools_box.addWidget(sp_fw_lbl)

        sp_fw_frame = FCFrame()
        self.tools_box.addWidget(sp_fw_frame)

        sp_fw_grid = GLay(v_spacing=5, h_spacing=3)
        sp_fw_frame.setLayout(sp_fw_grid)

        # Spindle Speed Forward
        self.speedfwd_entry = FCSpinner(callback=self.confirmation_message_int)
        self.speedfwd_entry.set_range(0, 999999)
        self.speedfwd_entry.set_step(1000)

        self.speedfwd_label = FCLabel('%s:' % _("Spindle speed"))
        self.speedfwd_label.setToolTip(
            _("The dispenser speed while pushing solder paste\n"
              "through the dispenser nozzle.")
        )
        sp_fw_grid.addWidget(self.speedfwd_label, 0, 0)
        sp_fw_grid.addWidget(self.speedfwd_entry, 0, 1)

        # Dwell Forward
        self.dwellfwd_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.dwellfwd_entry.set_range(0.0000001, 10000.0000)
        self.dwellfwd_entry.set_precision(self.decimals)
        self.dwellfwd_entry.setSingleStep(0.1)

        self.dwellfwd_label = FCLabel('%s:' % _("Dwell"))
        self.dwellfwd_label.setToolTip(
            _("Pause after solder dispensing.")
        )
        sp_fw_grid.addWidget(self.dwellfwd_label, 2, 0)
        sp_fw_grid.addWidget(self.dwellfwd_entry, 2, 1)

        # #############################################################################################################
        # Spindle Reverse Frame
        # #############################################################################################################
        sp_rev_lbl = FCLabel('%s' % _("Reverse"), color='teal', bold=True)
        self.tools_box.addWidget(sp_rev_lbl)

        sp_rev_frame = FCFrame()
        self.tools_box.addWidget(sp_rev_frame)

        sp_rev_grid = GLay(v_spacing=5, h_spacing=3)
        sp_rev_frame.setLayout(sp_rev_grid)

        # Spindle Speed Reverse
        self.speedrev_entry = FCSpinner(callback=self.confirmation_message_int)
        self.speedrev_entry.set_range(0, 999999)
        self.speedrev_entry.set_step(1000)

        self.speedrev_label = FCLabel('%s:' % _("Spindle speed"))
        self.speedrev_label.setToolTip(
            _("The dispenser speed while retracting solder paste\n"
              "through the dispenser nozzle.")
        )
        sp_rev_grid.addWidget(self.speedrev_label, 0, 0)
        sp_rev_grid.addWidget(self.speedrev_entry, 0, 1)

        # Dwell Reverse
        self.dwellrev_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.dwellrev_entry.set_range(0.0000001, 10000.0000)
        self.dwellrev_entry.set_precision(self.decimals)
        self.dwellrev_entry.setSingleStep(0.1)

        self.dwellrev_label = FCLabel('%s:' % _("Dwell"))
        self.dwellrev_label.setToolTip(
            _("Pause after solder paste dispenser retracted,\n"
              "to allow pressure equilibrium.")
        )
        sp_rev_grid.addWidget(self.dwellrev_label, 2, 0)
        sp_rev_grid.addWidget(self.dwellrev_entry, 2, 1)

        # #############################################################################################################
        # General Parameters Frame
        # #############################################################################################################
        self.gen_param_label = FCLabel('%s' % _("Common Parameters"), color='indigo', bold=True)
        self.gen_param_label.setToolTip(
            _("Parameters that are common for all tools.")
        )
        self.tools_box.addWidget(self.gen_param_label)

        gen_par_frame = FCFrame()
        self.tools_box.addWidget(gen_par_frame)

        gen_param_grid = GLay(v_spacing=5, h_spacing=3)
        gen_par_frame.setLayout(gen_param_grid)

        pp_label = FCLabel('%s:' % _('Preprocessor'))
        pp_label.setToolTip(
            _("Files that control the GCode generation.")
        )

        self.pp_combo = FCComboBox()
        gen_param_grid.addWidget(pp_label, 0, 0)
        gen_param_grid.addWidget(self.pp_combo, 0, 1)
        self.calibration.bind_cycle_controls(self)

        # #############################################################################################################
        # Geometry Frame
        # #############################################################################################################
        geo_lbl = FCLabel('%s' % _("Geometry"), color='red', bold=True)
        self.tools_box.addWidget(geo_lbl)

        geo_frame = FCFrame()
        self.tools_box.addWidget(geo_frame)

        geo_grid = GLay(v_spacing=5, h_spacing=3)
        geo_frame.setLayout(geo_grid)

        # Generate Geometry
        self.soldergeo_btn = FCButton(_("Generate Geometry"), bold=True)
        self.soldergeo_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/geometry32.png'))

        self.soldergeo_btn.setToolTip(
            _("Generate solder paste dispensing geometry.")
        )
        geo_grid.addWidget(self.soldergeo_btn, 0, 0, 1, 2)

        # Geometry Object to be used for Solderpaste dispensing
        self.geo_obj_combo = FCComboBox(callback=solder_class.on_rmb_combo)
        self.geo_obj_combo.setModel(self.app.collection)
        self.geo_obj_combo.setRootModelIndex(self.app.collection.index(2, 0, QtCore.QModelIndex()))
        self.geo_obj_combo.is_last = True
        self.geo_obj_combo.obj_type = "Geometry"

        self.geo_obj_combo.setToolTip(
            _("Geometry Solder Paste object.\n"
              "Selecting geometry restores its nozzle settings.\n"
              "Regenerate Geometry after changing quantity or placement.\n"
              "Controller and calibration edits apply when generating CNCJob.")
        )
        geo_grid.addWidget(self.geo_obj_combo, 2, 0, 1, 2)
        settings_note = FCLabel(_("Regenerate Geometry after quantity or placement changes.\n"
                                 "Controller and calibration edits apply to the next CNCJob."))
        settings_note.setWordWrap(True)
        geo_grid.addWidget(settings_note, 3, 0, 1, 2)

        # #############################################################################################################
        # CNCJob Frame
        # #############################################################################################################
        cnc_lbl = FCLabel('%s' % _("CNCJob"), color='brown', bold=True)
        self.tools_box.addWidget(cnc_lbl)

        cnc_frame = FCFrame()
        self.tools_box.addWidget(cnc_frame)

        cnc_grid = GLay(v_spacing=5, h_spacing=3)
        cnc_frame.setLayout(cnc_grid)

        # ## Buttons
        self.solder_gcode_btn = FCButton(_("Generate CNCJob"), bold=True)
        self.solder_gcode_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/cnc16.png'))
        self.solder_gcode_btn.setToolTip(
            _("Generate GCode for Solder Paste dispensing\n"
              "on PCB pads.")
        )
        cnc_grid.addWidget(self.solder_gcode_btn, 0, 0, 1, 2)

        # Gerber Object to be used for solderpaste dispensing
        self.cnc_obj_combo = FCComboBox(callback=solder_class.on_rmb_combo)
        self.cnc_obj_combo.setModel(self.app.collection)
        self.cnc_obj_combo.setRootModelIndex(self.app.collection.index(3, 0, QtCore.QModelIndex()))
        self.cnc_obj_combo.is_last = True
        self.cnc_obj_combo.obj_type = "CNCJob"

        self.cnc_obj_combo.setToolTip(
            _("CNCJob Solder paste object.\n"
              "In order to enable the GCode save section,\n"
              "the name of the object has to end in:\n"
              "'_solderpaste' as a protection.")
        )
        cnc_grid.addWidget(self.cnc_obj_combo, 2, 0, 1, 2)

        # Save and Review GCode
        buttons_hlay = QtWidgets.QHBoxLayout()
        self.solder_gcode_save_btn = FCButton(_("Save GCode"), bold=True)
        self.solder_gcode_save_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.solder_gcode_save_btn.setToolTip(
            _("Save the generated GCode for Solder Paste dispensing\n"
              "on PCB pads, to a file.")
        )

        self.solder_gcode_view_btn = QtWidgets.QToolButton()
        self.solder_gcode_view_btn.setToolTip(_("Review CNC Code."))
        self.solder_gcode_view_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/find32.png'))

        buttons_hlay.addWidget(self.solder_gcode_save_btn)
        buttons_hlay.addWidget(self.solder_gcode_view_btn)
        self.tools_box.addLayout(buttons_hlay)

        GLay.set_common_column_size(
            [geo_grid, fr_grid, tc_grid, disp_grid, tool_grid, sp_fw_grid, sp_rev_grid, param_grid, cnc_grid,
             gen_param_grid], 0)

        self.layout.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

        # action to be added in the combobox context menu
        self.combo_context_del_action = QtGui.QAction(QtGui.QIcon(self.app.resource_location + '/trash16.png'),
                                                      _("Delete Object"))

        # #################################### FINSIHED GUI ###########################
        # #############################################################################

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
