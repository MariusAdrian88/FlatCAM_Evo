
from PyQt6 import QtWidgets, QtCore

from appGUI.GUIElements import FCLabel, FCComboBox, GLay, FCFrame, FCCheckBox, RadioSet
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class GeometryExpPrefGroupUI(OptionsGroupUI):

    def __init__(self, app, parent=None):
        super(GeometryExpPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Export")))
        self.decimals = app.decimals
        self.options = app.options

        # #############################################################################################################
        # Export Frame
        # #############################################################################################################
        self.export_options_label = FCLabel('%s' % _("Export Options"), color='brown', bold=True)
        self.export_options_label.setToolTip(
            _("The parameters set here are used in the file exported\n"
              "when using the File -> Export -> Export DXF menu entry.")
        )
        self.layout.addWidget(self.export_options_label)

        export_frame = FCFrame()
        self.layout.addWidget(export_frame)

        export_grid = GLay(v_spacing=5, h_spacing=3)
        export_frame.setLayout(export_grid)

        # DXF format
        self.dxf_format_label = FCLabel("%s:" % _("Format"))
        self.dxf_format_label.setToolTip(
            _("Autodesk DXF Format used when exporting Geometry as DXF.")
        )

        self.dxf_format_combo = FCComboBox()
        self.dxf_format_combo.addItems(['R12', 'R2000', 'R2004', 'R2007', 'R2010', 'R2013', 'R2018'])

        export_grid.addWidget(self.dxf_format_label, 0, 0)
        export_grid.addWidget(self.dxf_format_combo, 0, 1)

        # DXF layers
        self.dxf_layers_label = FCLabel("%s:" % _("Layers"))
        self.dxf_layers_label.setToolTip(
            _("How the DXF layers are named.\n"
              "Tool: one layer per tool.\n"
              "Object: one layer per object.")
        )
        self.dxf_layers_radio = RadioSet([{'label': _('Tool'), 'value': 'tool'},
                                          {'label': _('Object'), 'value': 'object'}], compact=True)
        export_grid.addWidget(self.dxf_layers_label, 1, 0)
        export_grid.addWidget(self.dxf_layers_radio, 1, 1)

        # Gerber traces
        self.dxf_traces_label = FCLabel("%s:" % _("Gerber"))
        self.dxf_traces_label.setToolTip(
            _("How Gerber traces are exported.\n"
              "Outline: the copper shape.\n"
              "Centerline: the trace axis.")
        )
        self.dxf_traces_radio = RadioSet([{'label': _('Outline'), 'value': 'outline'},
                                          {'label': _('Centerline'), 'value': 'centerline'}], compact=True)
        export_grid.addWidget(self.dxf_traces_label, 2, 0)
        export_grid.addWidget(self.dxf_traces_radio, 2, 1)

        # CNCJob travel moves
        self.dxf_travel_cb = FCCheckBox(_("Travel moves"))
        self.dxf_travel_cb.setToolTip(_("Export the travel moves of a CNCJob."))
        export_grid.addWidget(self.dxf_travel_cb, 3, 0, 1, 2)

        # Arcs and circles
        self.dxf_arcs_cb = FCCheckBox(_("Arcs and circles"))
        self.dxf_arcs_cb.setToolTip(_("Export arcs and circles as DXF arcs and circles, not as polylines."))
        export_grid.addWidget(self.dxf_arcs_cb, 4, 0, 1, 2)

        # SVG format
        self.svg_paths_only_cb = FCCheckBox(_("SVG Paths-Only"))
        self.svg_paths_only_cb.setToolTip(
            _("SVG Format. When checked it will export only paths.")
        )
        export_grid.addWidget(self.svg_paths_only_cb, 5, 0, 1, 2)

        self.layout.addStretch()
