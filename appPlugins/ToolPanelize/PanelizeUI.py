from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    RadioSet, FCDoubleSpinner, FCSpinner, OptionalInputSection

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class PanelizeUI:

    pluginName = _("Panelization")

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
        # Source Object Frame
        # #############################################################################################################
        self.object_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.object_label.setToolTip(
            _("Specify the type of object to be panelized\n"
              "It can be of type: Gerber, Excellon or Geometry.\n"
              "The selection here decide the type of objects that will be\n"
              "in the Object combobox.")
        )
        self.tools_box.addWidget(self.object_label)

        obj_frame = FCFrame()
        self.tools_box.addWidget(obj_frame)

        # Grid Layout
        grid0 = GLay(v_spacing=5, h_spacing=3)
        obj_frame.setLayout(grid0)

        # Type of object to be panelized
        self.type_object_label = FCLabel('%s:' % _("Target"))

        self.type_obj_combo = FCComboBox()
        self.type_obj_combo.addItems([_("Gerber"), _("Excellon"), _("Geometry")])
        self.type_obj_combo.setItemIcon(0, QtGui.QIcon(self.app.resource_location + "/flatcam_icon16.png"))
        self.type_obj_combo.setItemIcon(1, QtGui.QIcon(self.app.resource_location + "/drill16.png"))
        self.type_obj_combo.setItemIcon(2, QtGui.QIcon(self.app.resource_location + "/geometry16.png"))

        grid0.addWidget(self.type_object_label, 2, 0)
        grid0.addWidget(self.type_obj_combo, 2, 1)

        # Object to be panelized
        self.object_combo = FCComboBox()
        self.object_combo.setModel(self.app.collection)
        self.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.object_combo.is_last = False

        self.object_combo.setToolTip(
            _("Object to be panelized. This means that it will\n"
              "be duplicated in an array of rows and columns.")
        )

        grid0.addWidget(self.object_combo, 4, 0, 1, 2)

        # #############################################################################################################
        # Reference Object Frame
        # #############################################################################################################
        # Type of box Panel object
        self.box_label = FCLabel('%s' % _("Reference"), color='green', bold=True)
        self.box_label.setToolTip(
            _("Choose the reference for panelization:\n"
              "- Object = the bounding box of a different object\n"
              "- Bounding Box = the bounding box of the object to be panelized\n"
              "\n"
              "The reference is useful when doing panelization for more than one\n"
              "object. The spacings (really offsets) will be applied in reference\n"
              "to this reference object therefore maintaining the panelized\n"
              "objects in sync.")
        )
        self.tools_box.addWidget(self.box_label)

        pr_frame = FCFrame()
        self.tools_box.addWidget(pr_frame)

        # Grid Layout
        grid1 = GLay(v_spacing=5, h_spacing=3)
        pr_frame.setLayout(grid1)

        self.reference_radio = RadioSet([{'label': _('Object'), 'value': 'object'},
                                         {'label': _('Bounding Box'), 'value': 'bbox'}])
        grid1.addWidget(self.reference_radio, 0, 0, 1, 2)

        # Type of Box Object to be used as an envelope for panelization
        self.type_box_combo = FCComboBox()
        self.type_box_combo.addItems([_("Gerber"), _("Geometry")])

        # we get rid of item1 ("Excellon") as it is not suitable for use as a "box" for panelizing
        # self.type_box_combo.view().setRowHidden(1, True)
        self.type_box_combo.setItemIcon(0, QtGui.QIcon(self.app.resource_location + "/flatcam_icon16.png"))
        self.type_box_combo.setItemIcon(1, QtGui.QIcon(self.app.resource_location + "/geometry16.png"))

        self.type_box_combo_label = FCLabel('%s:' % _("Box Type"))
        self.type_box_combo_label.setToolTip(
            _("Specify the type of object to be used as an container for\n"
              "panelization. It can be: Gerber or Geometry type.\n"
              "The selection here decide the type of objects that will be\n"
              "in the Box Object combobox.")
        )
        grid1.addWidget(self.type_box_combo_label, 2, 0)
        grid1.addWidget(self.type_box_combo, 2, 1)

        # Box
        self.box_combo = FCComboBox()
        self.box_combo.setModel(self.app.collection)
        self.box_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        # self.box_combo.is_last = True

        self.box_combo.setToolTip(
            _("The actual object that is used as container for the\n "
              "selected object that is to be panelized.")
        )
        grid1.addWidget(self.box_combo, 4, 0, 1, 2)

        # #############################################################################################################
        # Panel Data Frame
        # #############################################################################################################
        panel_data_label = FCLabel('%s' % _("Panel Data"), color='red', bold=True)
        panel_data_label.setToolTip(
            _("This informations will shape the resulting panel.\n"
              "The number of rows and columns will set how many\n"
              "duplicates of the original geometry will be generated.\n"
              "\n"
              "The spacings will set the distance between any two\n"
              "elements of the panel array.")
        )
        self.tools_box.addWidget(panel_data_label)

        pd_frame = FCFrame()
        self.tools_box.addWidget(pd_frame)

        grid2 = GLay(v_spacing=5, h_spacing=3)
        pd_frame.setLayout(grid2)

        # Spacing Columns
        self.spacing_columns = FCDoubleSpinner(callback=self.confirmation_message)
        self.spacing_columns.set_range(0, 9999)
        self.spacing_columns.set_precision(4)

        self.spacing_columns_label = FCLabel('%s:' % _("Spacing cols"))
        self.spacing_columns_label.setToolTip(
            _("Spacing between columns.\n"
              "In current units.")
        )
        grid2.addWidget(self.spacing_columns_label, 0, 0)
        grid2.addWidget(self.spacing_columns, 0, 1)

        # Spacing Rows
        self.spacing_rows = FCDoubleSpinner(callback=self.confirmation_message)
        self.spacing_rows.set_range(0, 9999)
        self.spacing_rows.set_precision(4)

        self.spacing_rows_label = FCLabel('%s:' % _("Spacing rows"))
        self.spacing_rows_label.setToolTip(
            _("Spacing between rows.\n"
              "In current units.")
        )
        grid2.addWidget(self.spacing_rows_label, 2, 0)
        grid2.addWidget(self.spacing_rows, 2, 1)

        # Columns
        self.columns = FCSpinner(callback=self.confirmation_message_int)
        self.columns.set_range(0, 10000)

        self.columns_label = FCLabel('%s:' % _("Columns"))
        self.columns_label.setToolTip(
            _("Number of columns")
        )
        grid2.addWidget(self.columns_label, 4, 0)
        grid2.addWidget(self.columns, 4, 1)

        # Rows
        self.rows = FCSpinner(callback=self.confirmation_message_int)
        self.rows.set_range(0, 10000)

        self.rows_label = FCLabel('%s:' % _("Rows"))
        self.rows_label.setToolTip(
            _("Number of rows")
        )
        grid2.addWidget(self.rows_label, 6, 0)
        grid2.addWidget(self.rows, 6, 1)

        # #############################################################################################################
        # COMMON PARAMETERS Frame
        # #############################################################################################################
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.param_label.setToolTip(_("Parameters that are common for all tools."))
        self.tools_box.addWidget(self.param_label)

        self.gp_frame = FCFrame()
        self.tools_box.addWidget(self.gp_frame)

        grid3 = GLay(v_spacing=5, h_spacing=3)
        self.gp_frame.setLayout(grid3)

        # Type of resulting Panel object
        self.panel_type_radio = RadioSet([{'label': _('Gerber'), 'value': 'gerber'},
                                          {'label': _('Geo'), 'value': 'geometry'}])
        self.panel_type_label = FCLabel('%s:' % _("Panel Type"), bold=True)
        self.panel_type_label.setToolTip(
            _("Choose the type of object for the panel object:\n"
              "- Gerber\n"
              "- Geometry")
        )
        grid3.addWidget(self.panel_type_label, 0, 0)
        grid3.addWidget(self.panel_type_radio, 0, 1)

        # Path optimization
        self.optimization_cb = FCCheckBox('%s' % _("Path Optimization"))
        self.optimization_cb.setToolTip(
            _("Active only for Geometry panel type.\n"
              "When checked the application will find\n"
              "any two overlapping Line elements in the panel\n"
              "and will remove the overlapping parts, keeping only one of them.")
        )
        grid3.addWidget(self.optimization_cb, 2, 0, 1, 2)

        # Step and Repeat
        self.step_repeat_cb = FCCheckBox('%s' % _("Step and Repeat"))
        self.step_repeat_cb.setToolTip(
            _("Active only for a Gerber panel of a Gerber object.\n"
              "When checked, the source of the panel has one copy\n"
              "inside a Gerber Step and Repeat block (SR command).\n"
              "The source is small and fast to make.\n"
              "A reader without Step and Repeat support cannot read it.")
        )
        grid3.addWidget(self.step_repeat_cb, 3, 0, 1, 2)

        # Constrains
        self.constrain_cb = FCCheckBox('%s:' % _("Constrain panel within"))
        self.constrain_cb.setToolTip(
            _("Area define by DX and DY within to constrain the panel.\n"
              "DX and DY values are in current units.\n"
              "Regardless of how many columns and rows are desired,\n"
              "the final panel will have as many columns and rows as\n"
              "they fit completely within selected area.")
        )
        grid3.addWidget(self.constrain_cb, 4, 0, 1, 2)

        self.x_width_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.x_width_entry.set_precision(self.decimals)
        self.x_width_entry.set_range(0.0000, 10000.0000)

        self.x_width_lbl = FCLabel('%s:' % _("Width (DX)"))
        self.x_width_lbl.setToolTip(
            _("The width (DX) within which the panel must fit.\n"
              "In current units.")
        )
        grid3.addWidget(self.x_width_lbl, 6, 0)
        grid3.addWidget(self.x_width_entry, 6, 1)

        self.y_height_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.y_height_entry.set_range(0, 10000)
        self.y_height_entry.set_precision(self.decimals)

        self.y_height_lbl = FCLabel('%s:' % _("Height (DY)"))
        self.y_height_lbl.setToolTip(
            _("The height (DY)within which the panel must fit.\n"
              "In current units.")
        )
        grid3.addWidget(self.y_height_lbl, 8, 0)
        grid3.addWidget(self.y_height_entry, 8, 1)

        self.constrain_sel = OptionalInputSection(
            self.constrain_cb, [self.x_width_lbl, self.x_width_entry, self.y_height_lbl, self.y_height_entry])

        self.separator_line = QtWidgets.QFrame()
        self.separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid3.addWidget(self.separator_line, 10, 0, 1, 2)

        GLay.set_common_column_size([grid0, grid1, grid2, grid3], 0)

        # #############################################################################################################
        # Generate Panel Button
        # #############################################################################################################
        self.panelize_object_button = FCButton(_("Panelize Object"), bold=True)
        self.panelize_object_button.setIcon(QtGui.QIcon(self.app.resource_location + '/panelize16.png'))
        self.panelize_object_button.setToolTip(
            _("Panelize the specified object around the specified box.\n"
              "In other words it creates multiple copies of the source object,\n"
              "arranged in a 2D array of rows and columns.")
        )
        self.tools_box.addWidget(self.panelize_object_button)

        self.layout.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

        # #################################### FINSIHED GUI ###########################
        # #############################################################################
        self.panel_type_radio.activated_custom.connect(self.on_panel_type)

    def on_panel_type(self, val):
        if val == 'geometry':
            self.optimization_cb.setDisabled(False)
        else:
            self.optimization_cb.setDisabled(True)

    def set_reference(self, current_val):
        if current_val == 'object':
            self.type_box_combo.setDisabled(False)
            self.type_box_combo_label.setDisabled(False)
            self.box_combo.setDisabled(False)
        else:
            self.type_box_combo.setDisabled(True)
            self.type_box_combo_label.setDisabled(True)
            self.box_combo.setDisabled(True)

    def set_level(self, checked):
        if not checked:
            self.level.setText('%s' % _('Beginner'))
            self.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)
            self.param_label.hide()
            self.gp_frame.hide()
        else:
            self.level.setText('%s' % _('Advanced'))
            self.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)
            self.param_label.show()
            self.gp_frame.show()

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
