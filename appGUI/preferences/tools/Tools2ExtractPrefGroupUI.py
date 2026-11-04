
from PyQt6 import QtWidgets

from appGUI.GUIElements import FCCheckBox, RadioSet, FCDoubleSpinner, FCLabel, GLay, FCFrame
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class Tools2EDrillsPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(Tools2EDrillsPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Extract Drills Options")))
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # PARAMETERS Frame
        # #############################################################################################################
        self.padt_label = FCLabel('%s' % _("Processed Pads Type"), bold=True)
        self.padt_label.setToolTip(
            _("The type of pads shape to be processed.\n"
              "If the PCB has many SMD pads with rectangular pads,\n"
              "disable the Rectangular aperture.")
        )

        self.layout.addWidget(self.padt_label)

        param_frame = FCFrame()
        self.layout.addWidget(param_frame)

        param_grid = GLay(v_spacing=5, h_spacing=3)
        param_frame.setLayout(param_grid)

        # Circular Aperture Selection
        self.circular_cb = FCCheckBox('%s' % _("Circular"))
        self.circular_cb.setToolTip(
            _("Process Circular Pads.")
        )

        param_grid.addWidget(self.circular_cb, 3, 0, 1, 2)

        # Oblong Aperture Selection
        self.oblong_cb = FCCheckBox('%s' % _("Oblong"))
        self.oblong_cb.setToolTip(
            _("Process Oblong Pads.")
        )

        param_grid.addWidget(self.oblong_cb, 4, 0, 1, 2)

        # Square Aperture Selection
        self.square_cb = FCCheckBox('%s' % _("Square"))
        self.square_cb.setToolTip(
            _("Process Square Pads.")
        )

        param_grid.addWidget(self.square_cb, 5, 0, 1, 2)

        # Rectangular Aperture Selection
        self.rectangular_cb = FCCheckBox('%s' % _("Rectangular"))
        self.rectangular_cb.setToolTip(
            _("Process Rectangular Pads.")
        )

        param_grid.addWidget(self.rectangular_cb, 6, 0, 1, 2)

        # Others type of Apertures Selection
        self.other_cb = FCCheckBox('%s' % _("Others"))
        self.other_cb.setToolTip(
            _("Process pads not in the categories above.")
        )

        param_grid.addWidget(self.other_cb, 7, 0, 1, 2)

        # All selected Gerbers
        self.all_selected_cb = FCCheckBox('%s' % _("All selected Gerbers"))
        self.all_selected_cb.setToolTip(
            _("Also process the other Gerber objects selected in the project tree.\n"
              "Their apertures are all used (the pad type filters still apply).")
        )

        param_grid.addWidget(self.all_selected_cb, 8, 0, 1, 2)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # param_grid.addWidget(separator_line, 8, 0, 1, 2)

        # #############################################################################################################
        # Method Frame
        # #############################################################################################################
        met_frame = FCFrame()
        self.layout.addWidget(met_frame)

        met_grid = GLay(v_spacing=5, h_spacing=3)
        met_frame.setLayout(met_grid)

        self.method_radio = RadioSet(
            [
                {'label': _("Fixed Diameter"), 'value': 'fixed'},
                {'label': _("Fixed Annular Ring"), 'value': 'ring'},
                {'label': _("Proportional"), 'value': 'prop'}
            ],
            orientation='vertical',
            compact=True)
        self.method_label = FCLabel('%s' % _("Method"), bold=True)
        self.method_label.setToolTip(
            _("The method for processing pads. Can be:\n"
              "- Fixed Diameter -> all holes will have a set size\n"
              "- Fixed Annular Ring -> all holes will have a set annular ring\n"
              "- Proportional -> each hole size will be a fraction of the pad size"))

        met_grid.addWidget(self.method_label, 0, 0)
        met_grid.addWidget(self.method_radio, 0, 1)

        # Slots for oblong pads
        self.oblong_slots_cb = FCCheckBox('%s' % _("Slots for oblong pads"))
        self.oblong_slots_cb.setToolTip(
            _("An oblong pad gives a slot instead of a round hole.")
        )
        met_grid.addWidget(self.oblong_slots_cb, 2, 0, 1, 2)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # param_grid.addWidget(separator_line, 10, 0, 1, 2)

        # #############################################################################################################
        # Fixed Diameter Frame
        # #############################################################################################################
        self.fixed_label = FCLabel('%s' % _("Fixed Diameter"), bold=True)
        self.layout.addWidget(self.fixed_label)

        fix_frame = FCFrame()
        self.layout.addWidget(fix_frame)

        fix_grid = GLay(v_spacing=5, h_spacing=3)
        fix_frame.setLayout(fix_grid)

        # Diameter value
        self.dia_entry = FCDoubleSpinner()
        self.dia_entry.set_precision(self.decimals)
        self.dia_entry.set_range(0.0001, 10000.0000)

        self.dia_label = FCLabel('%s:' % _("Value"))
        self.dia_label.setToolTip(
            _("Fixed hole diameter.")
        )

        fix_grid.addWidget(self.dia_label, 0, 0)
        fix_grid.addWidget(self.dia_entry, 0, 1)

        # #############################################################################################################
        # Annular ring Frame
        # #############################################################################################################
        self.ring_label = FCLabel('%s' % _("Fixed Annular Ring"), bold=True)
        self.ring_label.setToolTip(
            _("The size of annular ring.\n"
              "The copper sliver between the hole exterior\n"
              "and the margin of the copper pad.")
        )
        self.layout.addWidget(self.ring_label)

        ring_frame = FCFrame()
        self.layout.addWidget(ring_frame)

        ring_grid = GLay(v_spacing=5, h_spacing=3)
        ring_frame.setLayout(ring_grid)

        # Circular Annular Ring Value
        self.circular_ring_label = FCLabel('%s:' % _("Circular"))
        self.circular_ring_label.setToolTip(
            _("The size of annular ring for circular pads.")
        )

        self.circular_ring_entry = FCDoubleSpinner()
        self.circular_ring_entry.set_precision(self.decimals)
        self.circular_ring_entry.set_range(0.0000, 10000.0000)

        ring_grid.addWidget(self.circular_ring_label, 0, 0)
        ring_grid.addWidget(self.circular_ring_entry, 0, 1)

        # Oblong Annular Ring Value
        self.oblong_ring_label = FCLabel('%s:' % _("Oblong"))
        self.oblong_ring_label.setToolTip(
            _("The size of annular ring for oblong pads.")
        )

        self.oblong_ring_entry = FCDoubleSpinner()
        self.oblong_ring_entry.set_precision(self.decimals)
        self.oblong_ring_entry.set_range(0.0000, 10000.0000)

        ring_grid.addWidget(self.oblong_ring_label, 2, 0)
        ring_grid.addWidget(self.oblong_ring_entry, 2, 1)

        # Square Annular Ring Value
        self.square_ring_label = FCLabel('%s:' % _("Square"))
        self.square_ring_label.setToolTip(
            _("The size of annular ring for square pads.")
        )

        self.square_ring_entry = FCDoubleSpinner()
        self.square_ring_entry.set_precision(self.decimals)
        self.square_ring_entry.set_range(0.0000, 10000.0000)

        ring_grid.addWidget(self.square_ring_label, 4, 0)
        ring_grid.addWidget(self.square_ring_entry, 4, 1)

        # Rectangular Annular Ring Value
        self.rectangular_ring_label = FCLabel('%s:' % _("Rectangular"))
        self.rectangular_ring_label.setToolTip(
            _("The size of annular ring for rectangular pads.")
        )

        self.rectangular_ring_entry = FCDoubleSpinner()
        self.rectangular_ring_entry.set_precision(self.decimals)
        self.rectangular_ring_entry.set_range(0.0000, 10000.0000)

        ring_grid.addWidget(self.rectangular_ring_label, 6, 0)
        ring_grid.addWidget(self.rectangular_ring_entry, 6, 1)

        # Others Annular Ring Value
        self.other_ring_label = FCLabel('%s:' % _("Others"))
        self.other_ring_label.setToolTip(
            _("The size of annular ring for other pads.")
        )

        self.other_ring_entry = FCDoubleSpinner()
        self.other_ring_entry.set_precision(self.decimals)
        self.other_ring_entry.set_range(0.0000, 10000.0000)

        ring_grid.addWidget(self.other_ring_label, 8, 0)
        ring_grid.addWidget(self.other_ring_entry, 8, 1)

        # #############################################################################################################
        # Proportional Diameter Frame
        # #############################################################################################################
        self.prop_label = FCLabel('%s' % _("Proportional Diameter"), bold=True)
        self.layout.addWidget(self.prop_label)

        prop_frame = FCFrame()
        self.layout.addWidget(prop_frame)

        prop_grid = GLay(v_spacing=5, h_spacing=3)
        prop_frame.setLayout(prop_grid)

        # Factor value
        self.factor_entry = FCDoubleSpinner(suffix='%')
        self.factor_entry.set_precision(self.decimals)
        self.factor_entry.set_range(0.01, 100.0000)
        self.factor_entry.setSingleStep(0.1)

        self.factor_label = FCLabel('%s:' % _("Factor"))
        self.factor_label.setToolTip(
            _("Proportional Diameter.\n"
              "The hole diameter will be a fraction of the pad size.")
        )

        prop_grid.addWidget(self.factor_label, 0, 0)
        prop_grid.addWidget(self.factor_entry, 0, 1)

        # #############################################################################################################
        # Extract Soldermask Frame
        # #############################################################################################################
        self.extract_sm_label = FCLabel('%s' % _("Extract Soldermask"), bold=True)
        self.extract_sm_label.setToolTip(
            _("Extract soldermask from a given Gerber file."))
        self.layout.addWidget(self.extract_sm_label)

        solder_frame = FCFrame()
        self.layout.addWidget(solder_frame)

        solder_grid = GLay(v_spacing=5, h_spacing=3)
        solder_frame.setLayout(solder_grid)

        # CLEARANCE soldermask extraction
        self.clearance_label = FCLabel('%s:' % _("Clearance"))
        self.clearance_label.setToolTip(
            _("This set how much the soldermask extends\n"
              "beyond the margin of the pads.")
        )
        self.clearance_entry = FCDoubleSpinner()
        self.clearance_entry.set_range(0.0000, 10000.0000)
        self.clearance_entry.set_precision(self.decimals)
        self.clearance_entry.setSingleStep(0.1)

        solder_grid.addWidget(self.clearance_label, 0, 0)
        solder_grid.addWidget(self.clearance_entry, 0, 1)

        # Include regions
        self.include_regions_cb = FCCheckBox('%s' % _("Include regions"))
        self.include_regions_cb.setToolTip(
            _("Also extract the copper regions (G36 areas) into the soldermask.")
        )
        solder_grid.addWidget(self.include_regions_cb, 2, 0, 1, 2)

        # #############################################################################################################
        # Extract CutOut Frame
        # #############################################################################################################
        self.extract_cut_label = FCLabel('%s' % _("Extract Cutout"), bold=True)
        self.extract_cut_label.setToolTip(
            _("Extract a cutout from a given Gerber file."))
        self.layout.addWidget(self.extract_cut_label)

        ecut_frame = FCFrame()
        self.layout.addWidget(ecut_frame)

        ecut_grid = GLay(v_spacing=5, h_spacing=3)
        ecut_frame.setLayout(ecut_grid)

        # Margin Cutout
        self.margin_cut_label = FCLabel('%s:' % _("Margin"))
        self.margin_cut_label.setToolTip(
            _("Margin over bounds. A positive value here\n"
              "will make the cutout of the PCB further from\n"
              "the actual PCB border")
        )
        self.margin_cut_entry = FCDoubleSpinner()
        self.margin_cut_entry.set_range(-10000.0000, 10000.0000)
        self.margin_cut_entry.set_precision(self.decimals)
        self.margin_cut_entry.setSingleStep(0.1)

        ecut_grid.addWidget(self.margin_cut_label, 0, 0)
        ecut_grid.addWidget(self.margin_cut_entry, 0, 1)

        # Thickness Cutout
        self.thick_cut_label = FCLabel('%s:' % _("Thickness"))
        self.thick_cut_label.setToolTip(
            _("The thickness of the line that makes the cutout geometry.")
        )
        self.thick_cut_entry = FCDoubleSpinner()
        self.thick_cut_entry.set_range(0.0001, 10000.0000)
        self.thick_cut_entry.set_precision(self.decimals)
        self.thick_cut_entry.setSingleStep(0.1)

        ecut_grid.addWidget(self.thick_cut_label, 2, 0)
        ecut_grid.addWidget(self.thick_cut_entry, 2, 1)

        # Cutout shape
        self.cut_shape_label = FCLabel('%s:' % _("Shape"))
        self.cut_shape_label.setToolTip(
            _("Rectangular: the bounding box of the Gerber.\n"
              "Convex: the convex hull of the Gerber.")
        )
        self.cut_shape_radio = RadioSet(
            [{'label': _("Rectangular"), 'value': 'rect'}, {'label': _("Convex"), 'value': 'convex'}],
            compact=True)
        ecut_grid.addWidget(self.cut_shape_label, 4, 0)
        ecut_grid.addWidget(self.cut_shape_radio, 4, 1)

        GLay.set_common_column_size(
            [param_grid, ring_grid, fix_grid, prop_grid, met_grid, solder_grid, ecut_grid], 0)

        self.layout.addStretch()
