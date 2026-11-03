"""Photo engraving controls; combo values are stable, untranslated identifiers."""

from PyQt6 import QtCore, QtGui, QtWidgets
from appGUI.GUIElements import (FCButton, FCCheckBox, FCComboBox, FCDoubleSpinner, FCFrame, FCLabel,
                                FCSliderWithSpinner, FCSpinner, GLay)
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
    'sim_material': ((_("Wood"), 'wood'), (_("Stone"), 'stone'), (_("Marble"), 'marble'),
                     (_("Metal"), 'metal'), (_("Aluminium"), 'aluminium')),
}

# (section key, title, title color, field names). The key is the first field name, as the controller expects.
SECTIONS = (
    ('x0', _("Size"), 'blue', ('x0', 'y0', 'width', 'res')),
    ('mode', _("Process"), 'green', ('mode', 'tone', 'strategy', 'angle', 'spacing', 'laser_dia', 'min_depth', 'max_depth')),
    ('tool_kind', _("Tool"), 'brown', ('tool_kind', 'tool_dia', 'tool_angle', 'tool_tip')),
    ('rough', _("Roughing"), 'darkred',
     ('rough', 'rough_kind', 'rough_dia', 'rough_angle', 'rough_tip', 'stepdown', 'allowance')),
    ('litho_min', _("Lithophane"), 'teal', ('litho_min', 'litho_max', 'frame')),
    ('brightness', _("Image Adjust"), 'indigo',
     ('brightness', 'contrast', 'gamma', 'invert', 'autocontrast', 'equalize', 'blur', 'sharpen')),
    ('tolerance', _("Optimize"), 'purple', ('tolerance', 'lift_gap', 'overscan', 'levels')),
    ('sim_material', _("3D Simulation"), 'red', ('sim_material', 'sim_thickness', 'sim_res')),
)

LABELS = {
    'x0': _("Origin X"), 'y0': _("Origin Y"), 'width': _("Width"), 'res': _("Resolution"),
    'autocontrast': _("Auto contrast"), 'equalize': _("Equalize"), 'blur': _("Blur"),
    'sharpen': _("Sharpen"), 'brightness': _("Brightness"), 'contrast': _("Contrast"), 'gamma': _("Gamma"),
    'invert': _("Invert"), 'mode': _("Mode"), 'tone': _("Tone"),
    'strategy': _("Strategy"), 'angle': _("Scan angle"), 'spacing': _("Spacing"), 'laser_dia': _("Dot size"),
    'min_depth': _("Minimum depth"), 'max_depth': _("Maximum depth"), 'tool_kind': _("Cutter"),
    'tool_dia': _("Diameter"), 'tool_angle': _("V angle"), 'tool_tip': _("Tip diameter"),
    'rough': _("Roughing"), 'rough_kind': _("Cutter"), 'rough_dia': _("Diameter"),
    'rough_angle': _("V angle"), 'rough_tip': _("Tip diameter"), 'stepdown': _("Stepdown"),
    'allowance': _("Allowance"), 'litho_min': _("Minimum thickness"), 'litho_max': _("Maximum thickness"),
    'frame': _("Frame width"), 'tolerance': _("Tolerance"), 'lift_gap': _("Lift gap"),
    'overscan': _("Overscan"), 'levels': _("Power levels"), 'sim_res': _("Cell size"),
    'sim_material': _("Material"), 'sim_thickness': _("Thickness"),
}

