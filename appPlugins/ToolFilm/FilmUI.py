"""Widgets and presentation for the Film plugin."""

from PyQt6 import QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    FCComboBox2, RadioSet, FCDoubleSpinner, FCSpinner, OptionalHideInputSection
from reportlab.lib.units import inch, mm

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class FilmUI:

    pluginName = _("Film")

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
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(_("Create a positive/negative film for UV exposure."))
        self.title_box.addWidget(title_label)
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(_(
            "Beginner Mode - many parameters are hidden.\n"
            "Advanced Mode - full control.\n"
            "Permanent change is done in 'Preferences' menu."))
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        # Source Object
        self.obj_combo_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.obj_combo_label.setToolTip(_("Excellon object for drilling/milling operation."))
        self.tools_box.addWidget(self.obj_combo_label)
        obj_frame = FCFrame()
        self.tools_box.addWidget(obj_frame)
        obj_grid = GLay(v_spacing=5, h_spacing=3)
        obj_frame.setLayout(obj_grid)
        self.tf_type_obj_combo = RadioSet([{'label': _('Gerber'), 'value': 'grb'},
                                           {'label': _('Geometry'), 'value': 'geo'}])
        self.tf_type_obj_combo_label = FCLabel('%s:' % _("Type"))
        self.tf_type_obj_combo_label.setToolTip(
            _("Specify the type of object for which to create the film.\n"
              "The object can be of type: Gerber or Geometry.\n"
              "The selection here decide the type of objects that will be\n"
              "in the Film Object combobox."))
        obj_grid.addWidget(self.tf_type_obj_combo_label, 0, 0)
        obj_grid.addWidget(self.tf_type_obj_combo, 0, 1)
        self.tf_object_combo = FCComboBox()
        self.tf_object_combo.setModel(self.app.collection)
        self.tf_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.tf_object_combo.is_last = True
        obj_grid.addWidget(self.tf_object_combo, 2, 0, 1, 2)
        self.tf_type_box_combo = RadioSet([{'label': _('Gerber'), 'value': 'grb'},
                                           {'label': _('Geometry'), 'value': 'geo'}])
        self.tf_type_box_combo_label = FCLabel('%s:' % _("Box Type"))
        self.tf_type_box_combo_label.setToolTip(
            _("Specify the type of object to be used as an container for\n"
              "film creation. It can be: Gerber or Geometry type."
              "The selection here decide the type of objects that will be\n"
              "in the Box Object combobox."))
        obj_grid.addWidget(self.tf_type_box_combo_label, 4, 0)
        obj_grid.addWidget(self.tf_type_box_combo, 4, 1)
        self.tf_box_combo = FCComboBox()
        self.tf_box_combo.setModel(self.app.collection)
        self.tf_box_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.tf_box_combo.is_last = True
        obj_grid.addWidget(self.tf_box_combo, 6, 0, 1, 2)

        # Adjustments
        self.film_adj_label = FCLabel('%s' % _("Adjustments"), color='green', bold=True)
        self.film_adj_label.setToolTip(_("Compensate print distortions."))
        self.tools_box.addWidget(self.film_adj_label)
        self.adj_frame = FCFrame()
        self.tools_box.addWidget(self.adj_frame)
        adj_grid = GLay(v_spacing=5, h_spacing=3)
        self.adj_frame.setLayout(adj_grid)
        self.film_scale_cb = FCCheckBox('%s' % _("Scale"))
        self.film_scale_cb.setToolTip(
            _("A value greater than 1 will compact the film\n"
              "while a value less than 1 will jolt it."))
        adj_grid.addWidget(self.film_scale_cb, 2, 0, 1, 2)
        self.film_scale_type_lbl = FCLabel('%s:' % _("Type"))
        self.film_scale_type_lbl.setToolTip(
            _("'Length' -> scale by a length value\n"
              "'Factor' -> scale by a ratio"))
        self.film_scale_type_combo = FCComboBox2()
        self.film_scale_type_combo.addItems([_("Length"), _("Factor")])
        adj_grid.addWidget(self.film_scale_type_lbl, 4, 0)
        adj_grid.addWidget(self.film_scale_type_combo, 4, 1)
        self.film_scalex_label = FCLabel('%s:' % _("X val"))
        self.film_scalex_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.film_scalex_entry.set_range(-10000.0000, 10000.0000)
        self.film_scalex_entry.set_precision(self.decimals)
        self.film_scalex_entry.setSingleStep(0.01)
        adj_grid.addWidget(self.film_scalex_label, 6, 0)
        adj_grid.addWidget(self.film_scalex_entry, 6, 1)
        self.film_scaley_label = FCLabel('%s:' % _("Y val"))
        self.film_scaley_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.film_scaley_entry.set_range(-10000.0000, 10000.0000)
        self.film_scaley_entry.set_precision(self.decimals)
        self.film_scaley_entry.setSingleStep(0.01)
        adj_grid.addWidget(self.film_scaley_label, 7, 0)
        adj_grid.addWidget(self.film_scaley_entry, 7, 1)
        self.scale_ref_label = FCLabel('%s:' % _("Reference"))
        self.scale_ref_label.setToolTip(_("The reference point to be used as origin for the adjustment."))
        self.scale_ref_combo = FCComboBox2()
        self.scale_ref_combo.addItems(
            [_('Center'), _('Bottom Left'), _('Top Left'), _('Bottom Right'), _('Top right')])
        adj_grid.addWidget(self.scale_ref_label, 10, 0)
        adj_grid.addWidget(self.scale_ref_combo, 10, 1)
        self.ois_scale = OptionalHideInputSection(self.film_scale_cb,
                                                  [self.film_scale_type_lbl, self.film_scale_type_combo,
                                                   self.film_scalex_label, self.film_scalex_entry,
                                                   self.film_scaley_label, self.film_scaley_entry,
                                                   self.scale_ref_label, self.scale_ref_combo])
        self.scale_separator_line = QtWidgets.QFrame()
        self.scale_separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.scale_separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        adj_grid.addWidget(self.scale_separator_line, 12, 0, 1, 2)

        self.film_skew_cb = FCCheckBox('%s' % _("Skew"))
        self.film_skew_cb.setToolTip(
            _("Positive values will skew to the right\n"
              "while negative values will skew to the left."))
        adj_grid.addWidget(self.film_skew_cb, 14, 0, 1, 2)
        self.film_skew_type_lbl = FCLabel('%s:' % _("Type"))
        self.film_skew_type_lbl.setToolTip(
            _("'Length' -> deform by a length value\n"
              "'Angle' -> deform by an angle\n"
              "'Ratio' -> deform by a ratio between what should be and what is"))
        self.film_skew_type_combo = FCComboBox2()
        self.film_skew_type_combo.addItems([_("Length"), _("Angle"), _("Factor")])
        adj_grid.addWidget(self.film_skew_type_lbl, 16, 0)
        adj_grid.addWidget(self.film_skew_type_combo, 16, 1)
        self.film_skewx_label = FCLabel('%s:' % _("X val"))
        self.film_skewx_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.film_skewx_entry.set_range(-10000.0000, 10000.0000)
        self.film_skewx_entry.set_precision(self.decimals)
        self.film_skewx_entry.setSingleStep(0.01)
        adj_grid.addWidget(self.film_skewx_label, 18, 0)
        adj_grid.addWidget(self.film_skewx_entry, 18, 1)
        self.film_skewy_label = FCLabel('%s:' % _("Y val"))
        self.film_skewy_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.film_skewy_entry.set_range(-10000.0000, 10000.0000)
        self.film_skewy_entry.set_precision(self.decimals)
        self.film_skewy_entry.setSingleStep(0.01)
        adj_grid.addWidget(self.film_skewy_label, 20, 0)
        adj_grid.addWidget(self.film_skewy_entry, 20, 1)
        self.skew_ref_label = FCLabel('%s:' % _("Reference"))
        self.skew_ref_label.setToolTip(_("The reference point to be used as origin for the adjustment."))
        self.skew_ref_combo = FCComboBox2()
        self.skew_ref_combo.addItems([_('Center'), _('Bottom Left')])
        adj_grid.addWidget(self.skew_ref_label, 22, 0)
        adj_grid.addWidget(self.skew_ref_combo, 22, 1)
        self.ois_skew = OptionalHideInputSection(self.film_skew_cb,
                                                 [self.film_skew_type_lbl, self.film_skew_type_combo,
                                                  self.film_skewx_label, self.film_skewx_entry,
                                                  self.film_skewy_label, self.film_skewy_entry,
                                                  self.skew_ref_label, self.skew_ref_combo])
        self.skew_separator_line1 = QtWidgets.QFrame()
        self.skew_separator_line1.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.skew_separator_line1.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        adj_grid.addWidget(self.skew_separator_line1, 24, 0, 1, 2)
        self.film_mirror_cb = FCCheckBox('%s' % _("Mirror"))
        self.film_mirror_cb.setToolTip(_("Mirror the film geometry on the selected axis or on both."))
        adj_grid.addWidget(self.film_mirror_cb, 26, 0, 1, 2)
        self.film_mirror_axis = RadioSet([{'label': _('X'), 'value': 'x'},
                                          {'label': _('Y'), 'value': 'y'},
                                          {'label': _('Both'), 'value': 'both'}], compact=True)
        self.film_mirror_axis_label = FCLabel('%s:' % _("Axis"))
        self.film_mirror_axis_label.setToolTip(_("Mirror the film geometry on the selected axis or on both."))
        adj_grid.addWidget(self.film_mirror_axis_label, 28, 0)
        adj_grid.addWidget(self.film_mirror_axis, 28, 1)
        self.ois_mirror = OptionalHideInputSection(self.film_mirror_cb,
                                                   [self.film_mirror_axis_label, self.film_mirror_axis])

        # Parameters
        self.film_param_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.tools_box.addWidget(self.film_param_label)
        par_frame = FCFrame()
        self.tools_box.addWidget(par_frame)
        param_grid = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(param_grid)
        self.convex_box_label = FCLabel('%s:' % _("Convex Shape"))
        self.convex_box_label.setToolTip(
            _("Create a convex shape surrounding the entire PCB.\n"
              "If not checked the shape is rectangular."))
        self.convex_box_cb = FCCheckBox()
        param_grid.addWidget(self.convex_box_label, 0, 0)
        param_grid.addWidget(self.convex_box_cb, 0, 1)
        self.rounded_label = FCLabel('%s:' % _("Rounded"))
        self.rounded_label.setToolTip(_("Resulting geometry will have rounded corners."))
        self.rounded_cb = FCCheckBox()
        param_grid.addWidget(self.rounded_label, 2, 0)
        param_grid.addWidget(self.rounded_cb, 2, 1)
        self.film_scale_stroke_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.film_scale_stroke_entry.set_range(-999.9999, 999.9999)
        self.film_scale_stroke_entry.setSingleStep(0.01)
        self.film_scale_stroke_entry.set_precision(self.decimals)
        self.film_scale_stroke_label = FCLabel('%s:' % _("Scale Stroke"))
        self.film_scale_stroke_label.setToolTip(
            _("Scale the line stroke thickness of each feature in the SVG file.\n"
              "It means that the line that envelope each SVG feature will be thicker or thinner,\n"
              "therefore the fine features may be more affected by this parameter."))
        param_grid.addWidget(self.film_scale_stroke_label, 4, 0)
        param_grid.addWidget(self.film_scale_stroke_entry, 4, 1)
        self.film_type = RadioSet([{'label': _('Positive'), 'value': 'pos'},
                                   {'label': _('Negative'), 'value': 'neg'}], compact=True)
        self.film_type_label = FCLabel('%s:' % _("Polarity"))
        self.film_type_label.setToolTip(_("Generate a Positive black film or a Negative film."))
        param_grid.addWidget(self.film_type_label, 6, 0)
        param_grid.addWidget(self.film_type, 6, 1)
        self.boundary_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.boundary_entry.set_range(-999.9999, 999.9999)
        self.boundary_entry.setSingleStep(0.01)
        self.boundary_entry.set_precision(self.decimals)
        self.boundary_label = FCLabel('%s:' % _("Border"))
        self.boundary_label.setToolTip(
            _("Specify a border around the object.\n"
              "Only for negative film.\n"
              "It helps if we use as a Box Object the same \n"
              "object as in Film Object. It will create a thick\n"
              "black bar around the actual print allowing for a\n"
              "better delimitation of the outline features which are of\n"
              "white color like the rest and which may confound with the\n"
              "surroundings if not for this border."))
        param_grid.addWidget(self.boundary_label, 8, 0)
        param_grid.addWidget(self.boundary_entry, 8, 1)
        self.boundary_label.hide()
        self.boundary_entry.hide()
        self.punch_cb = FCCheckBox(_("Punch drill holes"))
        self.punch_cb.setToolTip(_("When checked the generated film will have holes in pads when\n"
                                   "the generated film is positive. This is done to help drilling,\n"
                                   "when done manually."))
        param_grid.addWidget(self.punch_cb, 10, 0, 1, 2)
        self.punch_frame = QtWidgets.QFrame()
        self.punch_frame.setContentsMargins(0, 0, 0, 0)
        param_grid.addWidget(self.punch_frame, 12, 0, 1, 2)
        punch_grid = GLay(v_spacing=5, h_spacing=3)
        punch_grid.setContentsMargins(0, 0, 0, 0)
        self.punch_frame.setLayout(punch_grid)
        self.ois_p = OptionalHideInputSection(self.punch_cb, [self.punch_frame])
        self.source_label = FCLabel('%s:' % _("Source"))
        self.source_label.setToolTip(
            _("The punch hole source can be:\n"
              "- Excellon -> an Excellon holes center will serve as reference.\n"
              "- Pad Center -> will try to use the pads center as reference."))
        self.source_punch = RadioSet([{'label': _('Excellon'), 'value': 'exc'},
                                      {'label': _('Pad center'), 'value': 'pad'}], compact=True)
        punch_grid.addWidget(self.source_label, 0, 0)
        punch_grid.addWidget(self.source_punch, 0, 1)
        self.exc_combo = FCComboBox()
        self.exc_combo.setToolTip(_("Remove the geometry of Excellon from the Film to create the holes in pads."))
        self.exc_combo.setModel(self.app.collection)
        self.exc_combo.setRootModelIndex(self.app.collection.index(1, 0, QtCore.QModelIndex()))
        self.exc_combo.is_last = True
        self.exc_combo.obj_type = "Excellon"
        punch_grid.addWidget(self.exc_combo, 2, 0, 1, 2)
        self.exc_combo.hide()
        self.punch_size_label = FCLabel('%s:' % _("Punch Size"))
        self.punch_size_label.setToolTip(_("The value here will control how big is the punch hole in the pads."))
        self.punch_size_spinner = FCDoubleSpinner(callback=self.confirmation_message)
        self.punch_size_spinner.set_range(0, 999.9999)
        self.punch_size_spinner.setSingleStep(0.1)
        self.punch_size_spinner.set_precision(self.decimals)
        punch_grid.addWidget(self.punch_size_label, 4, 0)
        punch_grid.addWidget(self.punch_size_spinner, 4, 1)
        self.punch_size_label.hide()
        self.punch_size_spinner.hide()

        # Export
        self.export_label = FCLabel('%s' % _("Export"), color='red', bold=True)
        self.tools_box.addWidget(self.export_label)
        exp_frame = FCFrame()
        self.tools_box.addWidget(exp_frame)
        export_grid = GLay(v_spacing=5, h_spacing=3)
        exp_frame.setLayout(export_grid)
        self.file_type_label = FCLabel('%s:' % _("Film Type"))
        self.file_type_label.setToolTip(
            _("The file type of the saved film. Can be:\n"
              "- 'SVG' -> open-source vectorial format\n"
              "- 'PNG' -> raster image\n"
              "- 'PDF' -> portable document format"))
        self.file_type_radio = RadioSet([{'label': _('SVG'), 'value': 'svg'},
                                         {'label': _('PNG'), 'value': 'png'},
                                         {'label': _('PDF'), 'value': 'pdf'}], compact=True)
        export_grid.addWidget(self.file_type_label, 0, 0)
        export_grid.addWidget(self.file_type_radio, 0, 1)
        self.orientation_label = FCLabel('%s:' % _("Page Orientation"))
        self.orientation_label.setToolTip(_("Can be:\n- Portrait\n- Landscape"))
        self.orientation_radio = RadioSet([{'label': _('Portrait'), 'value': 'p'},
                                           {'label': _('Landscape'), 'value': 'l'}], compact=True)
        export_grid.addWidget(self.orientation_label, 2, 0)
        export_grid.addWidget(self.orientation_radio, 2, 1)
        self.pagesize_label = FCLabel('%s:' % _("Page Size"))
        self.pagesize_label.setToolTip(_("A selection of standard ISO 216 page sizes."))
        self.pagesize_combo = FCComboBox()
        self.pagesize = {
            'Bounds': None,
            'A0': (841 * mm, 1189 * mm), 'A1': (594 * mm, 841 * mm),
            'A2': (420 * mm, 594 * mm), 'A3': (297 * mm, 420 * mm),
            'A4': (210 * mm, 297 * mm), 'A5': (148 * mm, 210 * mm),
            'A6': (105 * mm, 148 * mm), 'A7': (74 * mm, 105 * mm),
            'A8': (52 * mm, 74 * mm), 'A9': (37 * mm, 52 * mm), 'A10': (26 * mm, 37 * mm),
            'B0': (1000 * mm, 1414 * mm), 'B1': (707 * mm, 1000 * mm),
            'B2': (500 * mm, 707 * mm), 'B3': (353 * mm, 500 * mm),
            'B4': (250 * mm, 353 * mm), 'B5': (176 * mm, 250 * mm),
            'B6': (125 * mm, 176 * mm), 'B7': (88 * mm, 125 * mm),
            'B8': (62 * mm, 88 * mm), 'B9': (44 * mm, 62 * mm), 'B10': (31 * mm, 44 * mm),
            'C0': (917 * mm, 1297 * mm), 'C1': (648 * mm, 917 * mm),
            'C2': (458 * mm, 648 * mm), 'C3': (324 * mm, 458 * mm),
            'C4': (229 * mm, 324 * mm), 'C5': (162 * mm, 229 * mm),
            'C6': (114 * mm, 162 * mm), 'C7': (81 * mm, 114 * mm),
            'C8': (57 * mm, 81 * mm), 'C9': (40 * mm, 57 * mm), 'C10': (28 * mm, 40 * mm),
            'LETTER': (8.5 * inch, 11 * inch), 'LEGAL': (8.5 * inch, 14 * inch),
            'ELEVENSEVENTEEN': (11 * inch, 17 * inch), 'JUNIOR_LEGAL': (5 * inch, 8 * inch),
            'HALF_LETTER': (5.5 * inch, 8 * inch), 'GOV_LETTER': (8 * inch, 10.5 * inch),
            'GOV_LEGAL': (8.5 * inch, 13 * inch), 'LEDGER': (17 * inch, 11 * inch),
        }
        self.pagesize_combo.addItems(list(self.pagesize.keys()))
        export_grid.addWidget(self.pagesize_label, 4, 0)
        export_grid.addWidget(self.pagesize_combo, 4, 1)
        self.on_film_type(val='hide')
        self.png_dpi_label = FCLabel('%s:' % "PNG DPI")
        self.png_dpi_label.setToolTip(
            _("Default value is 300 DPI, the usual resolution for print. Change this value to scale the PNG file."))
        self.png_dpi_spinner = FCSpinner(callback=self.confirmation_message_int)
        self.png_dpi_spinner.set_range(0, 100000)
        export_grid.addWidget(self.png_dpi_label, 6, 0)
        export_grid.addWidget(self.png_dpi_spinner, 6, 1)
        self.png_dpi_label.hide()
        self.png_dpi_spinner.hide()
        GLay.set_common_column_size([adj_grid, param_grid, obj_grid, export_grid, punch_grid], 0)
        self.film_object_button = FCButton(_("Save Film"), bold=True)
        self.film_object_button.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.film_object_button.setToolTip(
            _("Create a Film for the selected object, within\n"
              "the specified box. Does not create a new \n "
              "FlatCAM object, but directly save it in the\n"
              "selected format."))
        self.tools_box.addWidget(self.film_object_button)
        self.layout.addStretch(1)
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(_("Will reset the tool parameters."))
        self.layout.addWidget(self.reset_button)

    def on_level_changed(self, checked):
        if not checked:
            self.level.setText('%s' % _('Beginner'))
            self.level.setStyleSheet("QToolButton {color: green;}")
            self.film_adj_label.hide()
            self.adj_frame.hide()
            self.film_scale_cb.set_value(False)
            self.film_skew_cb.set_value(False)
            self.film_mirror_cb.set_value(False)
            self.film_scale_stroke_entry.set_value(0.0)
        else:
            self.level.setText('%s' % _('Advanced'))
            self.level.setStyleSheet("QToolButton {color: red;}")
            self.film_adj_label.show()
            self.adj_frame.show()

    def on_film_type(self, val):
        if val == 'neg':
            self.boundary_label.show()
            self.boundary_entry.show()
            self.punch_cb.set_value(False)
            self.punch_cb.hide()
        else:
            self.boundary_label.hide()
            self.boundary_entry.hide()
            self.punch_cb.show()

    def on_file_type(self, val):
        if val == 'pdf':
            self.orientation_label.show()
            self.orientation_radio.show()
            self.pagesize_label.show()
            self.pagesize_combo.show()
            self.png_dpi_label.hide()
            self.png_dpi_spinner.hide()
        elif val == 'png':
            self.png_dpi_label.show()
            self.png_dpi_spinner.show()
            self.orientation_label.hide()
            self.orientation_radio.hide()
            self.pagesize_label.hide()
            self.pagesize_combo.hide()
        else:
            self.orientation_label.hide()
            self.orientation_radio.hide()
            self.pagesize_label.hide()
            self.pagesize_combo.hide()
            self.png_dpi_label.hide()
            self.png_dpi_spinner.hide()

    def on_punch_source(self, val):
        if val == 'pad' and self.punch_cb.get_value():
            self.punch_size_label.show()
            self.punch_size_spinner.show()
            self.exc_combo.hide()
        else:
            self.punch_size_label.hide()
            self.punch_size_spinner.hide()
            self.exc_combo.show()
        if val == 'pad' and self.tf_type_obj_combo.get_value() == 'geo':
            self.source_punch.set_value('exc')
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Using the Pad center does not work on Geometry objects. "
                                                          "Only a Gerber object has pads."))

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals, minval,
                                                                                  self.decimals, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
