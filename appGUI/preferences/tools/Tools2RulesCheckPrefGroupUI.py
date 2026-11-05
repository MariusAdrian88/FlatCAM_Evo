
from PyQt6 import QtWidgets

from appGUI.GUIElements import FCCheckBox, FCDoubleSpinner, FCLabel, GLay, FCFrame
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class Tools2RulesCheckPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(Tools2RulesCheckPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Check Rules Plugin")))
        self.setToolTip(
            _("A tool to check if Gerber files are within a set\n"
              "of Manufacturing Rules.")
        )
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # Rules Frame
        # #############################################################################################################
        rules_copper_label = FCLabel('%s %s' % (_("Copper"), _("Rules")), bold=True)
        self.layout.addWidget(rules_copper_label)

        copper_frame = FCFrame()
        self.layout.addWidget(copper_frame)

        self.copper_grid = GLay(v_spacing=5, h_spacing=3)
        copper_frame.setLayout(self.copper_grid)

        # Trace size
        self.trace_size_cb = FCCheckBox('%s:' % _("Trace Size"))
        self.trace_size_cb.setToolTip(
            _("This checks if the minimum size for traces is met.")
        )
        self.copper_grid.addWidget(self.trace_size_cb, 0, 0, 1, 2)

        # Trace size value
        self.trace_size_lbl = FCLabel('%s:' % _("Value"))
        self.trace_size_lbl.setToolTip(
            _("Minimum acceptable trace size.")
        )

        self.trace_size_entry = FCDoubleSpinner()
        self.trace_size_entry.set_range(0.0000, 10000.0000)
        self.trace_size_entry.set_precision(self.decimals)
        self.trace_size_entry.setSingleStep(0.1)

        self.copper_grid.addWidget(self.trace_size_lbl, 2, 0)
        self.copper_grid.addWidget(self.trace_size_entry, 2, 1)

        # Copper2copper clearance
        self.clearance_copper2copper_cb = FCCheckBox('%s:' % _("Copper to Copper clearance"))
        self.clearance_copper2copper_cb.setToolTip(
            _("This checks if the minimum clearance between copper\n"
              "features is met.")
        )
        self.copper_grid.addWidget(self.clearance_copper2copper_cb, 4, 0, 1, 2)

        # Copper2copper clearance value
        self.clearance_copper2copper_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_copper2copper_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_copper2copper_entry = FCDoubleSpinner()
        self.clearance_copper2copper_entry.set_range(0.0000, 10000.0000)
        self.clearance_copper2copper_entry.set_precision(self.decimals)
        self.clearance_copper2copper_entry.setSingleStep(0.1)

        self.copper_grid.addWidget(self.clearance_copper2copper_lbl, 6, 0)
        self.copper_grid.addWidget(self.clearance_copper2copper_entry, 6, 1)

        # Copper2outline clearance
        self.clearance_copper2ol_cb = FCCheckBox('%s:' % _("Copper to Outline clearance"))
        self.clearance_copper2ol_cb.setToolTip(
            _("This checks if the minimum clearance between copper\n"
              "features and the outline is met.")
        )
        self.copper_grid.addWidget(self.clearance_copper2ol_cb, 8, 0, 1, 2)

        # Copper2outline clearance value
        self.clearance_copper2ol_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_copper2ol_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_copper2ol_entry = FCDoubleSpinner()
        self.clearance_copper2ol_entry.set_range(0.0000, 10000.0000)
        self.clearance_copper2ol_entry.set_precision(self.decimals)
        self.clearance_copper2ol_entry.setSingleStep(0.1)

        self.copper_grid.addWidget(self.clearance_copper2ol_lbl, 10, 0)
        self.copper_grid.addWidget(self.clearance_copper2ol_entry, 10, 1)

        # Ring integrity check
        self.ring_integrity_cb = FCCheckBox('%s:' % _("Minimum Annular Ring"))
        self.ring_integrity_cb.setToolTip(
            _("This checks if the minimum copper ring left by drilling\n"
              "a hole into a pad is met.")
        )
        self.copper_grid.addWidget(self.ring_integrity_cb, 12, 0, 1, 2)

        # Ring integrity value
        self.ring_integrity_lbl = FCLabel('%s:' % _("Value"))
        self.ring_integrity_lbl.setToolTip(
            _("Minimum acceptable ring value.")
        )

        self.ring_integrity_entry = FCDoubleSpinner()
        self.ring_integrity_entry.set_range(0.0000, 10000.0000)
        self.ring_integrity_entry.set_precision(self.decimals)
        self.ring_integrity_entry.setSingleStep(0.1)

        self.copper_grid.addWidget(self.ring_integrity_lbl, 14, 0)
        self.copper_grid.addWidget(self.ring_integrity_entry, 14, 1)

        # Copper gap versus the isolation tool
        self.iso_cb = FCCheckBox('%s:' % _("Copper gap vs isolation tool"))
        self.iso_cb.setToolTip(
            _("This checks if the gaps between copper features are wide\n"
              "enough for the isolation tool to pass.")
        )
        self.copper_grid.addWidget(self.iso_cb, 16, 0, 1, 2)

        # Isolation tool diameter
        self.iso_lbl = FCLabel('%s:' % _("Value"))
        self.iso_lbl.setToolTip(
            _("Diameter of the isolation tool. Copper gaps narrower than this cannot be isolated.")
        )

        self.iso_entry = FCDoubleSpinner()
        self.iso_entry.set_range(0.0000, 10000.0000)
        self.iso_entry.set_precision(self.decimals)
        self.iso_entry.setSingleStep(0.1)

        self.copper_grid.addWidget(self.iso_lbl, 18, 0)
        self.copper_grid.addWidget(self.iso_entry, 18, 1)

        # #############################################################################################################
        # Silk Frame
        # #############################################################################################################
        silk_copper_label = FCLabel('%s %s' % (_("Silk"), _("Rules")), bold=True)
        self.layout.addWidget(silk_copper_label)

        silk_frame = FCFrame()
        self.layout.addWidget(silk_frame)

        self.silk_grid = GLay(v_spacing=5, h_spacing=3)
        silk_frame.setLayout(self.silk_grid)

        # Silkscreen2silkscreen clearance
        self.clearance_silk2silk_cb = FCCheckBox('%s:' % _("Silk to Silk Clearance"))
        self.clearance_silk2silk_cb.setToolTip(
            _("This checks if the minimum clearance between silkscreen\n"
              "features and silkscreen features is met.")
        )
        self.silk_grid.addWidget(self.clearance_silk2silk_cb, 0, 0, 1, 2)

        # Copper2silkscreen clearance value
        self.clearance_silk2silk_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_silk2silk_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_silk2silk_entry = FCDoubleSpinner()
        self.clearance_silk2silk_entry.set_range(0.0000, 10000.0000)
        self.clearance_silk2silk_entry.set_precision(self.decimals)
        self.clearance_silk2silk_entry.setSingleStep(0.1)

        self.silk_grid.addWidget(self.clearance_silk2silk_lbl, 2, 0)
        self.silk_grid.addWidget(self.clearance_silk2silk_entry, 2, 1)

        # Silkscreen2soldermask clearance
        self.clearance_silk2sm_cb = FCCheckBox('%s:' % _("Silk to Solder Mask Clearance"))
        self.clearance_silk2sm_cb.setToolTip(
            _("This checks if the minimum clearance between silkscreen\n"
              "features and soldermask features is met.")
        )
        self.silk_grid.addWidget(self.clearance_silk2sm_cb, 4, 0, 1, 2)

        # Silkscreen2soldermask clearance value
        self.clearance_silk2sm_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_silk2sm_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_silk2sm_entry = FCDoubleSpinner()
        self.clearance_silk2sm_entry.set_range(0.0000, 10000.0000)
        self.clearance_silk2sm_entry.set_precision(self.decimals)
        self.clearance_silk2sm_entry.setSingleStep(0.1)

        self.silk_grid.addWidget(self.clearance_silk2sm_lbl, 6, 0)
        self.silk_grid.addWidget(self.clearance_silk2sm_entry, 6, 1)

        # Silk2outline clearance
        self.clearance_silk2ol_cb = FCCheckBox('%s:' % _("Silk to Outline Clearance"))
        self.clearance_silk2ol_cb.setToolTip(
            _("This checks if the minimum clearance between silk\n"
              "features and the outline is met.")
        )
        self.silk_grid.addWidget(self.clearance_silk2ol_cb, 8, 0, 1, 2)

        # Silk2outline clearance value
        self.clearance_silk2ol_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_silk2ol_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_silk2ol_entry = FCDoubleSpinner()
        self.clearance_silk2ol_entry.set_range(0.0000, 10000.0000)
        self.clearance_silk2ol_entry.set_precision(self.decimals)
        self.clearance_silk2ol_entry.setSingleStep(0.1)

        self.silk_grid.addWidget(self.clearance_silk2ol_lbl, 10, 0)
        self.silk_grid.addWidget(self.clearance_silk2ol_entry, 10, 1)

        # #############################################################################################################
        # Soldermask Frame
        # #############################################################################################################
        sm_copper_label = FCLabel('%s %s' % (_("Soldermask"), _("Rules")), bold=True)
        self.layout.addWidget(sm_copper_label)

        solder_frame = FCFrame()
        self.layout.addWidget(solder_frame)

        self.solder_grid = GLay(v_spacing=5, h_spacing=3)
        solder_frame.setLayout(self.solder_grid)

        # Soldermask2soldermask clearance
        self.clearance_sm2sm_cb = FCCheckBox('%s:' % _("Minimum Solder Mask Sliver"))
        self.clearance_sm2sm_cb.setToolTip(
            _("This checks if the minimum clearance between soldermask\n"
              "features and soldermask features is met.")
        )
        self.solder_grid.addWidget(self.clearance_sm2sm_cb, 0, 0, 1, 2)

        # Soldermask2soldermask clearance value

        self.clearance_sm2sm_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_sm2sm_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_sm2sm_entry = FCDoubleSpinner()
        self.clearance_sm2sm_entry.set_range(0.0000, 10000.0000)
        self.clearance_sm2sm_entry.set_precision(self.decimals)
        self.clearance_sm2sm_entry.setSingleStep(0.1)

        self.solder_grid.addWidget(self.clearance_sm2sm_lbl, 2, 0)
        self.solder_grid.addWidget(self.clearance_sm2sm_entry, 2, 1)

        # #############################################################################################################
        # Holes Frame
        # #############################################################################################################
        holes_copper_label = FCLabel('%s %s' % (_("Holes"), _("Rules")), bold=True)
        self.layout.addWidget(holes_copper_label)

        holes_frame = FCFrame()
        self.layout.addWidget(holes_frame)

        self.holes_grid = GLay(v_spacing=5, h_spacing=3)
        holes_frame.setLayout(self.holes_grid)

        # Hole2Hole clearance
        self.clearance_d2d_cb = FCCheckBox('%s:' % _("Hole to Hole Clearance"))
        self.clearance_d2d_cb.setToolTip(
            _("This checks if the minimum clearance between a drill hole\n"
              "and another drill hole is met.")
        )
        self.holes_grid.addWidget(self.clearance_d2d_cb, 0, 0, 1, 2)

        # Hole2Hole clearance value
        self.clearance_d2d_lbl = FCLabel('%s:' % _("Value"))
        self.clearance_d2d_lbl.setToolTip(
            _("Minimum acceptable clearance value.")
        )

        self.clearance_d2d_entry = FCDoubleSpinner()
        self.clearance_d2d_entry.set_range(0.0000, 10000.0000)
        self.clearance_d2d_entry.set_precision(self.decimals)
        self.clearance_d2d_entry.setSingleStep(0.1)

        self.holes_grid.addWidget(self.clearance_d2d_lbl, 2, 0)
        self.holes_grid.addWidget(self.clearance_d2d_entry, 2, 1)

        # Drill holes size check
        self.drill_size_cb = FCCheckBox('%s:' % _("Hole Size"))
        self.drill_size_cb.setToolTip(
            _("This checks if the drill holes\n"
              "sizes are above the threshold.")
        )
        self.holes_grid.addWidget(self.drill_size_cb, 4, 0, 1, 2)

        # Drills holes value
        self.drill_size_lbl = FCLabel('%s:' % _("Value"))
        self.drill_size_lbl.setToolTip(
            _("Minimum acceptable drill size.")
        )

        self.drill_size_entry = FCDoubleSpinner()
        self.drill_size_entry.set_range(0.0000, 10000.0000)
        self.drill_size_entry.set_precision(self.decimals)
        self.drill_size_entry.setSingleStep(0.1)

        self.holes_grid.addWidget(self.drill_size_lbl, 6, 0)
        self.holes_grid.addWidget(self.drill_size_entry, 6, 1)

        # Minimum slot width
        self.slot_size_cb = FCCheckBox('%s:' % _("Minimum Slot Width"))
        self.slot_size_cb.setToolTip(
            _("This checks if the width of the slots is above the threshold.")
        )
        self.holes_grid.addWidget(self.slot_size_cb, 8, 0, 1, 2)

        self.slot_size_lbl = FCLabel('%s:' % _("Value"))
        self.slot_size_lbl.setToolTip(
            _("Minimum acceptable slot width (the tool diameter that cuts the slot).")
        )

        self.slot_size_entry = FCDoubleSpinner()
        self.slot_size_entry.set_range(0.0000, 10000.0000)
        self.slot_size_entry.set_precision(self.decimals)
        self.slot_size_entry.setSingleStep(0.1)

        self.holes_grid.addWidget(self.slot_size_lbl, 10, 0)
        self.holes_grid.addWidget(self.slot_size_entry, 10, 1)

        # Hole to outline clearance
        self.hole2outline_cb = FCCheckBox('%s:' % _("Hole to Outline clearance"))
        self.hole2outline_cb.setToolTip(
            _("This checks if the minimum distance between a hole\n"
              "and the board outline is met.")
        )
        self.holes_grid.addWidget(self.hole2outline_cb, 12, 0, 1, 2)

        self.hole2outline_lbl = FCLabel('%s:' % _("Value"))
        self.hole2outline_lbl.setToolTip(
            _("Minimum acceptable distance between the edge of a hole and the board outline.")
        )

        self.hole2outline_entry = FCDoubleSpinner()
        self.hole2outline_entry.set_range(0.0000, 10000.0000)
        self.hole2outline_entry.set_precision(self.decimals)
        self.hole2outline_entry.setSingleStep(0.1)

        self.holes_grid.addWidget(self.hole2outline_lbl, 14, 0)
        self.holes_grid.addWidget(self.hole2outline_entry, 14, 1)

        # Hole to copper clearance
        self.hole2copper_cb = FCCheckBox('%s:' % _("Hole to Copper clearance"))
        self.hole2copper_cb.setToolTip(
            _("This checks if the minimum distance between a hole\n"
              "and copper that is not its own pad is met.")
        )
        self.holes_grid.addWidget(self.hole2copper_cb, 16, 0, 1, 2)

        self.hole2copper_lbl = FCLabel('%s:' % _("Value"))
        self.hole2copper_lbl.setToolTip(
            _("Minimum acceptable distance between the edge of a hole and foreign copper.")
        )

        self.hole2copper_entry = FCDoubleSpinner()
        self.hole2copper_entry.set_range(0.0000, 10000.0000)
        self.hole2copper_entry.set_precision(self.decimals)
        self.hole2copper_entry.setSingleStep(0.1)

        self.holes_grid.addWidget(self.hole2copper_lbl, 18, 0)
        self.holes_grid.addWidget(self.hole2copper_entry, 18, 1)

        self.layout.addStretch(1)
