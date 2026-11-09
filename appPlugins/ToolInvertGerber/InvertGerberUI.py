# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 2/14/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, RadioSet, FCDoubleSpinner

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class InvertUI:

    pluginName = _("Invert Gerber")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.layout.addWidget(title_label)

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)

        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        # Source Object Frame
        self.gerber_label = FCLabel('%s' % _("Source"), color='darkorange', bold=True)
        self.gerber_label.setToolTip(_("Gerber object(s) that will be inverted."))
        self.tools_box.addWidget(self.gerber_label)

        src_frame = FCFrame()
        self.tools_box.addWidget(src_frame)
        src_grid = GLay(v_spacing=5, h_spacing=3)
        src_frame.setLayout(src_grid)

        self.source_label = FCLabel('%s:' % _("Source"))
        self.source_label.setToolTip(
            _("- Object: invert the Gerber object chosen below.\n"
              "- Selection: invert every selected Gerber object,\n"
              "each into its own new object."))
        self.source_radio = RadioSet([
            {'label': _('Object'), 'value': 'object'},
            {'label': _('Selection'), 'value': 'selection'}
        ], compact=True)
        src_grid.addWidget(self.source_label, 0, 0)
        src_grid.addWidget(self.source_radio, 0, 1)

        self.gerber_obj_label = FCLabel('%s:' % _("Object"))
        self.gerber_combo = FCComboBox()
        self.gerber_combo.setModel(self.app.collection)
        self.gerber_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.gerber_combo.is_last = True
        self.gerber_combo.obj_type = "Gerber"
        src_grid.addWidget(self.gerber_obj_label, 2, 0)
        src_grid.addWidget(self.gerber_combo, 2, 1)

        # Reference Frame
        self.reference_title = FCLabel('%s' % _("Reference"), color='green', bold=True)
        self.reference_title.setToolTip(_("The area in which the copper is inverted."))
        self.tools_box.addWidget(self.reference_title)

        ref_frame = FCFrame()
        self.tools_box.addWidget(ref_frame)
        ref_grid = GLay(v_spacing=5, h_spacing=3)
        ref_frame.setLayout(ref_grid)

        self.reference_label = FCLabel('%s:' % _("Reference"))
        self.reference_label.setToolTip(
            _("- Itself: the bounding box of the source object.\n"
              "- Object: the area of another object."))
        self.reference_radio = RadioSet([
            {'label': _('Itself'), 'value': 'itself'},
            {'label': _('Object'), 'value': 'object'}
        ], compact=True)
        ref_grid.addWidget(self.reference_label, 0, 0)
        ref_grid.addWidget(self.reference_radio, 0, 1)

        self.ref_type_label = FCLabel('%s:' % _("Type"))
        self.ref_type_label.setToolTip(_("The type of the reference object: Gerber or Geometry."))
        self.ref_type_combo = FCComboBox()
        self.ref_type_combo.addItems([_("Gerber"), _("Geometry")])
        ref_grid.addWidget(self.ref_type_label, 2, 0)
        ref_grid.addWidget(self.ref_type_combo, 2, 1)

        self.ref_obj_label = FCLabel('%s:' % _("Object"))
        self.ref_combo = FCComboBox()
        self.ref_combo.setModel(self.app.collection)
        self.ref_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ref_combo.is_last = True
        self.ref_combo.obj_type = "Gerber"
        ref_grid.addWidget(self.ref_obj_label, 4, 0)
        ref_grid.addWidget(self.ref_combo, 4, 1)

        self.ref_area_label = FCLabel('%s:' % _("Area"))
        self.ref_area_label.setToolTip(
            _("- Box: the bounding box of the reference object.\n"
              "- Exact: the exact shape of the reference object."))
        self.ref_area_radio = RadioSet([
            {'label': _('Box'), 'value': 'box'},
            {'label': _('Exact'), 'value': 'exact'}
        ], compact=True)
        ref_grid.addWidget(self.ref_area_label, 6, 0)
        ref_grid.addWidget(self.ref_area_radio, 6, 1)

        self.ref_widgets = [self.ref_type_label, self.ref_type_combo, self.ref_obj_label, self.ref_combo,
                            self.ref_area_label, self.ref_area_radio]

        self.source_radio.activated_custom.connect(self.on_source_changed)
        self.reference_radio.activated_custom.connect(self.on_reference_changed)
        self.ref_type_combo.currentIndexChanged.connect(self.on_ref_type_changed)
        # a RadioSet starts with no button checked; set_value() also runs the handlers above
        self.source_radio.set_value('object')
        self.reference_radio.set_value('itself')
        self.ref_area_radio.set_value('box')

        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.param_label.setToolTip('%s.' % _("Parameters for this tool"))
        self.tools_box.addWidget(self.param_label)

        self.gp_frame = FCFrame()
        self.tools_box.addWidget(self.gp_frame)
        grid0 = GLay(v_spacing=5, h_spacing=3)
        self.gp_frame.setLayout(grid0)

        self.margin_label = FCLabel('%s:' % _('Margin'))
        self.margin_label.setToolTip(
            _("Distance by which to avoid\n"
              "the edges of the Gerber object.")
        )
        self.margin_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.margin_entry.set_precision(self.decimals)
        self.margin_entry.set_range(0.0000, 10000.0000)
        self.margin_entry.setObjectName(_("Margin"))
        grid0.addWidget(self.margin_label, 0, 0)
        grid0.addWidget(self.margin_entry, 0, 1)

        self.join_label = FCLabel('%s:  ' % _("Lines Join Style"))
        self.join_label.setToolTip(
            _("The way that the lines in the object outline will be joined.\n"
              "Can be:\n"
              "- rounded -> an arc is added between two joining lines\n"
              "- square -> the lines meet in 90 degrees angle\n"
              "- bevel -> the lines are joined by a third line")
        )
        self.join_radio = RadioSet([
            {'label': _('Rounded'), 'value': 'r'},
            {'label': _('Square'), 'value': 's'},
            {'label': _('Bevel'), 'value': 'b'}
        ], orientation='vertical', compact=True)
        grid0.addWidget(self.join_label, 2, 0)
        grid0.addWidget(self.join_radio, 2, 1)

        self.invert_btn = FCButton(_('Invert Gerber'), bold=True)
        self.invert_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/invert32.png'))
        self.invert_btn.setToolTip(
            _("Will invert the Gerber object: areas that have copper\n"
              "will be empty of copper and previous empty area will be\n"
              "filled with copper.")
        )
        self.tools_box.addWidget(self.invert_btn)
        self.tools_box.addStretch(1)

        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(_("Will reset the tool parameters."))
        self.tools_box.addWidget(self.reset_button)

    def on_source_changed(self, value):
        self.gerber_obj_label.setVisible(value == 'object')
        self.gerber_combo.setVisible(value == 'object')

    def on_reference_changed(self, value):
        for widget in self.ref_widgets:
            widget.setVisible(value == 'object')

    def on_ref_type_changed(self, *args):
        # the project tree roots: 0 Gerber, 1 Excellon, 2 Geometry
        idx = max(0, min(1, self.ref_type_combo.currentIndex()))
        row = (0, 2)[idx]
        self.ref_combo.obj_type = ("Gerber", "Geometry")[idx]
        self.ref_combo.setRootModelIndex(self.app.collection.index(row, 0, QtCore.QModelIndex()))
        self.ref_combo.setCurrentIndex(0)
        self.ref_combo.obj_type = ("Gerber", "Geometry")[row == 2]

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
