# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtGui, QtCore
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, FCDoubleSpinner, RadioSet

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class CompareUI:

    pluginName = _("Compare")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout
        units = str(self.app.app_units).lower()

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(
            _("Compare two objects of the same type (Gerber, Excellon or Geometry).\n"
              "Shows the geometry that is only in Old (removed) and only in New (added)."))
        self.layout.addWidget(title_label)

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        # #############################################################################################################
        # OBJECTS Frame
        # #############################################################################################################
        self.obj_label = FCLabel('%s' % _("Objects"), color='darkorange', bold=True)
        self.tools_box.addWidget(self.obj_label)

        obj_frame = FCFrame()
        self.tools_box.addWidget(obj_frame)
        obj_grid = GLay(v_spacing=5, h_spacing=3)
        obj_frame.setLayout(obj_grid)

        self.type_radio = RadioSet([
            {"label": _("Gerber"), "value": "grb"},
            {"label": _("Excellon"), "value": "exc"},
            {"label": _("Geometry"), "value": "geo"},
        ])
        self.type_radio.setToolTip(_("The type of both objects."))
        obj_grid.addWidget(self.type_radio, 0, 0, 1, 3)

        self.old_label = FCLabel('%s:' % _("Old"))
        self.old_label.setToolTip(_("The reference object."))
        self.old_combo = self._object_combo()
        obj_grid.addWidget(self.old_label, 1, 0)
        obj_grid.addWidget(self.old_combo, 1, 1, 1, 2)

        self.new_label = FCLabel('%s:' % _("New"))
        self.new_label.setToolTip(_("The object that is compared to Old."))
        self.new_combo = self._object_combo()
        obj_grid.addWidget(self.new_label, 2, 0)
        obj_grid.addWidget(self.new_combo, 2, 1, 1, 2)

        self.tol_label = FCLabel('%s:' % _("Tolerance"))
        self.tol_label.setToolTip(
            _("In the units of Old. Geometry within this distance of the other object is never reported\n"
              "as changed. Geometry farther than about 1.011 times this distance is always reported as changed.\n"
              "Between the two the result can go either way."))
        self.tol_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tol_entry.set_range(0.0, 10000.0)
        self.tol_entry.set_precision(self.decimals)
        self.tol_entry.setSingleStep(0.001)
        self.tol_units_label = FCLabel(units)
        obj_grid.addWidget(self.tol_label, 3, 0)
        obj_grid.addWidget(self.tol_entry, 3, 1)
        obj_grid.addWidget(self.tol_units_label, 3, 2)

        # #############################################################################################################
        # DISPLAY Frame
        # #############################################################################################################
        self.display_label = FCLabel('%s' % _("Display"), color='blue', bold=True)
        self.tools_box.addWidget(self.display_label)

        disp_frame = FCFrame()
        self.tools_box.addWidget(disp_frame)
        disp_grid = GLay(v_spacing=5, h_spacing=3)
        disp_frame.setLayout(disp_grid)

        self.removed_cb = FCCheckBox(_("Removed"))
        self.removed_cb.setToolTip(_("Show the geometry that is in Old but not in New."))
        self.removed_swatch = self._swatch()
        self.removed_swatch.setAccessibleName(_("Removed color"))
        disp_grid.addWidget(self.removed_cb, 0, 0)
        disp_grid.addWidget(self.removed_swatch, 0, 1)

        self.added_cb = FCCheckBox(_("Added"))
        self.added_cb.setToolTip(_("Show the geometry that is in New but not in Old."))
        self.added_swatch = self._swatch()
        self.added_swatch.setAccessibleName(_("Added color"))
        disp_grid.addWidget(self.added_cb, 1, 0)
        disp_grid.addWidget(self.added_swatch, 1, 1)

        self.summary_label = FCLabel('')
        self.summary_label.setWordWrap(True)
        disp_grid.addWidget(self.summary_label, 2, 0, 1, 2)

        # #############################################################################################################
        # Buttons
        # #############################################################################################################
        self.compare_button = FCButton(_("Compare"), bold=True)
        self.compare_button.setIcon(QtGui.QIcon(self.app.resource_location + '/compare64.png'))
        self.compare_button.setToolTip(_("Compare the objects. A new click replaces a running comparison."))
        self.tools_box.addWidget(self.compare_button)

        self.tools_box.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(_("Will reset the tool parameters."))
        self.tools_box.addWidget(self.reset_button)

    def _object_combo(self):
        combo = FCComboBox()
        combo.setModel(self.app.collection)
        combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        combo.is_last = True
        combo.obj_type = "Gerber"
        combo.setCurrentIndex(0)
        return combo

    @staticmethod
    def _swatch():
        button = FCButton()
        button.setFixedSize(40, 16)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        button.setToolTip(_("Click to choose the comparison color."))
        return button

    def set_swatch(self, label, rgb):
        label.setStyleSheet("background-color:%s; border: 1px solid gray;" % rgb)

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
