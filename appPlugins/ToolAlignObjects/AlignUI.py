# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 1/13/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtGui, QtCore
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, RadioSet, FCTable, FCCheckBox, FCSpinner

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

# the values of the fit model combo box, in the order of the items
FIT_MODELS = ('rigid', 'similarity', 'affine')


class AlignUI:

    pluginName = _("Align Objects")

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

        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)
        self.level = QtWidgets.QToolButton()
        self.level.setCheckable(True)
        self.level.setToolTip(_("Beginner Mode - many parameters are hidden.\n"
                               "Advanced Mode - full control.\n"
                               "Permanent change is done in 'Preferences' menu."))
        self.title_box.addWidget(self.level)

        # #############################################################################################################
        # Moving Object Frame
        # #############################################################################################################
        self.aligned_label = FCLabel('%s' % _("MOVING object"), color='indigo', bold=True)
        self.aligned_label.setToolTip(
            _("Specify the type of object to be aligned.\n"
              "It can be of type: Gerber, Excellon or Geometry.\n"
              "The selection here decide the type of objects that will be\n"
              "in the Object combobox.")
        )
        self.tools_box.addWidget(self.aligned_label)

        m_frame = FCFrame()
        self.tools_box.addWidget(m_frame)

        # Grid Layout
        grid0 = GLay(v_spacing=5, h_spacing=3)
        m_frame.setLayout(grid0)

        # Type of object to be aligned
        self.type_obj_radio = RadioSet([
            {"label": _("Gerber"), "value": "grb"},
            {"label": _("Excellon"), "value": "exc"},
            {"label": _("Geometry"), "value": "geo"},
        ])

        grid0.addWidget(self.type_obj_radio, 0, 0, 1, 2)

        # Object to be aligned
        self.object_combo = FCComboBox()
        self.object_combo.setModel(self.app.collection)
        self.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.object_combo.is_last = True

        self.object_combo.setToolTip(
            _("Object to be aligned.")
        )

        grid0.addWidget(self.object_combo, 2, 0, 1, 2)

        # #############################################################################################################
        # Destination Object Frame
        # #############################################################################################################
        self.aligner_label = FCLabel('%s' % _("DESTINATION object"), color='red', bold=True)
        self.aligner_label.setToolTip(
            _("Specify the type of object to be aligned to.\n"
              "It can be of type: Gerber, Excellon or Geometry.\n"
              "The selection here decide the type of objects that will be\n"
              "in the Object combobox.")
        )
        self.tools_box.addWidget(self.aligner_label)

        d_frame = FCFrame()
        self.tools_box.addWidget(d_frame)

        # Grid Layout
        grid1 = GLay(v_spacing=5, h_spacing=3)
        d_frame.setLayout(grid1)

        # Type of object to be aligned to = aligner
        self.type_aligner_obj_radio = RadioSet([
            {"label": _("Gerber"), "value": "grb"},
            {"label": _("Excellon"), "value": "exc"},
            {"label": _("Geometry"), "value": "geo"},
        ])

        grid1.addWidget(self.type_aligner_obj_radio, 0, 0, 1, 2)

        # Object to be aligned to = aligner
        self.aligner_object_combo = FCComboBox()
        self.aligner_object_combo.setModel(self.app.collection)
        self.aligner_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.aligner_object_combo.is_last = True

        self.aligner_object_combo.setToolTip(
            _("Object to be aligned to. Aligner.")
        )

        grid1.addWidget(self.aligner_object_combo, 2, 0, 1, 2)

        # #############################################################################################################
        # Parameters Frame
        # #############################################################################################################
        self.param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.tools_box.addWidget(self.param_label)

        par_frame = FCFrame()
        self.tools_box.addWidget(par_frame)

        # Grid Layout
        grid2 = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(grid2)

        # Alignment Type
        self.a_type_lbl = FCLabel('%s:' % _("Alignment Type"), bold=True)
        self.a_type_lbl.setToolTip(
            _("The type of alignment can be:\n"
              "- 1 Point -> it require a single point of sync, the action will be a translation\n"
              "- 2 Points -> it require two points of sync, the action will be translation followed by rotation\n"
              "- 2 Points + Scale -> like 2 Points, with a uniform scale\n"
              "- Best Fit -> it uses any number of points and the selected fit model.\n"
              "- Auto -> it finds the alignment by itself, by matching the hole and pad centers of the 2 objects.")
        )
        self.a_type_radio = RadioSet(
            [
                {'label': _('1 Point'), 'value': 'sp'},
                {'label': _('2 Points'), 'value': 'dp'},
                {'label': _('2 Points + Scale'), 'value': 'dps'},
                {'label': _('Best Fit'), 'value': 'fit'},
                {'label': _('Auto'), 'value': 'auto'}
            ], orientation='vertical')

        grid2.addWidget(self.a_type_lbl, 0, 0, 1, 2)
        grid2.addWidget(self.a_type_radio, 2, 0, 1, 2)

        # Fit model, visible only in the Best Fit and Auto modes
        self.fit_model_lbl = FCLabel('%s:' % _("Fit Model"))
        self.fit_model_lbl.setToolTip(
            _("The transformation that is fitted to the point pairs:\n"
              "- Rigid -> translation and rotation (2 or more pairs)\n"
              "- Similarity -> rigid and a uniform scale (2 or more pairs)\n"
              "- Affine -> rigid, X/Y scale and skew (3 or more pairs, not on one line)\n"
              "In the Auto mode it is the model of the final refine.")
        )
        self.fit_model_combo = FCComboBox()
        self.fit_model_combo.addItems([_("Rigid"), _("Similarity"), _("Affine")])

        grid2.addWidget(self.fit_model_lbl, 4, 0)
        grid2.addWidget(self.fit_model_combo, 4, 1)

        # Allow mirror, visible only in the Auto mode
        self.allow_mirror_cb = FCCheckBox(_("Allow mirror"))
        self.allow_mirror_cb.setToolTip(
            _("Also test the mirrored object, for a layer of the bottom side.")
        )
        grid2.addWidget(self.allow_mirror_cb, 6, 0, 1, 2)

        self.advanced_frame = FCFrame()
        self.tools_box.addWidget(self.advanced_frame)
        advanced_grid = GLay(v_spacing=5, h_spacing=3)
        self.advanced_frame.setLayout(advanced_grid)
        self.limit_pairs_cb = FCCheckBox(_("Limit pairs"))
        self.limit_pairs_cb.setToolTip(_("Limit source landmarks and fitted pairs in Auto mode.\n"
                                        "All features still verify the result. Zero means unlimited."))
        self.limit_pairs_entry = FCSpinner(callback=self.confirmation_message_int)
        self.limit_pairs_entry.set_range(0, 1000000)
        self.limit_pairs_entry.set_value(6)
        self.limit_pairs_entry.setEnabled(False)
        self.limit_pairs_cb.toggled.connect(self.limit_pairs_entry.setEnabled)
        advanced_grid.addWidget(self.limit_pairs_cb, 0, 0)
        advanced_grid.addWidget(self.limit_pairs_entry, 0, 1)

        # #############################################################################################################
        # Points Frame
        # #############################################################################################################
        self.points_label = FCLabel('%s' % _("Points"), color='green', bold=True)
        self.tools_box.addWidget(self.points_label)

        pts_frame = FCFrame()
        self.tools_box.addWidget(pts_frame)

        grid3 = GLay(v_spacing=5, h_spacing=3)
        pts_frame.setLayout(grid3)

        self.points_table = FCTable()
        self.points_table.setColumnCount(6)
        self.points_table.setHorizontalHeaderLabels(
            ['#', _("START X"), _("START Y"), _("DEST X"), _("DEST Y"), _("Error")])
        self.points_table.verticalHeader().setVisible(False)
        self.points_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.points_table.setMinimumHeight(120)
        self.points_table.setToolTip(
            _("The picked point pairs. The Error column shows the distance between the\n"
              "transformed START point and the DESTINATION point after the alignment.")
        )
        grid3.addWidget(self.points_table, 0, 0, 1, 2)

        self.result_label = FCLabel('')
        grid3.addWidget(self.result_label, 2, 0, 1, 2)

        # Buttons
        self.align_object_button = FCButton(_("Align Object"), bold=True)
        self.align_object_button.setIcon(QtGui.QIcon(self.app.resource_location + '/align16.png'))
        self.align_object_button.setToolTip(
            _("Align the specified object to the aligner object.\n"
              "Click the START point on the moving object, then the DESTINATION point on the aligner object.\n"
              "Backspace removes the last point. Esc or right click cancels.")
        )
        self.tools_box.addWidget(self.align_object_button)

        self.find_button = FCButton(_("Find"), bold=True)
        self.find_button.setToolTip(
            _("Search the alignment automatically (Auto mode). The search matches the centers of the\n"
              "holes and pads of the 2 objects.")
        )
        self.tools_box.addWidget(self.find_button)

        self.next_button = FCButton(_("Next candidate"), bold=True)
        self.next_button.setToolTip(
            _("Show the next best alignment that the search found.")
        )
        self.next_button.setEnabled(False)
        self.tools_box.addWidget(self.next_button)

        self.apply_button = FCButton(_("Apply"), bold=True)
        self.apply_button.setToolTip(
            _("Fit the points and align the object (Best Fit mode, the Enter key does the same)\n"
              "or apply the shown candidate (Auto mode).")
        )
        self.apply_button.setEnabled(False)
        self.tools_box.addWidget(self.apply_button)

        self.layout.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

        self.show_mode('sp')
        self.set_level(False)

        # #################################### FINSIHED GUI ###########################
        # #############################################################################

    def set_level(self, checked):
        self.advanced_frame.setVisible(checked)
        self.level.setText(_("Advanced") if checked else _("Beginner"))
        self.level.setStyleSheet("QToolButton { color: %s; }" % ('red' if checked else 'green'))

    def show_fit_model(self, visible):
        """The fit model combo box and the Apply button are used only in the Best Fit mode."""
        self.fit_model_lbl.setVisible(visible)
        self.fit_model_combo.setVisible(visible)
        self.apply_button.setVisible(visible)

    def show_mode(self, mode):
        """Show only the widgets that the alignment mode uses."""
        auto = mode == 'auto'
        self.fit_model_lbl.setVisible(mode in ('fit', 'auto'))
        self.fit_model_combo.setVisible(mode in ('fit', 'auto'))
        self.apply_button.setVisible(mode in ('fit', 'auto'))
        self.allow_mirror_cb.setVisible(auto)
        self.find_button.setVisible(auto)
        self.next_button.setVisible(auto)
        self.align_object_button.setVisible(not auto)

    def get_fit_model(self):
        idx = self.fit_model_combo.currentIndex()
        return FIT_MODELS[idx] if 0 <= idx < len(FIT_MODELS) else FIT_MODELS[0]

    def set_fit_model(self, value):
        self.fit_model_combo.setCurrentIndex(FIT_MODELS.index(value) if value in FIT_MODELS else 0)

    def fill_table(self, start_pts, dest_pts, errors=None):
        """
        Fill the table of the point pairs. A START point without DESTINATION shows empty DESTINATION cells.

        :param start_pts:   list of (x, y)
        :param dest_pts:    list of (x, y), not longer than start_pts
        :param errors:      list of float, one per complete pair, or None
        """
        table = self.points_table
        table.setRowCount(len(start_pts))
        for row, sp in enumerate(start_pts):
            dp = dest_pts[row] if row < len(dest_pts) else None
            cells = [
                str(row + 1),
                '%.*f' % (self.decimals, sp[0]), '%.*f' % (self.decimals, sp[1]),
                '%.*f' % (self.decimals, dp[0]) if dp else '', '%.*f' % (self.decimals, dp[1]) if dp else '',
                '%.*f' % (self.decimals, errors[row]) if errors is not None and row < len(errors) else ''
            ]
            for col, text in enumerate(cells):
                table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

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
