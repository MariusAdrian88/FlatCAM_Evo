"""Photo engraving controls; combo values are stable, untranslated identifiers."""

from PyQt6 import QtCore, QtWidgets
from appGUI.GUIElements import FCButton, FCCheckBox, FCComboBox, FCDoubleSpinner, FCLabel, FCSpinner, GLay
from .PhotoCarveModel import DEFAULTS

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class IdComboBox(FCComboBox):
    def add_choice(self, text, identifier):
        self.addItem(text, identifier)

    def get_value(self):
        return self.currentData()

    def set_value(self, value):
        index = self.findData(value)
        self.setCurrentIndex(index if index >= 0 else 0)


CHOICES = {
    'mode': ((_("V-carve"), 'vcarve'), (_("Halftone"), 'halftone'), (_("Relief"), 'relief'),
             (_("Lithophane"), 'lithophane'), (_("Laser"), 'laser')),
    'tone': ((_("Linear"), 'linear'), (_("Perceptual"), 'perceptual'), (_("V-bit area"), 'vbit_area'),
             (_("Lithophane"), 'lithophane')),
    'strategy': ((_("Raster"), 'raster'), (_("Spiral"), 'spiral'), ('-', '-'), ('X', 'x'), ('Y', 'y'),
                 ('XY', 'xy'), (_("Grayscale"), 'gray'), (_("Bayer"), 'bayer'), (_("Floyd–Steinberg"), 'floyd')),
    'tool_kind': ((_("V-bit"), 'vbit'), (_("Flat"), 'flat'), (_("Ball"), 'ball')),
    'rough_kind': ((_("Flat"), 'flat'), (_("Ball"), 'ball'), (_("V-bit"), 'vbit')),
}

SECTIONS = (
    (_("Size"), ('x0', 'y0', 'width', 'res')),
    (_("Adjust"), ('autocontrast', 'equalize', 'blur', 'sharpen', 'brightness', 'contrast', 'gamma', 'invert', 'mirror')),
    (_("Process"), ('mode', 'tone', 'strategy', 'angle', 'spacing', 'min_depth', 'max_depth')),
    (_("Tool"), ('tool_kind', 'tool_dia', 'tool_angle', 'tool_tip')),
    (_("Roughing"), ('rough', 'rough_kind', 'rough_dia', 'rough_angle', 'rough_tip', 'stepdown', 'allowance')),
    (_("Lithophane"), ('litho_min', 'litho_max', 'frame')),
    (_("Optimize"), ('tolerance', 'lift_gap', 'overscan', 'levels', 'sim_res')),
)

LABELS = {
    'x0': _("Origin X"), 'y0': _("Origin Y"), 'width': _("Width"), 'res': _("Resolution"),
    'autocontrast': _("Auto contrast"), 'equalize': _("Equalize"), 'blur': _("Blur"),
    'sharpen': _("Sharpen"), 'brightness': _("Brightness"), 'contrast': _("Contrast"), 'gamma': _("Gamma"),
    'invert': _("Invert"), 'mirror': _("Mirror"), 'mode': _("Mode"), 'tone': _("Tone"),
    'strategy': _("Strategy"), 'angle': _("Scan angle"), 'spacing': _("Spacing"),
    'min_depth': _("Minimum depth"), 'max_depth': _("Maximum depth"), 'tool_kind': _("Cutter"),
    'tool_dia': _("Diameter"), 'tool_angle': _("V angle"), 'tool_tip': _("Tip diameter"),
    'rough': _("Enable roughing"), 'rough_kind': _("Rough cutter"), 'rough_dia': _("Rough diameter"),
    'rough_angle': _("Rough V angle"), 'rough_tip': _("Rough tip diameter"), 'stepdown': _("Stepdown"),
    'allowance': _("Allowance"), 'litho_min': _("Minimum thickness"), 'litho_max': _("Maximum thickness"),
    'frame': _("Frame width"), 'tolerance': _("Tolerance"), 'lift_gap': _("Lift gap"),
    'overscan': _("Overscan"), 'levels': _("Laser levels"), 'sim_res': _("Simulation resolution"),
}


def add_parameter_fields(owner, layout, decimals):
    """Use the same widgets and stable IDs in the plugin and Preferences."""
    owner.sections = {}
    for title, names in SECTIONS:
        group = QtWidgets.QGroupBox(title)
        group.setToolTip(title)
        grid = GLay(v_spacing=5, h_spacing=3)
        group.setLayout(grid)
        layout.addWidget(group)
        owner.sections[names[0]] = group
        for row, name in enumerate(names):
            default = DEFAULTS[name]
            label = FCLabel(LABELS[name])
            label.setToolTip(LABELS[name])
            if name in CHOICES:
                widget = IdComboBox()
                for text, identifier in CHOICES[name]:
                    widget.add_choice(text, identifier)
            elif isinstance(default, bool):
                widget = FCCheckBox()
            elif isinstance(default, int):
                widget = FCSpinner()
                widget.set_range(2, 1000000)
            else:
                widget = FCDoubleSpinner()
                widget.set_precision(decimals)
                widget.set_range(-1000000.0, 1000000.0)
            widget.setObjectName(name)
            widget.setToolTip(LABELS[name])
            widget.set_value(default)
            setattr(owner, name + '_entry', widget)
            setattr(owner, name + '_label', label)
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)


class PhotoCarveUI:
    pluginName = _("Photo Carve")

    def __init__(self, layout, app):
        self.title_label = FCLabel(self.pluginName, bold=True)
        self.title_label.setToolTip(_("Create engraving geometry from an image."))
        layout.addWidget(self.title_label)
        image_group = QtWidgets.QGroupBox(_("Image"))
        image_group.setToolTip(_("Load an image with EXIF orientation applied."))
        image_layout = QtWidgets.QVBoxLayout(image_group)
        self.load_button = FCButton(_("Load Image"))
        self.load_button.setToolTip(_("Load an image from a file."))
        image_layout.addWidget(self.load_button)
        self.file_label = FCLabel(_("No image loaded"))
        self.px_label = FCLabel('')
        self.height_label = FCLabel('')
        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setFixedSize(256, 256)
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        for widget in (self.file_label, self.px_label, self.height_label, self.preview_label):
            widget.setToolTip(_("Image dimensions and adjusted preview. Aspect ratio is preserved."))
            image_layout.addWidget(widget)
        layout.addWidget(image_group)
        add_parameter_fields(self, layout, app.decimals)
        self.vbit_limit_label = FCLabel('')
        self.vbit_limit_label.setToolTip(_("Maximum depth allowed by the V-bit width and spacing."))
        self.sections['mode'].layout().addWidget(self.vbit_limit_label, 7, 0, 1, 2)
        for name, text in (('preview', _("Preview")), ('generate', _("Generate Geometry")),
                           ('sim', _("Simulate")), ('stop', _("Stop")), ('milling', _("Open Milling"))):
            button = FCButton(text)
            button.setToolTip(text)
            setattr(self, name + '_button', button)
            layout.addWidget(button)
        self.generate_button.setStyleSheet('font-weight: bold;')
        self.sim_speed_entry = FCSpinner()
        self.sim_speed_entry.set_range(1, 100)
        self.sim_speed_entry.set_value(10)
        self.sim_speed_entry.setToolTip(_("Simulation speed (1–100)."))
        layout.addWidget(self.sim_speed_entry)
        layout.addStretch()
