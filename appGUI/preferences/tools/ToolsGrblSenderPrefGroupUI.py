
from PyQt6 import QtWidgets

from appGUI.GUIElements import FCDoubleSpinner, FCSpinner, RadioSet, FCLabel, FCComboBox, GLay, FCFrame, FCCheckBox, FCEntry
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolsGrblSenderPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):
        super(ToolsGrblSenderPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("GCode sender Plugin")))
        self.decimals = app.decimals
        self.options = app.options

        connection_frame = FCFrame()
        self.layout.addWidget(connection_frame)
        connection_grid = GLay(v_spacing=5, h_spacing=3)
        connection_frame.setLayout(connection_grid)
        self.controller_radio = RadioSet([
            {'label': 'GRBL', 'value': 'grbl'},
            {'label': 'grblHAL', 'value': 'grblhal'},
            {'label': 'E-Cut', 'value': 'ecut'}])
        self.connection_radio = RadioSet([
            {'label': _('USB Serial'), 'value': 'serial'},
            {'label': _('Native USB'), 'value': 'native'},
            {'label': _('Network'), 'value': 'network'}])
        self.serial_port_entry = FCEntry()
        self.network_host_entry = FCEntry()
        self.network_host_entry.setToolTip(_("grblHAL raw TCP host/IP on a trusted LAN."))
        self.network_port_entry = FCSpinner()
        self.network_port_entry.set_range(1, 65535)
        self.controller_radio.set_value('grbl')
        self.connection_radio.set_value('serial')
        self.network_port_entry.set_value(23)
        for row, (label, widget) in enumerate((
                (_("Controller"), self.controller_radio),
                (_("grblHAL transport"), self.connection_radio),
                (_("Serial port"), self.serial_port_entry),
                (_("Network host/IP"), self.network_host_entry),
                (_("Network port"), self.network_port_entry))):
            connection_grid.addWidget(FCLabel('%s:' % label), row, 0)
            connection_grid.addWidget(widget, row, 1)

        self.grbl_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.grbl_label.setToolTip(
            _("Send the CNC Code to a GRBL controller.")
        )
        self.layout.addWidget(self.grbl_label)

        # #############################################################################################################
        # Parameters Frame
        # #############################################################################################################
        par_frame = FCFrame()
        self.layout.addWidget(par_frame)

        par_grid = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(par_grid)

        # JOG Step
        self.jog_step_label = FCLabel('%s:' % _("Step"))
        self.jog_step_label.setToolTip(
            _("Each jog action will move the axes with this value.")
        )

        self.jog_step_entry = FCDoubleSpinner()
        self.jog_step_entry.set_precision(self.decimals)
        self.jog_step_entry.set_range(0, 910000.0000)

        par_grid.addWidget(self.jog_step_label, 0, 0)
        par_grid.addWidget(self.jog_step_entry, 0, 1)

        # JOG Feedrate
        self.jog_fr_label = FCLabel('%s:' % _("Feedrate"))
        self.jog_fr_label.setToolTip(
            _("Feedrate when jogging.")
        )

        self.jog_fr_entry = FCDoubleSpinner()
        self.jog_fr_entry.set_precision(self.decimals)
        self.jog_fr_entry.set_range(0, 910000.0000)

        par_grid.addWidget(self.jog_fr_label, 2, 0)
        par_grid.addWidget(self.jog_fr_entry, 2, 1)

        # JOG Travel Z
        self.jog_travelz_label = FCLabel('%s:' % _("Travel Z"))
        self.jog_travelz_label.setToolTip(
            _("Safe height (Z) distance when jogging to origin.")
        )

        self.jog_travelz_entry = FCDoubleSpinner()
        self.jog_travelz_entry.set_precision(self.decimals)
        self.jog_travelz_entry.set_range(0, 910000.0000)

        par_grid.addWidget(self.jog_travelz_label, 4, 0)
        par_grid.addWidget(self.jog_travelz_entry, 4, 1)

        # Baud rate
        self.baudrate_label = FCLabel('%s:' % _("Baud rate"))
        self.baudrate_label.setToolTip(
            _("The baud rate that is selected for the serial connection.")
        )

        self.baudrate_combo = FCComboBox()
        self.baudrate_combo.addItems(
            ['9600', '19200', '38400', '57600', '115200', '230400', '460800', '500000', '576000', '921600', '1000000',
             '1152000', '1500000', '2000000'])

        par_grid.addWidget(self.baudrate_label, 6, 0)
        par_grid.addWidget(self.baudrate_combo, 6, 1)

        # Arc mode
        self.arc_mode_label = FCLabel('%s:' % _("Arcs"))
        self.arc_mode_label.setToolTip(
            _("Split: the sender splits G2/G3 arcs into G1 lines.\n"
              "Send: the sender sends G2/G3 to GRBL.\n"
              "With autolevelling on, arcs are always split.")
        )

        self.arc_mode_radio = RadioSet(
            [
                {'label': _('Split'), 'value': 'split'},
                {'label': _('Send'), 'value': 'send'}
            ])
        self.arc_mode_radio.setToolTip(self.arc_mode_label.toolTip())

        par_grid.addWidget(self.arc_mode_label, 8, 0)
        par_grid.addWidget(self.arc_mode_radio, 8, 1)

        # Arc segment
        self.arc_segment_label = FCLabel('%s:' % _("Arc segment"))
        self.arc_segment_label.setToolTip(
            _("The length of the G1 lines that replace an arc.")
        )

        self.arc_segment_entry = FCDoubleSpinner()
        self.arc_segment_entry.set_precision(self.decimals)
        self.arc_segment_entry.set_range(0.001, 100.0000)

        par_grid.addWidget(self.arc_segment_label, 10, 0)
        par_grid.addWidget(self.arc_segment_entry, 10, 1)

        # Segment length
        self.seg_len_label = FCLabel('%s:' % _("Segment length"))
        self.seg_len_label.setToolTip(
            _("Longest G1 line when the G-code is not segmented.\n"
              "Used only with autolevelling.")
        )

        self.seg_len_entry = FCDoubleSpinner()
        self.seg_len_entry.set_precision(self.decimals)
        self.seg_len_entry.set_range(0.001, 1000.0000)
        self.seg_len_entry.setToolTip(self.seg_len_label.toolTip())

        par_grid.addWidget(self.seg_len_label, 12, 0)
        par_grid.addWidget(self.seg_len_entry, 12, 1)

        # Status interval
        self.status_ms_label = FCLabel('%s:' % _("Status interval (ms)"))
        self.status_ms_label.setToolTip(
            _("How often the sender asks GRBL for a status report.")
        )

        self.status_ms_entry = FCSpinner()
        self.status_ms_entry.set_range(50, 2000)

        par_grid.addWidget(self.status_ms_label, 14, 0)
        par_grid.addWidget(self.status_ms_entry, 14, 1)

        # Tool-change touchplate
        self.toolchange_plate_label = FCLabel('%s:' % _("Tool-change touchplate"))
        self.toolchange_plate_label.setToolTip(
            _("Disabled: disable calibrated tool-change measurement.\n"
              "Fixed: use a fixed touchplate calibrated against the reference tool.\n"
              "Removable: use a touchplate placed on the PCB, accounting for its thickness.")
        )

        self.toolchange_plate_radio = RadioSet(
            [
                {'label': _('Disabled'), 'value': 'disabled'},
                {'label': _('Fixed'), 'value': 'fixed'},
                {'label': _('Removable'), 'value': 'removable'}
            ])
        self.toolchange_plate_radio.setToolTip(self.toolchange_plate_label.toolTip())

        par_grid.addWidget(self.toolchange_plate_label, 16, 0)
        par_grid.addWidget(self.toolchange_plate_radio, 16, 1)

        # Calibrated manual tool change
        self.toolchange_distance_label = FCLabel('%s:' % _("Probe distance"))
        self.toolchange_distance_label.setToolTip(
            _("Longest downward probe travel from the start position. Must be positive.")
        )
        self.toolchange_distance_entry = FCDoubleSpinner()
        self.toolchange_distance_entry.set_precision(self.decimals)
        self.toolchange_distance_entry.set_range(0.0001, 910000.0)

        self.toolchange_clearance_label = FCLabel('%s:' % _("Retract clearance"))
        self.toolchange_clearance_label.setToolTip(
            _("Distance above the contact to retract to after a successful probe. Must be positive.")
        )
        self.toolchange_clearance_entry = FCDoubleSpinner()
        self.toolchange_clearance_entry.set_precision(self.decimals)
        self.toolchange_clearance_entry.set_range(0.0001, 910000.0)

        self.toolchange_feed_label = FCLabel('%s:' % _("Probe feedrate"))
        self.toolchange_feed_label.setToolTip(
            _("Feedrate of the probe move. Must be positive.")
        )
        self.toolchange_feed_entry = FCDoubleSpinner()
        self.toolchange_feed_entry.set_precision(self.decimals)
        self.toolchange_feed_entry.set_range(0.0001, 910000.0)

        self.toolchange_xy_label = FCLabel('%s:' % _("Plate position tolerance"))
        self.toolchange_xy_label.setToolTip(
            _("Largest XY distance between the fixed reference contact and a later measurement. Must be positive.")
        )
        self.toolchange_xy_entry = FCDoubleSpinner()
        self.toolchange_xy_entry.set_precision(self.decimals)
        self.toolchange_xy_entry.set_range(0.0001, 910000.0)

        self.toolchange_thickness_label = FCLabel('%s:' % _("Removable plate thickness"))
        self.toolchange_thickness_label.setToolTip(
            _("Thickness of the removable plate placed on the PCB. Must be positive.")
        )
        self.toolchange_thickness_entry = FCDoubleSpinner()
        self.toolchange_thickness_entry.set_precision(self.decimals)
        self.toolchange_thickness_entry.set_range(0.0001, 910000.0)

        par_grid.addWidget(self.toolchange_distance_label, 18, 0)
        par_grid.addWidget(self.toolchange_distance_entry, 18, 1)
        par_grid.addWidget(self.toolchange_clearance_label, 20, 0)
        par_grid.addWidget(self.toolchange_clearance_entry, 20, 1)
        par_grid.addWidget(self.toolchange_feed_label, 22, 0)
        par_grid.addWidget(self.toolchange_feed_entry, 22, 1)
        par_grid.addWidget(self.toolchange_xy_label, 24, 0)
        par_grid.addWidget(self.toolchange_xy_entry, 24, 1)
        par_grid.addWidget(self.toolchange_thickness_label, 26, 0)
        par_grid.addWidget(self.toolchange_thickness_entry, 26, 1)

        # These preference fields stay metric when the application's display units change.
        stored_units = 'MM'
        for label in (self.toolchange_distance_label, self.toolchange_clearance_label,
                      self.toolchange_xy_label, self.toolchange_thickness_label):
            label.setText('%s (%s)' % (label.text(), stored_units))
        self.toolchange_feed_label.setText('%s (%s/min)' % (self.toolchange_feed_label.text(), stored_units))
        self.toolchange_plate_radio.activated_custom.connect(self._on_toolchange_mode)
        self._on_toolchange_mode(self.options.get('tools_grbl_toolchange_plate', 'disabled'))

        self.layout.addStretch(1)

    def _on_toolchange_mode(self, mode=None, *args):
        mode = str(mode or self.toolchange_plate_radio.get_value() or 'disabled')
        active = mode in ('fixed', 'removable')
        for widget in (self.toolchange_distance_label, self.toolchange_distance_entry,
                       self.toolchange_clearance_label, self.toolchange_clearance_entry,
                       self.toolchange_feed_label, self.toolchange_feed_entry):
            widget.setVisible(active)
        for widget in (self.toolchange_xy_label, self.toolchange_xy_entry):
            widget.setVisible(mode == 'fixed')
        for widget in (self.toolchange_thickness_label, self.toolchange_thickness_entry):
            widget.setVisible(mode == 'removable')
