
from PyQt6 import QtGui

from appGUI.GUIElements import FCSpinner, FCDoubleSpinner, FCCheckBox, FCComboBox, RadioSet, FCTextArea, \
    FCLabel, FCColorEntry, GLay, FCFrame
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class Tools2QRCodePrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(Tools2QRCodePrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("QRCode Plugin")))
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # Parameters Frame
        # #############################################################################################################
        self.qrlabel = FCLabel('%s' % _("Parameters"), bold=True)
        self.qrlabel.setToolTip(
            _("A tool to create a QRCode that can be inserted\n"
              "into a selected Gerber file, or it can be exported as a file.")
        )
        self.layout.addWidget(self.qrlabel)

        par_frame = FCFrame()
        self.layout.addWidget(par_frame)

        # ## Grid Layout
        param_grid = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(param_grid)

        # VERSION #
        self.version_label = FCLabel('%s:' % _("Version"))
        self.version_label.setToolTip(
            _("QRCode version can have values from 1 (21x21 boxes)\n"
              "to 40 (177x177 boxes). The version is fixed unless Auto is checked.")
        )
        self.version_entry = FCSpinner()
        self.version_entry.set_range(1, 40)
        self.version_entry.setWrapping(True)

        param_grid.addWidget(self.version_label, 0, 0)
        param_grid.addWidget(self.version_entry, 0, 1)

        self.version_auto_cb = FCCheckBox(_("Auto version"))
        self.version_auto_cb.setToolTip(
            _("When checked, the smallest version that fits the data is used.\n"
              "When not checked, the version is fixed and the data must fit in it.")
        )
        param_grid.addWidget(self.version_auto_cb, 1, 0, 1, 2)

        # ERROR CORRECTION #
        self.error_label = FCLabel('%s:' % _("Error correction"))
        self.error_label.setToolTip(
            _("Parameter that controls the error correction used for the QR Code.\n"
              "L = maximum 7%% errors can be corrected\n"
              "M = maximum 15%% errors can be corrected\n"
              "Q = maximum 25%% errors can be corrected\n"
              "H = maximum 30%% errors can be corrected.")
        )
        self.error_radio = RadioSet([{'label': 'L', 'value': 'L'},
                                     {'label': 'M', 'value': 'M'},
                                     {'label': 'Q', 'value': 'Q'},
                                     {'label': 'H', 'value': 'H'}])
        self.error_radio.setToolTip(
            _("Parameter that controls the error correction used for the QR Code.\n"
              "L = maximum 7%% errors can be corrected\n"
              "M = maximum 15%% errors can be corrected\n"
              "Q = maximum 25%% errors can be corrected\n"
              "H = maximum 30%% errors can be corrected.")
        )
        param_grid.addWidget(self.error_label, 2, 0)
        param_grid.addWidget(self.error_radio, 2, 1)

        # BOX SIZE #
        self.bsize_label = FCLabel('%s:' % _("Box Size"))
        self.bsize_label.setToolTip(
            _("Box size control the overall size of the QRcode\n"
              "by adjusting the size of each box in the code.")
        )
        self.bsize_entry = FCSpinner()
        self.bsize_entry.set_range(1, 9999)
        self.bsize_entry.setWrapping(True)

        param_grid.addWidget(self.bsize_label, 4, 0)
        param_grid.addWidget(self.bsize_entry, 4, 1)

        # BORDER SIZE #
        self.border_size_label = FCLabel('%s:' % _("Border Size"))
        self.border_size_label.setToolTip(
            _("Size of the QRCode border. How many boxes thick is the border.\n"
              "Default value is 4. The width of the clearance around the QRCode.")
        )
        self.border_size_entry = FCSpinner()
        self.border_size_entry.set_range(0, 9999)
        self.border_size_entry.setWrapping(True)

        param_grid.addWidget(self.border_size_label, 6, 0)
        param_grid.addWidget(self.border_size_entry, 6, 1)

        # Text box
        self.text_label = FCLabel('%s:' % _("QRCode Data"))
        self.text_label.setToolTip(
            _("QRCode Data. Alphanumeric text to be encoded in the QRCode.")
        )
        self.text_data = FCTextArea()
        self.text_data.setPlaceholderText(
            _("Add here the text to be included in the QRCode...")
        )
        param_grid.addWidget(self.text_label, 8, 0)
        param_grid.addWidget(self.text_data, 10, 0, 1, 2)

        # POLARITY CHOICE #
        self.pol_label = FCLabel('%s:' % _("Polarity"))
        self.pol_label.setToolTip(
            _("Choose the polarity of the QRCode.\n"
              "It can be drawn in a negative way (squares are clear)\n"
              "or in a positive way (squares are opaque).")
        )
        self.pol_radio = RadioSet([{'label': _('Negative'), 'value': 'neg'},
                                   {'label': _('Positive'), 'value': 'pos'}])
        self.pol_radio.setToolTip(
            _("Choose the type of QRCode to be created.\n"
              "If added on a Silkscreen Gerber file the QRCode may\n"
              "be added as positive. If it is added to a Copper Gerber\n"
              "file then perhaps the QRCode can be added as negative.")
        )
        param_grid.addWidget(self.pol_label, 12, 0)
        param_grid.addWidget(self.pol_radio, 12, 1)

        # BOUNDING BOX TYPE #
        self.bb_label = FCLabel('%s:' % _("Bounding Box"))
        self.bb_label.setToolTip(
            _("The bounding box, meaning the empty space that surrounds\n"
              "the QRCode geometry, can have a rounded or a square shape.")
        )
        self.bb_radio = RadioSet([{'label': _('Rounded'), 'value': 'r'},
                                  {'label': _('Square'), 'value': 's'}])
        self.bb_radio.setToolTip(
            _("The bounding box, meaning the empty space that surrounds\n"
              "the QRCode geometry, can have a rounded or a square shape.")
        )
        param_grid.addWidget(self.bb_label, 14, 0)
        param_grid.addWidget(self.bb_radio, 14, 1)

        # FILL COLOR #
        self.fill_color_label = FCLabel('%s:' % _('Fill Color'))
        self.fill_color_label.setToolTip(
            _("Set the QRCode fill color (squares color).")
        )
        self.fill_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        param_grid.addWidget(self.fill_color_label, 16, 0)
        param_grid.addWidget(self.fill_color_entry, 16, 1)

        # BACK COLOR #
        self.back_color_label = FCLabel('%s:' % _('Back Color'))
        self.back_color_label.setToolTip(
            _("Set the QRCode background color.")
        )
        self.back_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        param_grid.addWidget(self.back_color_label, 18, 0)
        param_grid.addWidget(self.back_color_entry, 18, 1)

        # Selection Limit
        self.sel_limit_label = FCLabel('%s:' % _("Selection limit"))
        self.sel_limit_label.setToolTip(
            _("Set the number of selected geometry\n"
              "items above which the utility geometry\n"
              "becomes just a selection rectangle.\n"
              "Increases the performance when moving a\n"
              "large number of geometric elements.")
        )
        self.sel_limit_entry = FCSpinner()
        self.sel_limit_entry.set_range(0, 9999)

        param_grid.addWidget(self.sel_limit_label, 20, 0)
        param_grid.addWidget(self.sel_limit_entry, 20, 1)

        # MASK PATTERN #
        self.mask_pattern_label = FCLabel('%s:' % _("Mask pattern"))
        self.mask_pattern_label.setToolTip(_("The QR mask pattern. Auto lets the encoder choose the best one."))
        self.mask_pattern_combo = FCComboBox()
        self.mask_pattern_combo.addItems(['Auto'] + [str(i) for i in range(8)])
        param_grid.addWidget(self.mask_pattern_label, 22, 0)
        param_grid.addWidget(self.mask_pattern_combo, 22, 1)

        # MIN MODULE #
        self.min_module_label = FCLabel('%s [%s]:' % (_("Min module"), _("mm")))
        self.min_module_label.setToolTip(
            _("The smallest QR module (box) size, in mm, that the manufacturing process can make.\n"
              "A smaller module raises a warning."))
        self.min_module_entry = FCDoubleSpinner()
        self.min_module_entry.set_range(0.0, 100.0)
        self.min_module_entry.set_precision(self.decimals)
        self.min_module_entry.setSingleStep(0.05)
        param_grid.addWidget(self.min_module_label, 24, 0)
        param_grid.addWidget(self.min_module_entry, 24, 1)

        # ROTATION / MIRROR #
        self.rotation_label = FCLabel('%s:' % _("Rotation"))
        self.rotation_label.setToolTip(_("Rotate the QRCode counter-clockwise, in degrees."))
        self.rotation_radio = RadioSet([{'label': '0', 'value': 0}, {'label': '90', 'value': 90},
                                        {'label': '180', 'value': 180}, {'label': '270', 'value': 270}])
        param_grid.addWidget(self.rotation_label, 26, 0)
        param_grid.addWidget(self.rotation_radio, 26, 1)

        self.mirror_cb = FCCheckBox(_("Mirror"))
        self.mirror_cb.setToolTip(_("Mirror the QRCode about the vertical axis (for the bottom layer)."))
        param_grid.addWidget(self.mirror_cb, 28, 0, 1, 2)

        # CLEARANCE MARGIN #
        self.margin_label = FCLabel('%s:' % _("Clearance"))
        self.margin_label.setToolTip(
            _("The clearance around the QRCode, in app units.\n"
              "Zero means: the Border Size in boxes."))
        self.margin_entry = FCDoubleSpinner()
        self.margin_entry.set_range(0.0, 10000.0)
        self.margin_entry.set_precision(self.decimals)
        self.margin_entry.setSingleStep(0.1)
        param_grid.addWidget(self.margin_label, 30, 0)
        param_grid.addWidget(self.margin_entry, 30, 1)

        # PLACEMENT #
        self.placement_label = FCLabel('%s:' % _("Placement"))
        self.placement_label.setToolTip(_("Place the QRCode with a click on the canvas or at given coordinates."))
        self.placement_radio = RadioSet([{'label': _('Click'), 'value': 'click'},
                                         {'label': _('Coordinates'), 'value': 'coords'}])
        param_grid.addWidget(self.placement_label, 32, 0)
        param_grid.addWidget(self.placement_radio, 32, 1)

        self.anchor_label = FCLabel('%s:' % _("Anchor"))
        self.anchor_label.setToolTip(
            _("The point of the QRCode (clearance included) that goes to the placement point."))
        self.anchor_radio = RadioSet([{'label': 'BL', 'value': 'bl'}, {'label': 'TL', 'value': 'tl'},
                                      {'label': 'BR', 'value': 'br'}, {'label': 'TR', 'value': 'tr'},
                                      {'label': 'C', 'value': 'c'}])
        param_grid.addWidget(self.anchor_label, 34, 0)
        param_grid.addWidget(self.anchor_radio, 34, 1)

        self.multi_cb = FCCheckBox(_("Keep placing"))
        self.multi_cb.setToolTip(_("Stay armed after each click and increment the serial number."))
        param_grid.addWidget(self.multi_cb, 36, 0, 1, 2)

        # SERIAL #
        self.serial_start_label = FCLabel('%s:' % _("Serial start"))
        self.serial_start_label.setToolTip(_("The first value of the {serial} variable."))
        self.serial_start_entry = FCSpinner()
        self.serial_start_entry.set_range(0, 999999999)
        param_grid.addWidget(self.serial_start_label, 38, 0)
        param_grid.addWidget(self.serial_start_entry, 38, 1)

        self.serial_step_label = FCLabel('%s:' % _("Serial step"))
        self.serial_step_label.setToolTip(_("The {serial} variable grows with this value after each insertion."))
        self.serial_step_entry = FCSpinner()
        self.serial_step_entry.set_range(1, 999999)
        param_grid.addWidget(self.serial_step_label, 40, 0)
        param_grid.addWidget(self.serial_step_entry, 40, 1)

        self.serial_pad_label = FCLabel('%s:' % _("Serial width"))
        self.serial_pad_label.setToolTip(_("The {serial} variable is padded with zeros to this width."))
        self.serial_pad_entry = FCSpinner()
        self.serial_pad_entry.set_range(0, 20)
        param_grid.addWidget(self.serial_pad_label, 42, 0)
        param_grid.addWidget(self.serial_pad_entry, 42, 1)

        # OUTPUT #
        self.output_label = FCLabel('%s:' % _("Output"))
        self.output_label.setToolTip(_("Add the QRCode to the source Gerber or create a new Gerber object."))
        self.output_radio = RadioSet([{'label': _('Add to source'), 'value': 'source'},
                                      {'label': _('New object'), 'value': 'new'}])
        param_grid.addWidget(self.output_label, 44, 0)
        param_grid.addWidget(self.output_radio, 44, 1)
        # self.layout.addStretch()

        # QRCode Tool
        self.fill_color_entry.editingFinished.connect(self.on_qrcode_fill_color_entry)
        self.back_color_entry.editingFinished.connect(self.on_qrcode_back_color_entry)

    def on_qrcode_fill_color_entry(self):
        self.app.options['tools_qrcode_fill_color'] = self.fill_color_entry.get_value()

    def on_qrcode_back_color_entry(self):
        self.app.options['tools_qrcode_back_color'] = self.back_color_entry.get_value()
