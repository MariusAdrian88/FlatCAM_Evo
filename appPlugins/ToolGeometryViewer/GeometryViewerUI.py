"""Controls for the Geometry 3D preview."""

from PyQt6 import QtCore, QtWidgets

from appGUI.GUIElements import (FCCheckBox, FCColorEntry, FCDoubleSpinner, FCFrame, FCLabel,
                                FCSliderWithSpinner, GLay, RadioSet)

import gettext

_ = gettext.gettext


class GeometryViewerUI:
    def __init__(self, layout, app, name, source_name=''):
        layout.addWidget(FCLabel(_('Geometry 3D Viewer'), size=16, bold=True))
        title = FCLabel(name)
        title.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(title)
        self.source = FCLabel('')
        self.source.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        if source_name:
            self.source.setText(_('Source: %s') % source_name)
        layout.addWidget(self.source)
        frame = FCFrame()
        grid = GLay(v_spacing=5, h_spacing=3)
        grid.setColumnStretch(1, 1)
        frame.setLayout(grid)
        layout.addWidget(frame)
        self.camera_reference = RadioSet([{'label': 'LMB', 'value': 'lmb'},
                                          {'label': _('Center'), 'value': 'center_rotate'}])
        self.camera_reference.set_value('center_rotate')
        self.mode = RadioSet([{'label': _('Material removal'), 'value': 'material'},
                              {'label': _('Tool coverage'), 'value': 'coverage'}], orientation='vertical')
        self.mode.set_value('coverage')
        self.thickness = FCDoubleSpinner()
        self.margin = FCDoubleSpinner()
        for spinner in (self.thickness, self.margin):
            spinner.set_precision(max(4, app.decimals))
            spinner.set_range(0, 100000)
        self.thickness.setMinimum(0.0001)
        self.set_units(app.app_units)
        unit_scale = 1 if app.app_units.upper() == 'MM' else 1 / 25.4
        self.thickness.set_value(0.8 * unit_scale)
        self.margin.set_value(unit_scale)
        self.color = FCColorEntry()
        self.color.set_value('#809966FF')
        self.copper_color = FCColorEntry()
        self.copper_color.set_value('#B87333FF')
        self.copper_thickness = FCSliderWithSpinner(min=10, max=1000)
        self.copper_thickness.spinner.setSuffix(' µm')
        self.copper_thickness.set_value(75)
        self.opacity = FCSliderWithSpinner(min=0, max=255)
        self.opacity.set_value(255)
        self.centerlines = FCCheckBox(label=_('Show centerlines'))
        for row, (label, widget) in enumerate(((_('Camera reference:'), self.camera_reference),
                                                (None, None),
                                                (_('Display mode:'), self.mode),
                                                (_('Board thickness:'), self.thickness),
                                                (_('Board margin:'), self.margin),
                                                (_('Board color:'), self.color),
                                                (_('Copper color:'), self.copper_color),
                                                (_('Copper thickness:'), self.copper_thickness),
                                                (_('Board opacity:'), self.opacity))):
            if label is None:
                separator_line = QtWidgets.QFrame()
                separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
                separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
                grid.addWidget(separator_line, row, 0, 1, 2)
                continue
            grid.addWidget(FCLabel(label), row, 0)
            grid.addWidget(widget, row, 1)
        layout.addWidget(self.centerlines)
        self.legend = FCLabel('')
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        self.status = FCLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()

    def set_units(self, units):
        self.units = units
        for spinner in (self.thickness, self.margin):
            spinner.setSuffix(' ' + units.lower())
