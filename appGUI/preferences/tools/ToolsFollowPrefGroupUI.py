
from appGUI.GUIElements import FCCheckBox, FCLabel, FCFrame, GLay, RadioSet, FCDoubleSpinner, FCEntry
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolsFollowPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(ToolsFollowPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Follow Plugin")))
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # PATHS Frame
        # #############################################################################################################
        self.paths_label = FCLabel('%s' % _("Paths"), bold=True)
        self.paths_label.setToolTip(
            _("Create a Geometry object that follows the centerlines of the\n"
              "Gerber traces: one single-pass path per trace, no copper outline.")
        )
        self.layout.addWidget(self.paths_label)

        paths_frame = FCFrame()
        self.layout.addWidget(paths_frame)

        paths_grid = GLay(v_spacing=5, h_spacing=3)
        paths_frame.setLayout(paths_grid)

        self.select_label = FCLabel('%s:' % _("Selection"))
        self.select_label.setToolTip(
            _("All: follow every path of the object.\n"
              "Area: follow only the parts of the paths inside the areas drawn on the canvas;\n"
              "paths crossing the border of an area are cut at the border.")
        )
        self.select_radio = RadioSet([
            {'label': _("All"), 'value': 'all'},
            {'label': _("Area"), 'value': 'area'},
        ], compact=True)
        paths_grid.addWidget(self.select_label, 0, 0)
        paths_grid.addWidget(self.select_radio, 0, 1, 1, 2)

        self.area_shape_label = FCLabel('%s:' % _("Area shape"))
        self.area_shape_label.setToolTip(
            _("The shape of the area drawn on the canvas.\n"
              "Square: a rectangle defined by two corners.\n"
              "Polygon: a free polygon defined by its points.")
        )
        self.area_shape_radio = RadioSet([
            {'label': _("Square"), 'value': 'square'},
            {'label': _("Polygon"), 'value': 'polygon'},
        ], compact=True)
        paths_grid.addWidget(self.area_shape_label, 1, 0)
        paths_grid.addWidget(self.area_shape_radio, 1, 1, 1, 2)

        self.dia_mode_label = FCLabel('%s:' % _("Tool diameter"))
        self.dia_mode_label.setToolTip(
            _("Mill default: one tool, with the diameter of the Milling plugin default.\n"
              "Aperture size: one tool for each aperture size of the Gerber object,\n"
              "each tool holding the paths drawn with that aperture.")
        )
        self.dia_mode_radio = RadioSet([
            {'label': _("Mill default"), 'value': 'default'},
            {'label': _("Aperture size"), 'value': 'aperture'},
        ], compact=True)
        paths_grid.addWidget(self.dia_mode_label, 2, 0)
        paths_grid.addWidget(self.dia_mode_radio, 2, 1, 1, 2)

        # #############################################################################################################
        # CLEANUP Frame
        # #############################################################################################################
        self.cleanup_label = FCLabel('%s' % _("Cleanup"), bold=True)
        self.layout.addWidget(self.cleanup_label)

        cleanup_frame = FCFrame()
        self.layout.addWidget(cleanup_frame)

        cleanup_grid = GLay(v_spacing=5, h_spacing=3)
        cleanup_frame.setLayout(cleanup_grid)

        self.union_cb = FCCheckBox(_("Union"))
        self.union_cb.setToolTip(
            _("Join all paths into one geometry. Crossing paths are split\n"
              "at the crossing and then merged again.")
        )
        cleanup_grid.addWidget(self.union_cb, 0, 0, 1, 3)

        self.merge_cb = FCCheckBox(_("Merge paths"))
        self.merge_cb.setToolTip(
            _("Join paths that touch end to end into one longer path,\n"
              "so the machine lifts the tool less often.")
        )
        cleanup_grid.addWidget(self.merge_cb, 1, 0, 1, 3)

        self.simplification_cb = FCCheckBox(_("Simplification"))
        self.simplification_cb.setToolTip(
            _("Remove the points of each path that deviate less than the tolerance\n"
              "from the straight line. The result has fewer points and the same shape within the tolerance.")
        )
        cleanup_grid.addWidget(self.simplification_cb, 2, 0, 1, 3)

        self.tolerance_label = FCLabel('%s:' % _("Tolerance"))
        self.tolerance_label.setToolTip(
            _("The largest distance a simplified path may move away from the original path.")
        )
        self.tolerance_entry = FCDoubleSpinner()
        self.tolerance_entry.set_range(0.0, 10000.0)
        self.tolerance_entry.set_precision(max(int(self.decimals), 4))
        self.tolerance_entry.setSingleStep(0.01)
        self.tolerance_units_label = FCLabel(str(app.app_units).lower())
        cleanup_grid.addWidget(self.tolerance_label, 3, 0)
        cleanup_grid.addWidget(self.tolerance_entry, 3, 1)
        cleanup_grid.addWidget(self.tolerance_units_label, 3, 2)

        # #############################################################################################################
        # OUTPUT Frame
        # #############################################################################################################
        self.output_label = FCLabel('%s' % _("Output"), bold=True)
        self.layout.addWidget(self.output_label)

        output_frame = FCFrame()
        self.layout.addWidget(output_frame)

        output_grid = GLay(v_spacing=5, h_spacing=3)
        output_frame.setLayout(output_grid)

        self.suffix_label = FCLabel('%s:' % _("Name suffix"))
        self.suffix_label.setToolTip(_("Added to the name of the Gerber object to make the name of the result."))
        self.suffix_entry = FCEntry()
        output_grid.addWidget(self.suffix_label, 0, 0)
        output_grid.addWidget(self.suffix_entry, 0, 1, 1, 2)

        self.layout.addStretch()

        self.simplification_cb.stateChanged.connect(self._on_simplification_changed)
        self._on_simplification_changed()

    def _on_simplification_changed(self, *args):
        enabled = self.simplification_cb.isChecked()
        self.tolerance_label.setEnabled(enabled)
        self.tolerance_entry.setEnabled(enabled)
        self.tolerance_units_label.setEnabled(enabled)
