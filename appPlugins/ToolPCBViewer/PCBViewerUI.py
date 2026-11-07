"""PCB preview controls, using the same frames, project selectors and color dialogs as the GUI."""

from PyQt6 import QtCore, QtGui, QtWidgets

from appGUI.GUIElements import (FCButton, FCColorEntry, FCComboBox, FCFrame, FCLabel,
                                FCSliderWithDoubleSpinner, FCSliderWithSpinner, GLay, RadioSet)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class PCBViewerUI:
    def __init__(self, layout, app):
        self.app = app
        self.combos, self.colors = {}, {}
        layout.addWidget(FCLabel(_('PCB 3D Viewer'), size=16, bold=True))
        grids = []
        for title, color in ((_("Camera"), 'blue'), (_("PCB"), 'blue'), (_("Mechanical"), 'brown'), (_("Copper"), 'orange'),
                             (_("Soldermask"), 'red'), (_("Overlay"), 'green'), (_("Excellon"), 'purple')):
            layout.addWidget(FCLabel(title, color=color, bold=True))
            frame = FCFrame()
            grid = GLay(v_spacing=5, h_spacing=3)
            grid.setColumnStretch(1, 1)
            frame.setLayout(grid)
            layout.addWidget(frame)
            grids.append(grid)
        camera, pcb, mechanical, copper, mask, overlay, excellon = grids
        self.camera_reference = RadioSet([{'label': 'LMB', 'value': 'lmb'},
                                          {'label': _('Center'), 'value': 'center'}])
        self.camera_reference.set_value('center')
        camera.addWidget(FCLabel(_("Reference:")), 0, 0)
        camera.addWidget(self.camera_reference, 0, 1, 1, 2)
        self.board_thickness = FCSliderWithDoubleSpinner(min=0.2, max=3, step=0.1, precision=2)
        self.board_thickness.spinner.setSuffix(' mm')
        self.board_thickness.set_value(0.8)
        pcb.addWidget(FCLabel(_("Thickness:")), 0, 0)
        pcb.addWidget(self.board_thickness, 0, 1, 1, 2)
        self.add_color(pcb, 1, 'board', '#809966FF')
        self.board_alpha = FCSliderWithSpinner(min=0, max=255)
        self.board_alpha.set_value(255)
        pcb.addWidget(FCLabel(_("Opacity:")), 2, 0)
        pcb.addWidget(self.board_alpha, 2, 1, 1, 2)
        self.board_alpha.setToolTip(_("Alpha: 0 is transparent; 255 is opaque."))
        self.add_combo(mechanical, 0, 'outline', _("Outline"))
        self.combos['outline'].setToolTip(_("Closed board contours, including cutouts.\n"
                                           "Without an outline, the preview uses the combined bounding box."))
        self.add_combo(copper, 0, 'copper_top', _("Top"))
        self.add_combo(copper, 1, 'copper_bottom', _("Bottom"))
        self.add_color(copper, 2, 'copper', '#FFD700FF')
        self.copper_thickness = FCSliderWithSpinner(min=10, max=30)
        self.copper_thickness.spinner.setSuffix(' µm')
        self.copper_thickness.set_value(18)
        copper.addWidget(FCLabel(_("Thickness:")), 3, 0)
        copper.addWidget(self.copper_thickness, 3, 1, 1, 2)
        for grid, prefix, color in ((mask, 'soldermask', '#FF000080'), (overlay, 'overlay', '#FFFFFFFF')):
            self.add_combo(grid, 0, prefix + '_top', _("Top"))
            self.add_combo(grid, 1, prefix + '_bottom', _("Bottom"))
            self.add_color(grid, 2, prefix, color)
        self.colors['soldermask'].value_changed.connect(self.on_mask_color_changed)
        self.mask_thickness = FCSliderWithSpinner(min=80, max=200)
        self.mask_thickness.spinner.setSuffix(' µm')
        self.mask_thickness.set_value(100)
        mask.addWidget(FCLabel(_("Thickness:")), 3, 0)
        mask.addWidget(self.mask_thickness, 3, 1, 1, 2)
        drill_count = sum(obj.kind == 'excellon' for obj in app.collection.get_list())
        for index in range(max(2, drill_count)):
            name = 'drills' if index == 0 else 'drills_%d' % (index + 1)
            self.add_combo(excellon, index, name, _("Drills") + ' %d' % (index + 1), row=1)
            self.combos[name].setCurrentIndex(index if index < drill_count else -1)
        self.status = FCLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.export_pdf_button = FCButton(_('Export 3D PDF'))
        self.export_pdf_button.setToolTip(_("Requires a PDF reader with U3D support, such as Adobe Acrobat or Foxit.\n"
                                          "Other readers display the static PCB preview."))
        self.export_pdf_button.setEnabled(False)
        layout.addWidget(self.export_pdf_button)
        layout.addStretch()

    def on_mask_color_changed(self, value):
        entry = self.colors['overlay']
        current = entry.get_value()
        if current[:7].upper() in ('#FFFFFF', '#000000'):
            color = '#000000' if value[:7].upper() == '#FFFFFF' else '#FFFFFF'
            entry.set_value(color + current[7:])

    def add_color(self, grid, row, name, color):
        entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))
        entry.set_value(color)
        grid.addWidget(FCLabel(_("Color:")), row, 0)
        grid.addWidget(entry, row, 1, 1, 2)
        self.colors[name] = entry

    def add_combo(self, grid, position, name, label, row=0):
        combo = FCComboBox()
        combo.setModel(self.app.collection)
        combo.setRootModelIndex(self.app.collection.index(row, 0, QtCore.QModelIndex()))
        combo.obj_type = 'Excellon' if row else 'Gerber'
        combo.setPlaceholderText(_("None"))
        combo.setCurrentIndex(-1)
        clear = FCButton('×')
        clear.setFixedWidth(20)
        clear.setToolTip(_("Clear selection"))
        clear.clicked.connect(lambda: combo.setCurrentIndex(-1))
        holder = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(combo)
        layout.addWidget(clear)
        grid.addWidget(FCLabel(label + ':'), position, 0)
        grid.addWidget(holder, position, 1)
        self.combos[name] = combo

    def values(self):
        result = {key + '_color': entry.get_value() for key, entry in self.colors.items()}
        result.update(board_thickness=self.board_thickness.get_value(),
                      copper_thickness=self.copper_thickness.get_value(),
                      mask_thickness=self.mask_thickness.get_value(), board_alpha=self.board_alpha.get_value())
        return result