TOOLTIPS = {
    'x0': _("X of the lower-left corner of the engraving."),
    'y0': _("Y of the lower-left corner of the engraving."),
    'width': _("Width of the engraving. The height follows the aspect ratio of the image."),
    'res': _("Size of one image cell on the material.\n"
             "A smaller value keeps more detail, but the job is larger and slower."),
    'mode': _("V-carve: lines with a V-bit, the line width follows the darkness.\n"
              "Halftone: V-bit dots on a hexagonal grid.\n"
              "Relief: 3D surface with a ball or flat end mill.\n"
              "Lithophane: back-lit panel, thin where the image is light.\n"
              "Laser: grayscale or dithered laser power."),
    'tone': _("How the gray value becomes a depth.\n"
              "Linear: depth follows the darkness.\n"
              "Perceptual: depth follows the darkness as the eye sees it.\n"
              "V-bit area: the groove width follows the darkness (best for V-carve)."),
    'strategy': _("V-carve: raster lines or a spiral.\n"
                  "Relief: passes along X, along Y, or both.\n"
                  "Laser: grayscale power, or a black and white dither (Bayer, Floyd–Steinberg).\n"
                  "Grayscale uses the power range from the Min Power to the Spindle speed of the Milling plugin.\n"
                  "A dither has only 2 powers: off (S0) or the Spindle speed. The Min Power has no effect;\n"
                  "change the Spindle speed to change the power of the dots."),
    'angle': _("Angle of the raster lines, in degrees."),
    'spacing': _("Distance between two lines, dots or passes."),
    'laser_dia': _("Laser only: diameter of the laser dot on the material.\n"
                   "It is the tool diameter of the geometry in the Milling plugin."),
    'min_depth': _("Depth of the white areas."),
    'max_depth': _("Depth of the black areas.\n"
                   "For a V-bit it is limited so that the groove is not wider than the spacing."),
    'tool_kind': _("Shape of the finishing cutter."),
    'tool_dia': _("Diameter of the finishing cutter."),
    'tool_angle': _("Included angle of the V-bit, in degrees."),
    'tool_tip': _("Diameter of the flat tip of the V-bit. Use 0 for a sharp tip."),
    'rough': _("Remove most of the material with a larger cutter before the finishing pass."),
    'rough_kind': _("Shape of the roughing cutter."),
    'rough_dia': _("Diameter of the roughing cutter."),
    'rough_angle': _("Included angle of the roughing V-bit, in degrees."),
    'rough_tip': _("Diameter of the flat tip of the roughing V-bit."),
    'stepdown': _("Depth of each roughing level."),
    'allowance': _("Material that the roughing pass leaves for the finishing pass."),
    'litho_min': _("Thickness of the lightest areas."),
    'litho_max': _("Thickness of the darkest areas. This is also the stock thickness that is used."),
    'frame': _("Width of the border that stays at full thickness."),
    'brightness': _("Add to the brightness of the image (-1 .. 1)."),
    'contrast': _("Change the contrast of the image (-1 .. 1)."),
    'gamma': _("Gamma correction. A value above 1 makes the mid tones darker."),
    'invert': _("Invert the image: black becomes white."),
    'autocontrast': _("Stretch the gray values to the full range."),
    'equalize': _("Equalize the histogram of the image."),
    'blur': _("Blur radius on the material. It removes noise and fine detail."),
    'sharpen': _("Sharpen radius on the material."),
    'tolerance': _("Largest allowed path error when points are removed.\n"
                   "A larger value makes a shorter G-code."),
    'lift_gap': _("A white gap longer than this is crossed with a lift and a rapid move.\n"
                  "Shorter gaps are crossed at the surface."),
    'overscan': _("Laser only: run-up and run-out length at each end of a line, with the laser off."),
    'levels': _("Laser only: number of power steps in grayscale mode."),
    'sim_res': _("Cell size of the 3D simulation. A smaller value is more exact and slower."),
    'sim_material': _("Material of the simulated stock. It sets the colors of the stock and of the cuts."),
    'sim_thickness': _("Thickness of the simulated stock. A deeper cut goes through the stock."),
}

