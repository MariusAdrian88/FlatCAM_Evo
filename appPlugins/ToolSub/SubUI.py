# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 4/24/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, FCDoubleSpinner, FCEntry

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class SubUI:

    pluginName = _("Subtract")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout
        units = str(self.app.app_units).lower()

        self.title_box = QtWidgets.QHBoxLayout()
        self.layout.addLayout(self.title_box)

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(
            _("Subtract, intersect or XOR the area of one or more Gerber / Geometry / Excellon objects\n"
              "with a Gerber, Geometry or Excellon target object.")
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

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        # #############################################################################################################
        # TARGET Frame
        # #############################################################################################################
        self.target_label = FCLabel('%s' % _("Target"), color='darkorange', bold=True)
        self.target_label.setToolTip(_("The object that is changed. The result is a new object."))
        self.tools_box.addWidget(self.target_label)

        target_frame = FCFrame()
        self.tools_box.addWidget(target_frame)
        target_grid = GLay(v_spacing=5, h_spacing=3)
        target_frame.setLayout(target_grid)

        self.type_label = FCLabel('%s:' % _("Type"))
        self.type_label.setToolTip(_("The kind of the target object."))
        self.type_combo = FCComboBox()
        self.type_combo.addItem(_("Gerber"), 'gerber')
        self.type_combo.addItem(_("Geometry"), 'geometry')
        self.type_combo.addItem(_("Excellon"), 'excellon')
        target_grid.addWidget(self.type_label, 0, 0)
        target_grid.addWidget(self.type_combo, 0, 1, 1, 2)

        self.target_name_label = FCLabel('%s:' % _("Object"))
        self.target_name_label.setToolTip(_("The target object."))
        self.target_combo = FCComboBox()
        target_grid.addWidget(self.target_name_label, 1, 0)
        target_grid.addWidget(self.target_combo, 1, 1, 1, 2)

        self.target_tools_label = FCLabel('%s:' % _("Apertures / Tools"))
        self.target_tools_label.setToolTip(
            _("The apertures (Gerber) or tools (Excellon, Multigeo Geometry) of the target that are changed.\n"
              "The checked ones are used, the others are copied unchanged."))
        self.target_tools_list = QtWidgets.QListWidget()
        self.target_tools_list.setMaximumHeight(110)
        target_grid.addWidget(self.target_tools_label, 2, 0, 1, 3)
        target_grid.addWidget(self.target_tools_list, 3, 0, 1, 3)

        # #############################################################################################################
        # SUBTRACTORS Frame
        # #############################################################################################################
        self.subs_label = FCLabel('%s' % _("Subtractors"), color='red', bold=True)
        self.subs_label.setToolTip(_("The objects whose area is used on the target. Check one or more."))
        self.tools_box.addWidget(self.subs_label)

        subs_frame = FCFrame()
        self.tools_box.addWidget(subs_frame)
        subs_grid = GLay(v_spacing=5, h_spacing=3)
        subs_frame.setLayout(subs_grid)

        self.subs_list = QtWidgets.QListWidget()
        self.subs_list.setMaximumHeight(130)
        subs_grid.addWidget(self.subs_list, 0, 0, 1, 3)

        self.sub_tools_label = FCLabel('%s:' % _("Apertures / Tools of the highlighted subtractor"))
        self.sub_tools_label.setToolTip(
            _("Highlight a subtractor in the list above, then check the apertures / tools that give its area."))
        self.sub_tools_list = QtWidgets.QListWidget()
        self.sub_tools_list.setMaximumHeight(110)
        subs_grid.addWidget(self.sub_tools_label, 1, 0, 1, 3)
        subs_grid.addWidget(self.sub_tools_list, 2, 0, 1, 3)

        # #############################################################################################################
        # PARAMETERS Frame
        # #############################################################################################################
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.tools_box.addWidget(self.param_label)

        gp_frame = FCFrame()
        self.tools_box.addWidget(gp_frame)
        param_grid = GLay(v_spacing=5, h_spacing=3)
        gp_frame.setLayout(param_grid)

        self.op_label = FCLabel('%s:' % _("Operation"))
        self.op_label.setToolTip(
            _("Subtract: remove the subtractor area from the target.\n"
              "Intersect: keep only the part of the target inside the subtractor area.\n"
              "XOR: the parts that are in only one of them. Not available for Excellon."))
        self.op_combo = FCComboBox()
        self.op_combo.addItem(_("Subtract"), 'subtract')
        self.op_combo.addItem(_("Intersect"), 'intersect')
        self.op_combo.addItem(_("XOR"), 'xor')
        param_grid.addWidget(self.op_label, 0, 0)
        param_grid.addWidget(self.op_combo, 0, 1, 1, 2)

        self.margin_label = FCLabel('%s:' % _("Margin"))
        self.margin_label.setToolTip(
            _("Grow the subtractor area by this distance before it is used.\n"
              "Lines of a Geometry subtractor only give an area with a margin above zero."))
        self.margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.margin_entry.set_range(0.0, 10000.0)
        self.margin_entry.set_precision(self.decimals)
        self.margin_entry.setSingleStep(0.1)
        self.margin_units_label = FCLabel(units)
        param_grid.addWidget(self.margin_label, 1, 0)
        param_grid.addWidget(self.margin_entry, 1, 1)
        param_grid.addWidget(self.margin_units_label, 1, 2)

        self.close_paths_cb = FCCheckBox(_("Close paths"))
        self.close_paths_cb.setToolTip(
            _("Geometry target only: the polygons are united and the cut paths are closed."))
        param_grid.addWidget(self.close_paths_cb, 2, 0, 1, 3)

        self.out_label = FCLabel('%s:' % _("Output name"))
        self.out_label.setToolTip(_("The name of the new object. A number is added if the name exists."))
        self.out_entry = FCEntry()
        param_grid.addWidget(self.out_label, 3, 0)
        param_grid.addWidget(self.out_entry, 3, 1, 1, 2)

        self.keep_target_cb = FCCheckBox(_("Keep target"))
        self.keep_target_cb.setToolTip(
            _("Checked: the target stays. Unchecked: the result replaces the target\n"
              "(the target is deleted and the result gets its name). Undo brings the target back."))
        param_grid.addWidget(self.keep_target_cb, 4, 0, 1, 3)

        self.delete_subs_cb = FCCheckBox(_("Delete subtractors"))
        self.delete_subs_cb.setToolTip(
            _("When checked will delete the subtractor objects\nafter a successful operation. Undo brings them back."))
        param_grid.addWidget(self.delete_subs_cb, 5, 0, 1, 3)

        # #############################################################################################################
        # Buttons
        # #############################################################################################################
        self.preview_button = FCButton(_("Preview"), bold=True)
        self.preview_button.setToolTip(
            _("Draw the subtractor area (with the margin) on the canvas.\n"
              "It is cleared by the next run, Reset or when the plugin is closed."))
        self.tools_box.addWidget(self.preview_button)

        self.apply_button = FCButton(_("Subtract"), bold=True)
        self.apply_button.setIcon(QtGui.QIcon(self.app.resource_location + '/subtract_btn32.png'))
        self.apply_button.setToolTip(_("Run the operation and create the result as a new object."))
        self.tools_box.addWidget(self.apply_button)

        self.tools_box.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.tools_box.addWidget(self.reset_button)

        # the widgets that the Beginner level hides
        self.advanced_widgets = [
            self.target_tools_label, self.target_tools_list, self.sub_tools_label, self.sub_tools_list,
            self.margin_label, self.margin_entry, self.margin_units_label, self.close_paths_cb,
        ]

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
