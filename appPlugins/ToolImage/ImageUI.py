# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore, QtWidgets, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCComboBox2, \
    RadioSet, FCDoubleSpinner, FCSpinner, FCCheckBox, FCSliderWithSpinner
from appCommon.imagetrace import PRESETS, PRESET_NAMES, CURVE_MODES

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ImageUI:

    pluginName = _("Image Import")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.layout.addWidget(title_label)
        load_layout = QtWidgets.QHBoxLayout()
        self.load_button = FCButton(_("Load Image"))
        self.load_button.setIcon(QtGui.QIcon(self.app.resource_location + '/image32.png'))
        self.load_button.setToolTip(_("Load and validate an image before importing it."))
        self.load_status = FCLabel('●', color='gray', size=18)
        self.load_status.setToolTip(_("No image loaded."))
        load_layout.addWidget(self.load_button, 1)
        load_layout.addWidget(self.load_status)
        self.layout.addLayout(load_layout)

        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setFixedHeight(200)
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setToolTip(_("Sampled image preview (up to 320 x 200 pixels).\n"
                                       "Settings apply to this preview; final import uses every original pixel."))
        self.layout.addWidget(self.preview_label)
        adjust_layout = QtWidgets.QHBoxLayout()
        self.invert_entry = FCCheckBox(_("Invert"))
        self.mirror_x_entry = FCCheckBox(_("Mirror horizontal (X)"))
        self.mirror_y_entry = FCCheckBox(_("Mirror vertical (Y)"))
        for widget in (self.invert_entry, self.mirror_x_entry, self.mirror_y_entry):
            adjust_layout.addWidget(widget)
        self.layout.addLayout(adjust_layout)

        self.param_lbl = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.layout.addWidget(self.param_lbl)
        par_frame = FCFrame()
        self.layout.addWidget(par_frame)
        par_grid = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(par_grid)

        self.tf_type_obj_combo_label = FCLabel('%s:' % _("Object Type"))
        self.tf_type_obj_combo_label.setToolTip(
            _("Specify the type of object to create from the image.\n"
              "It can be of type: Gerber or Geometry.")
        )
        self.tf_type_obj_combo = FCComboBox()
        self.tf_type_obj_combo.addItems([_("Gerber"), _("Geometry")])
        self.tf_type_obj_combo.setItemIcon(0, QtGui.QIcon(self.app.resource_location + "/flatcam_icon16.png"))
        self.tf_type_obj_combo.setItemIcon(1, QtGui.QIcon(self.app.resource_location + "/geometry16.png"))
        par_grid.addWidget(self.tf_type_obj_combo_label, 0, 0)
        par_grid.addWidget(self.tf_type_obj_combo, 0, 1, 1, 2)

        self.dpi_entry = FCSpinner()
        self.dpi_entry.set_range(1, 99999)
        self.dpi_label = FCLabel('%s:' % _("DPI value"))
        self.dpi_label.setToolTip(_("Specify a DPI value for the image.\n"
                                   "Loading an image uses its embedded DPI when available."))
        par_grid.addWidget(self.dpi_label, 2, 0)
        par_grid.addWidget(self.dpi_entry, 2, 1, 1, 2)

        area_lbl = FCLabel('%s' % _("Area:"), bold=True)
        area_lbl.setToolTip(_("Polygons inside the image with less area are discarded."))
        self.min_area_entry = FCDoubleSpinner()
        self.min_area_entry.set_range(0.0000, 10000.0000)
        self.min_area_entry.setSingleStep(0.1)
        self.min_area_entry.set_value(0.0)
        a_units = _("mm") if self.app.app_units == 'MM' else _("in")
        area_units_lbl = FCLabel('%s<sup>2</sup>' % a_units)
        par_grid.addWidget(area_lbl, 4, 0)
        par_grid.addWidget(self.min_area_entry, 4, 1)
        par_grid.addWidget(area_units_lbl, 4, 2)

        self.import_mode_lbl = FCLabel('%s:' % _('Mode'), color='red', bold=True)
        self.import_mode_lbl.setToolTip(
            _("Choose a method for the image interpretation.\n"
              "B/W means a black & white image. Color means a colored image.")
        )
        self.import_mode_radio = RadioSet([
            {'label': 'Raster', 'value': 'raster'},
            {'label': 'Tracing', 'value': 'trace'}
        ])
        mod_grid = GLay(v_spacing=5, h_spacing=3)
        self.layout.addLayout(mod_grid)
        mod_grid.addWidget(self.import_mode_lbl, 0, 0)
        mod_grid.addWidget(self.import_mode_radio, 0, 1)

        self.image_type_label = FCLabel('%s:' % _('Type'), bold=True)
        self.image_type_label.setToolTip(
            _("Choose a method for the image interpretation.\n"
              "B/W means a black & white image. Color means a colored image.")
        )
        self.image_type = RadioSet([{'label': 'B/W', 'value': 'black'},
                                    {'label': 'Color', 'value': 'color'}])
        mod_grid.addWidget(self.image_type_label, 6, 0)
        mod_grid.addWidget(self.image_type, 6, 1, 1, 2)

        self.raster_frame = FCFrame()
        self.layout.addWidget(self.raster_frame)
        raster_grid = GLay(v_spacing=5, h_spacing=3)
        self.raster_frame.setLayout(raster_grid)
        self.detail_label = FCLabel("%s:" % _('Level of detail'), bold=True)
        raster_grid.addWidget(self.detail_label, 0, 0, 1, 2)

        self.mask_bw_entry = FCSliderWithSpinner(0, 255, 1)
        self.mask_bw_label = FCLabel("%s <b>B/W</b>:" % _('Mask value'))
        self.mask_bw_label.setToolTip(
            _("Mask for monochrome image.\n"
              "Takes values between [0 ... 255].\n"
              "Decides the level of details to include\n"
              "in the resulting geometry.\n"
              "0 means no detail and 255 means everything \n"
              "(which is totally black).")
        )
        raster_grid.addWidget(self.mask_bw_label, 2, 0)
        raster_grid.addWidget(self.mask_bw_entry, 2, 1)

        self.mask_r_entry = FCSliderWithSpinner(0, 255, 1)
        self.mask_r_label = FCLabel("%s <b>R:</b>" % _('Mask value'))
        self.mask_r_label.setToolTip(
            _("Mask for RED color.\n"
              "Takes values between [0 ... 255].\n"
              "Decides the level of details to include\n"
              "in the resulting geometry.")
        )
        raster_grid.addWidget(self.mask_r_label, 4, 0)
        raster_grid.addWidget(self.mask_r_entry, 4, 1)

        self.mask_g_entry = FCSliderWithSpinner(0, 255, 1)
        self.mask_g_label = FCLabel("%s <b>G:</b>" % _('Mask value'))
        self.mask_g_label.setToolTip(
            _("Mask for GREEN color.\n"
              "Takes values between [0 ... 255].\n"
              "Decides the level of details to include\n"
              "in the resulting geometry.")
        )
        raster_grid.addWidget(self.mask_g_label, 6, 0)
        raster_grid.addWidget(self.mask_g_entry, 6, 1)

        self.mask_b_entry = FCSliderWithSpinner(0, 255, 1)
        self.mask_b_label = FCLabel("%s <b>B:</b>" % _('Mask value'))
        self.mask_b_label.setToolTip(
            _("Mask for BLUE color.\n"
              "Takes values between [0 ... 255].\n"
              "Decides the level of details to include\n"
              "in the resulting geometry.")
        )
        raster_grid.addWidget(self.mask_b_label, 8, 0)
        raster_grid.addWidget(self.mask_b_entry, 8, 1)

        self.trace_frame = FCFrame()
        self.layout.addWidget(self.trace_frame)
        trace_grid = GLay(v_spacing=5, h_spacing=3)
        self.trace_frame.setLayout(trace_grid)

        self.threshold_lbl = FCLabel('%s:' % _("Threshold"))
        self.threshold_lbl.setToolTip(_("B/W only.\nA pixel darker than this value [0 ... 255] is traced."))
        self.threshold_entry = FCSliderWithSpinner(0, 255, 1)
        trace_grid.addWidget(self.threshold_lbl, 0, 0)
        trace_grid.addWidget(self.threshold_entry, 0, 1)

        self.control_lbl = FCLabel('%s:' % _('Control'), color='indigo', bold=True)
        self.control_lbl.setToolTip(_("Tracing control."))
        self.control_radio = RadioSet([
            {'label': _("Presets"), 'value': 'presets'},
            {'label': _("Options"), 'value': 'options'}
        ])
        trace_grid.addWidget(self.control_lbl, 1, 0)
        trace_grid.addWidget(self.control_radio, 1, 1)

        self.preset_frame = QtWidgets.QFrame()
        self.preset_frame.setContentsMargins(0, 0, 0, 0)
        trace_grid.addWidget(self.preset_frame, 2, 0, 1, 2)
        preset_grid = GLay(v_spacing=5, h_spacing=3)
        preset_grid.setContentsMargins(0, 0, 0, 0)
        self.preset_frame.setLayout(preset_grid)

        self.presets_lbl = FCLabel('%s:' % _('Presets'))
        self.presets_lbl.setToolTip(
            _("Options presets to control the tracing.\n"
              "- Default: straight segments, good for PCB artwork\n"
              "- Detailed: keeps small features and more colors\n"
              "- Smooth: curves, for logos and drawings\n"
              "- Photo: removes noise, for photographs")
        )
        self.presets_combo = FCComboBox2()
        self.presets_combo.addItems([_("Default"), _("Detailed"), _("Smooth"), _("Photo")])
        preset_grid.addWidget(self.presets_lbl, 0, 0)
        preset_grid.addWidget(self.presets_combo, 0, 1)

        self.options_frame = QtWidgets.QFrame()
        self.options_frame.setContentsMargins(0, 0, 0, 0)
        trace_grid.addWidget(self.options_frame, 4, 0, 1, 2)
        options_grid = GLay(v_spacing=5, h_spacing=3)
        options_grid.setContentsMargins(0, 0, 0, 0)
        self.options_frame.setLayout(options_grid)

        self.curve_lbl = FCLabel('%s:' % _("Curve"))
        self.curve_lbl.setToolTip(
            _("How the outlines are made.\n"
              "- Polygon: straight segments\n"
              "- Spline: smooth curves\n"
              "- Pixel: the pixel edges, without fitting")
        )
        self.curve_combo = FCComboBox2()
        self.curve_combo.addItems([_("Polygon"), _("Spline"), _("Pixel")])
        options_grid.addWidget(self.curve_lbl, 0, 0)
        options_grid.addWidget(self.curve_combo, 0, 1)

        self.speckle_lbl = FCLabel('%s:' % _("Filter Speckle"))
        self.speckle_lbl.setToolTip(_("Patches smaller than this size (in pixels) are discarded."))
        self.speckle_entry = FCSpinner()
        self.speckle_entry.set_range(0, 128)
        self.speckle_entry.setSingleStep(1)
        options_grid.addWidget(self.speckle_lbl, 2, 0)
        options_grid.addWidget(self.speckle_entry, 2, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        options_grid.addWidget(separator_line, 4, 0, 1, 2)

        self.colors_lbl = FCLabel('%s' % _("Colors"), bold=True)
        self.colors_lbl.setToolTip(_("Color only."))
        options_grid.addWidget(self.colors_lbl, 6, 0, 1, 2)
        self.color_precision_lbl = FCLabel('%s:' % _("Precision"))
        self.color_precision_lbl.setToolTip(
            _("Number of significant bits of each RGB channel [1 ... 8].\n"
              "Lower values merge more colors.")
        )
        self.color_precision_entry = FCSpinner()
        self.color_precision_entry.set_range(1, 8)
        self.color_precision_entry.setSingleStep(1)
        options_grid.addWidget(self.color_precision_lbl, 8, 0)
        options_grid.addWidget(self.color_precision_entry, 8, 1)

        self.layer_diff_lbl = FCLabel('%s:' % _("Gradient Step"))
        self.layer_diff_lbl.setToolTip(
            _("Color difference between the layers of a gradient [0 ... 255].\n"
              "Higher values make fewer layers.")
        )
        self.layer_diff_entry = FCSpinner()
        self.layer_diff_entry.set_range(0, 255)
        self.layer_diff_entry.setSingleStep(1)
        options_grid.addWidget(self.layer_diff_lbl, 10, 0)
        options_grid.addWidget(self.layer_diff_entry, 10, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        options_grid.addWidget(separator_line, 12, 0, 1, 2)

        self.spline_lbl = FCLabel('%s' % _("Spline"), bold=True)
        self.spline_lbl.setToolTip(_("Spline curve only."))
        options_grid.addWidget(self.spline_lbl, 14, 0, 1, 2)
        self.corner_lbl = FCLabel('%s:' % _("Corner"))
        self.corner_lbl.setToolTip(_("Minimum angle (in degrees) to be considered a corner."))
        self.corner_entry = FCSpinner()
        self.corner_entry.set_range(0, 180)
        self.corner_entry.setSingleStep(1)
        options_grid.addWidget(self.corner_lbl, 16, 0)
        options_grid.addWidget(self.corner_entry, 16, 1)

        self.length_lbl = FCLabel('%s:' % _("Segment Length"))
        self.length_lbl.setToolTip(
            _("Curves are subdivided into segments of this length [3.5 ... 10].\n"
              "Lower values make more segments.")
        )
        self.length_entry = FCDoubleSpinner()
        self.length_entry.set_precision(1)
        self.length_entry.set_range(3.5, 10.0)
        self.length_entry.setSingleStep(0.5)
        options_grid.addWidget(self.length_lbl, 18, 0)
        options_grid.addWidget(self.length_entry, 18, 1)

        self.splice_lbl = FCLabel('%s:' % _("Splice"))
        self.splice_lbl.setToolTip(_("Minimum angle displacement (in degrees) to splice a spline."))
        self.splice_entry = FCSpinner()
        self.splice_entry.set_range(0, 180)
        self.splice_entry.setSingleStep(1)
        options_grid.addWidget(self.splice_lbl, 20, 0)
        options_grid.addWidget(self.splice_entry, 20, 1)

        GLay.set_common_column_size([par_grid, mod_grid, raster_grid, trace_grid, preset_grid, options_grid], 0)

        self.import_button = FCButton(_("Import Image"))
        self.import_button.setIcon(QtGui.QIcon(self.app.resource_location + '/image32.png'))
        self.import_button.setToolTip(_("Import the loaded image as a Gerber or Geometry object."))
        self.import_button.setEnabled(False)
        self.layout.addWidget(self.import_button)
        self.layout.addStretch(1)

        self.import_mode_radio.activated_custom.connect(self.on_import_image_mode)
        self.control_radio.activated_custom.connect(self.on_tracing_control_radio)
        self.presets_combo.currentIndexChanged.connect(self.on_preset_changed)
        self.curve_combo.currentIndexChanged.connect(self.on_curve_mode)

    def on_image_type(self, val):
        is_color = val == 'color'
        self.threshold_lbl.setDisabled(is_color)
        self.threshold_entry.setDisabled(is_color)
        for wdg in (self.colors_lbl, self.color_precision_lbl, self.color_precision_entry, self.layer_diff_lbl,
                    self.layer_diff_entry):
            wdg.setDisabled(not is_color)

        if val == 'color':
            self.mask_r_label.setDisabled(False)
            self.mask_r_entry.setDisabled(False)
            self.mask_g_label.setDisabled(False)
            self.mask_g_entry.setDisabled(False)
            self.mask_b_label.setDisabled(False)
            self.mask_b_entry.setDisabled(False)
            self.mask_bw_label.setDisabled(True)
            self.mask_bw_entry.setDisabled(True)
        else:
            self.mask_r_label.setDisabled(True)
            self.mask_r_entry.setDisabled(True)
            self.mask_g_label.setDisabled(True)
            self.mask_g_entry.setDisabled(True)
            self.mask_b_label.setDisabled(True)
            self.mask_b_entry.setDisabled(True)
            self.mask_bw_label.setDisabled(False)
            self.mask_bw_entry.setDisabled(False)

    def on_import_image_mode(self, val):
        if val == 'raster':
            self.raster_frame.show()
            self.trace_frame.hide()
        else:
            self.raster_frame.hide()
            self.trace_frame.show()

    def on_tracing_control_radio(self, val):
        if val == 'presets':
            self.preset_frame.show()
            self.options_frame.hide()
        else:
            self.preset_frame.hide()
            self.options_frame.show()

    def on_preset_changed(self, idx):
        opt = PRESETS[PRESET_NAMES[idx]]
        self.curve_combo.set_value(CURVE_MODES.index(opt['mode']))
        self.speckle_entry.set_value(opt['filter_speckle'])
        self.color_precision_entry.set_value(opt['color_precision'])
        self.layer_diff_entry.set_value(opt['layer_difference'])
        self.corner_entry.set_value(opt['corner_threshold'])
        self.length_entry.set_value(opt['length_threshold'])
        self.splice_entry.set_value(opt['splice_threshold'])
        self.on_curve_mode(self.curve_combo.get_value())

    def on_curve_mode(self, idx):
        is_spline = CURVE_MODES[idx] == 'spline'
        for wdg in (self.spline_lbl, self.corner_lbl, self.corner_entry, self.length_lbl, self.length_entry,
                    self.splice_lbl, self.splice_entry):
            wdg.setDisabled(not is_spline)