# (minimum, maximum, step) of each numeric field
RANGES = {
    'x0': (-10000.0, 10000.0, 1.0), 'y0': (-10000.0, 10000.0, 1.0), 'width': (0.01, 10000.0, 1.0),
    'res': (0.001, 100.0, 0.01), 'angle': (-360.0, 360.0, 1.0), 'spacing': (0.001, 1000.0, 0.1),
    'laser_dia': (0.001, 100.0, 0.01),
    'min_depth': (0.0, 1000.0, 0.1), 'max_depth': (0.0, 1000.0, 0.1),
    'tool_dia': (0.001, 1000.0, 0.1), 'tool_angle': (1.0, 179.0, 1.0), 'tool_tip': (0.0, 1000.0, 0.01),
    'rough_dia': (0.001, 1000.0, 0.1), 'rough_angle': (1.0, 179.0, 1.0), 'rough_tip': (0.0, 1000.0, 0.01),
    'stepdown': (0.001, 1000.0, 0.1), 'allowance': (0.0, 1000.0, 0.05),
    'litho_min': (0.001, 1000.0, 0.1), 'litho_max': (0.001, 1000.0, 0.1), 'frame': (0.0, 1000.0, 0.5),
    'brightness': (-1.0, 1.0, 0.05), 'contrast': (-1.0, 1.0, 0.05), 'gamma': (0.01, 10.0, 0.05),
    'blur': (0.0, 100.0, 0.1), 'sharpen': (0.0, 100.0, 0.1),
    'tolerance': (0.0, 10.0, 0.005), 'lift_gap': (0.0, 1000.0, 0.5), 'overscan': (0.0, 1000.0, 0.5),
    'levels': (2, 65536, 1), 'sim_res': (0.001, 100.0, 0.05), 'sim_thickness': (0.01, 10000.0, 1.0),
}

# Fields that the Beginner level hides
ADVANCED = {'x0', 'y0', 'rough_angle', 'rough_tip', 'allowance', 'autocontrast', 'equalize', 'blur',
            'sharpen', 'tolerance', 'lift_gap', 'overscan', 'levels', 'sim_res'}


def add_parameter_fields(owner, layout, decimals):
    """Use the same widgets and stable IDs in the plugin and Preferences.

    owner.sections[key] is the widget of a section (title + frame), owner.grids[key] its grid layout and
    owner.rows[name] the (label, widget) pair of a field.
    """
    owner.sections, owner.grids, owner.rows = {}, {}, {}
    for key, title, color, names in SECTIONS:
        section = QtWidgets.QWidget()
        section_layout = QtWidgets.QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        title_label = FCLabel(title, color=color, bold=True)
        section_layout.addWidget(title_label)
        frame = FCFrame()
        section_layout.addWidget(frame)
        grid = GLay(v_spacing=5, h_spacing=3)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        frame.setLayout(grid)
        layout.addWidget(section)
        owner.sections[key] = section
        owner.grids[key] = grid
        for row, name in enumerate(names):
            default = DEFAULTS[name]
            tooltip = TOOLTIPS.get(name, LABELS[name])
            label = FCLabel('%s:' % LABELS[name])
            label.setToolTip(tooltip)
            label.setMinimumWidth(110)
            if name in CHOICES:
                widget = IdComboBox()
                for text, identifier in CHOICES[name]:
                    widget.add_choice(text, identifier)
            elif isinstance(default, bool):
                widget = FCCheckBox()
            elif isinstance(default, int):
                low, high, step = RANGES.get(name, (2, 1000000, 1))
                widget = FCSpinner()
                widget.set_range(low, high)
                widget.set_step(step)
            else:
                low, high, step = RANGES.get(name, (-1000000.0, 1000000.0, 0.1))
                widget = FCDoubleSpinner()
                widget.set_precision(decimals)
                widget.set_range(low, high)
                widget.set_step(step)
            widget.setObjectName(name)
            widget.setToolTip(tooltip)
            widget.set_value(default)
            setattr(owner, name + '_entry', widget)
            setattr(owner, name + '_label', label)
            owner.rows[name] = (label, widget)
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)


def _icon(app, name):
    location = getattr(app, 'resource_location', None)
    return QtGui.QIcon(location + '/' + name) if location else QtGui.QIcon()


class PhotoCarveUI:
    pluginName = _("Photo Carve")

    def __init__(self, layout, app):
        self.app = app
        self.layout = layout

        # Title and level
        title_box = QtWidgets.QHBoxLayout()
        layout.addLayout(title_box)
        self.title_label = FCLabel(self.pluginName, size=16, bold=True)
        self.title_label.setToolTip(_("Make engraving geometry from a photo: V-carve, halftone, relief,\n"
                                      "lithophane or laser. The CNCJob is made in the Milling plugin."))
        title_box.addWidget(self.title_label)
        self.level = QtWidgets.QToolButton()
        self.level.setCheckable(True)
        self.level.setToolTip(_("Beginner Mode - many parameters are hidden.\n"
                                "Advanced Mode - full control.\n"
                                "Permanent change is done in 'Preferences' menu."))
        title_box.addWidget(self.level)

        # Image
        image_title = FCLabel(_("Image"), color='darkorange', bold=True)
        image_title.setToolTip(_("The image to engrave. The EXIF orientation is applied."))
        layout.addWidget(image_title)
        image_frame = FCFrame()
        layout.addWidget(image_frame)
        image_grid = GLay(v_spacing=5, h_spacing=3)
        image_frame.setLayout(image_grid)

        self.load_button = FCButton(_("Load Image"))
        self.load_button.setIcon(_icon(app, 'image32.png'))
        self.load_button.setToolTip(_("Load an image from a file (PNG, JPG, BMP, TIF, ...)."))
        image_grid.addWidget(self.load_button, 0, 0, 1, 2)

        self.file_label = FCLabel(_("No image loaded"))
        self.file_label.setWordWrap(True)
        image_grid.addWidget(self.file_label, 1, 0, 1, 2)
        self.px_label = FCLabel('')
        self.px_label.setToolTip(_("Size of the image in pixels."))
        image_grid.addWidget(self.px_label, 2, 0)
        self.height_label = FCLabel('')
        self.height_label.setToolTip(_("Height of the engraving, from the width and the aspect ratio."))
        image_grid.addWidget(self.height_label, 2, 1)

        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setFixedSize(240, 240)
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setToolTip(_("Preview of the adjusted image, as the job uses it."))
        self.preview_label.setVisible(False)
        image_grid.addWidget(self.preview_label, 3, 0, 1, 2, QtCore.Qt.AlignmentFlag.AlignHCenter)

        # Parameters
        add_parameter_fields(self, layout, app.decimals)
        self.vbit_limit_label = FCLabel('')
        self.vbit_limit_label.setToolTip(_("Maximum depth allowed by the V-bit width and the spacing."))
        self.grids['mode'].addWidget(self.vbit_limit_label, len(SECTIONS[1][3]), 0, 1, 2)

        # Image Adjust: the Mirror buttons share the row of Invert. They change the loaded image in place.
        adjust_grid = self.grids['brightness']
        invert_row = SECTIONS[5][3].index('invert')
        adjust_grid.removeWidget(self.invert_entry)
        invert_box = QtWidgets.QWidget()
        invert_hlay = QtWidgets.QHBoxLayout(invert_box)
        invert_hlay.setContentsMargins(0, 0, 0, 0)
        invert_hlay.addWidget(self.invert_entry)
        invert_hlay.addStretch()
        mirror_tip = _("Mirror the loaded image in place on the selected axis.")
        mirror_label = FCLabel('%s:' % _("Mirror"))
        mirror_label.setToolTip(mirror_tip)
        invert_hlay.addWidget(mirror_label)
        for axis, tip in (('X', _("Mirror the loaded image on the X axis (left to right).")),
                          ('Y', _("Mirror the loaded image on the Y axis (top to bottom)."))):
            button = QtWidgets.QToolButton()
            button.setText(axis)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setFixedWidth(24)
            button.setToolTip(tip)
            invert_hlay.addWidget(button)
            setattr(self, 'mirror_%s_button' % axis.lower(), button)
        adjust_grid.addWidget(invert_box, invert_row, 1)

        # Generate
        gen_box = QtWidgets.QHBoxLayout()
        layout.addLayout(gen_box)
        self.preview_button = FCButton(_("Preview"))
        self.preview_button.setIcon(_icon(app, 'preview32.png'))
        self.preview_button.setToolTip(_("Show what the job makes (depth, dots or laser power)\n"
                                         "and the job area on the canvas."))
        gen_box.addWidget(self.preview_button)
        self.generate_button = FCButton(_("Generate Geometry"), bold=True)
        self.generate_button.setIcon(_icon(app, 'geometry32.png'))
        self.generate_button.setToolTip(_("Make the Photo Carve Geometry object."))
        gen_box.addWidget(self.generate_button, stretch=1)

        self.milling_button = FCButton(_("Open in Milling"))
        self.milling_button.setIcon(_icon(app, 'milling_tool32.png'))
        self.milling_button.setToolTip(_("Select the generated Geometry and open the Milling plugin\n"
                                         "to make the CNCJob."))
        layout.addWidget(self.milling_button)

        # Simulation: its parameter section (material, thickness, cell size) goes last, with the controls in it
        sim_section = self.sections['sim_material']
        layout.removeWidget(sim_section)
        layout.addWidget(sim_section)
        sim_section.setToolTip(_("Simulate the material removal of the generated Geometry,\n"
                                 "or of the selected CNCJob, in the 3D view."))
        sim_grid = self.grids['sim_material']
        row = sim_grid.rowCount()
        speed_label = FCLabel('%s:' % _("Speed"))
        speed_label.setToolTip(_("Simulation speed. At 100 the result is shown at once, without animation."))
        sim_grid.addWidget(speed_label, row, 0)
        self.sim_speed_entry = FCSliderWithSpinner(1, 100, 1)
        self.sim_speed_entry.set_value(10)
        self.sim_speed_entry.setToolTip(speed_label.toolTip())
        sim_grid.addWidget(self.sim_speed_entry, row, 1)
        self.sim_button = FCButton(_("Simulate"), bold=True)
        self.sim_button.setIcon(_icon(app, 'cnc32.png'))
        self.sim_button.setToolTip(_("Start the 3D simulation."))
        sim_grid.addWidget(self.sim_button, row + 1, 0)
        self.stop_button = FCButton(_("Stop"))
        self.stop_button.setToolTip(_("Stop the simulation and keep the current stock."))
        sim_grid.addWidget(self.stop_button, row + 1, 1)
        self.sim_progress = QtWidgets.QProgressBar()
        self.sim_progress.setRange(0, 100)
        self.sim_progress.setValue(0)
        self.sim_progress.setFormat('%p %')
        self.sim_progress.setToolTip(_("The part of the job that the simulation has cut."))
        sim_grid.addWidget(self.sim_progress, row + 2, 0, 1, 2)

        layout.addStretch()

    def set_row_visible(self, name, visible):
        for widget in self.rows[name]:
            widget.setVisible(visible)

    def apply_visibility(self, mode, strategy, tool_kind, rough_on, rough_kind, advanced):
        """Show only the fields that the selected mode uses; Beginner also hides the advanced ones."""
        milling = mode != 'laser'
        relief = mode in ('relief', 'lithophane')
        visible = {
            'tone': mode not in ('laser', 'halftone'),
            'angle': mode == 'vcarve' and strategy == 'raster',
            'min_depth': mode in ('vcarve', 'relief'),
            'max_depth': mode in ('vcarve', 'halftone', 'relief'),
            'tool_angle': tool_kind == 'vbit', 'tool_tip': tool_kind == 'vbit',
            'rough_kind': rough_on, 'rough_dia': rough_on, 'stepdown': rough_on, 'allowance': rough_on,
            'rough_angle': rough_on and rough_kind == 'vbit', 'rough_tip': rough_on and rough_kind == 'vbit',
            'tolerance': milling, 'lift_gap': mode in ('vcarve', 'laser'),
            'overscan': mode == 'laser', 'laser_dia': mode == 'laser', 'levels': mode == 'laser' and strategy == 'gray',
        }
        for name in self.rows:
            self.set_row_visible(name, visible.get(name, True) and (advanced or name not in ADVANCED))
        self.sections['tool_kind'].setVisible(milling)
        self.sections['rough'].setVisible(relief)
        self.sections['litho_min'].setVisible(mode == 'lithophane')
        self.sections['tolerance'].setVisible(advanced)
        self.vbit_limit_label.setVisible(mode in ('vcarve', 'halftone'))
        self.spacing_label.setText('%s:' % {'laser': _("Line spacing"), 'halftone': _("Dot pitch"),
                                            'relief': _("Stepover"), 'lithophane': _("Stepover")
                                            }.get(mode, _("Line spacing")))
