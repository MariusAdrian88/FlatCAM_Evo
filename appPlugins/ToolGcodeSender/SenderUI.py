# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt

from appGUI.GUIElements import (
    FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, FCJog, FCDoubleSpinner, FCSpinner,
    RadioSet, FCDetachableTab, FCZeroAxes, FCSliderWithDoubleSpinner, FCEntry,
    VerticalScrollArea)
from appCommon.machine_profile.facts import active_profile_of
from appPlugins.Common.levelling_ui import build_probe_points_ui
from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI, CameraView, CurrentPageTabWidget
from appPlugins.ToolGcodeSender.SenderRegistrationUI import SenderRegistrationUI
from appPlugins.ToolGcodeSender.SenderToolChangeUI import SenderToolChangeUI
from appPlugins.ToolGcodeSender.SenderStatusUI import SenderStatusStrip

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class GcodeSenderUI:
    pluginName = _("GCode sender")

    def update_connection_controls(self, locked=False):
        controller = self.controller_combo.currentData()
        connection = self.connection_combo.currentData()
        grblhal = controller == 'grblhal'
        serial = controller == 'grbl' or (grblhal and connection == 'serial')
        com = controller == 'grbl' or (grblhal and connection in ('serial', 'native'))
        network = grblhal and connection == 'network'
        for visible, widgets in (
                (grblhal, (self.connection_label, self.connection_combo)),
                (com, (self.com_list_label, self.com_list_combo, self.com_search_button)),
                (serial, (self.baudrates_list_label, self.baudrates_list_combo, self.new_bd_label,
                          self.new_baudrate_entry, self.add_bd_button, self.del_bd_button)),
                (network, (self.network_host_label, self.network_host_entry,
                           self.network_port_label, self.network_port_entry)),
                (controller == 'ecut', (self.ecut_usb_label,))):
            for widget in widgets:
                widget.setVisible(visible)
        for widget in (self.controller_combo, self.connection_combo, self.com_list_combo,
                       self.com_search_button, self.baudrates_list_combo, self.new_baudrate_entry,
                       self.add_bd_button, self.del_bd_button, self.network_host_entry, self.network_port_entry):
            widget.setEnabled(not locked)
        self.com_connect_button.setEnabled(True)

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(
            _("Send G-code to a controller (GRBL, grblHAL or E-Cut).")
        )

        self.title_box.addWidget(title_label)
        self.title_box.addStretch()

        # App Level label
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(
            _(
                "Beginner Mode - many parameters are hidden.\n"
                "Advanced Mode - full control.\n"
                "Permanent change is done in 'Preferences' menu."
            )
        )
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        # Machine Profiles: shown only while a profile is active
        self.machine_profile_label = FCLabel(_("Machine profile: not checked"))
        self.machine_profile_label.setVisible(active_profile_of(self.app) is not None)
        self.tools_box.addWidget(self.machine_profile_label)

        self.console_cb = FCCheckBox(_("Machine panel"))
        self.console_cb.setToolTip(
            _("Show the panel of the machine at the right: state, job buttons, jog, program and console."))
        self.title_box.addWidget(self.console_cb)

        self.fit_view_cb = FCCheckBox(_("Fit View"))
        self.fit_view_cb.setToolTip(_("Fit the GCode bounds in isometric view after loading the source object."))


        # #############################################################################################################
        # ################################ The CNCJob object to send ###################################################
        # #############################################################################################################
        self.object_combo = FCComboBox()
        self.object_combo.setModel(self.app.collection)
        self.object_combo.setRootModelIndex(self.app.collection.index(3, 0, QtCore.QModelIndex()))
        self.object_combo.is_last = True

        self.al_toolbar = FCDetachableTab(protect=True)
        self.al_toolbar.setTabsClosable(False)
        self.al_toolbar.useOldIndex(True)
        self.al_toolbar.set_detachable(val=False)
        self.tools_box.addWidget(self.al_toolbar)

        # GRBL Connect TAB
        self.gr_conn_tab = QtWidgets.QWidget()
        self.gr_conn_tab.setObjectName("connect_tab")
        self.gr_conn_tab_layout = QtWidgets.QVBoxLayout(self.gr_conn_tab)
        self.gr_conn_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.al_toolbar.addTab(self.gr_conn_tab, _("Connect"))

        # JOB TAB: the source object and the job controls; usable before the connection
        self.job_tab = QtWidgets.QWidget()
        self.job_tab.setObjectName("job_tab")
        self.job_tab_layout = QtWidgets.QVBoxLayout(self.job_tab)
        self.job_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.al_toolbar.addTab(self.job_tab, _("Job"))

        # PROBING TAB: edge probe, height map and probe points; needs a connection
        self.probing_tab = QtWidgets.QWidget()
        self.probing_tab.setObjectName("probing_tab")
        self.probing_tab_layout = QtWidgets.QVBoxLayout(self.probing_tab)
        self.probing_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.al_toolbar.addTab(self.probing_tab, _("Probing"))
        self.probing_scroll_area = VerticalScrollArea()
        self.probing_content = QtWidgets.QWidget()
        self.probing_box = QtWidgets.QVBoxLayout(self.probing_content)
        self.probing_box.setContentsMargins(0, 0, 0, 0)
        self.probing_scroll_area.setWidget(self.probing_content)
        self.probing_tab_layout.addWidget(self.probing_scroll_area)

        # CAMERA PAGE: mounted in the dock by the Sender (no parent here, like jog_page)
        self.camera_page = QtWidgets.QWidget()
        self.camera_page.setObjectName("camera_page")
        self.camera_page_layout = QtWidgets.QVBoxLayout(self.camera_page)
        self.camera_page_layout.setContentsMargins(2, 2, 2, 2)
        self.camera_tabs = CurrentPageTabWidget()
        # the camera view is never in the dock: it lives in the camera pane (SenderCameraPane); hidden until then
        self.camera_view = CameraView(self.camera_page)
        self.camera_view.hide()
        self.camera_widget = SenderCameraUI(self.app, self.camera_tabs, view=self.camera_view)
        self.camera_page_layout.addWidget(self.camera_widget.preview_controls)
        self.camera_page_layout.addWidget(self.camera_tabs, stretch=1)
        self.camera_tabs.addTab(self.camera_widget, _("Calibration"))
        self.registration_widget = SenderRegistrationUI(self.app, self.camera_tabs)
        self.camera_tabs.addTab(self.registration_widget, _("Board registration"))
        # the camera view moves into the camera pane beside the plot while the preview runs (SenderCameraPane)

        # TOOL CHANGE TAB
        self.toolchange_tab = QtWidgets.QWidget()
        self.toolchange_tab.setObjectName("toolchange_tab")
        self.toolchange_tab_layout = QtWidgets.QVBoxLayout(self.toolchange_tab)
        self.toolchange_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.al_toolbar.addTab(self.toolchange_tab, _("Tool change"))
        self.toolchange_scroll_area = VerticalScrollArea()
        self.toolchange_widget = SenderToolChangeUI(self.app, self.toolchange_tab)
        self.toolchange_scroll_area.setWidget(self.toolchange_widget)
        self.toolchange_tab_layout.addWidget(self.toolchange_scroll_area)

        # CONFIGURATION TAB: the parameter row, the report and the $$ table (the controller adds `configuration`)
        self.config_tab = QtWidgets.QWidget()
        self.config_tab.setObjectName("config_tab")
        self.config_tab_layout = QtWidgets.QVBoxLayout(self.config_tab)
        self.config_tab_layout.setContentsMargins(2, 2, 2, 2)
        self.al_toolbar.addTab(self.config_tab, _("Configuration"))

        self.al_toolbar.tabBar.setTabTextColor(self.al_toolbar.indexOf(self.gr_conn_tab), QtGui.QColor('red'))
        for tab in (self.probing_tab, self.toolchange_tab, self.config_tab):
            self.al_toolbar.tabBar.setTabEnabled(self.al_toolbar.indexOf(tab), False)
        # #############################################################################################################

        # #############################################################################################################
        # GRBL CONNECT
        # #############################################################################################################
        self.connect_frame = FCFrame()
        self.gr_conn_tab_layout.addWidget(self.connect_frame)

        grbl_conn_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1, 0])
        self.connect_frame.setLayout(grbl_conn_grid)

        self.controller_label = FCLabel('%s:' % _("Controller"))
        self.controller_combo = FCComboBox()
        for label, value in (('GRBL', 'grbl'), ('grblHAL', 'grblhal'), ('E-Cut', 'ecut')):
            self.controller_combo.addItem(label, value)
        self.connection_label = FCLabel('%s:' % _("Connection"))
        self.connection_combo = FCComboBox()
        for label, value in ((_('USB Serial'), 'serial'), (_('Native USB'), 'native'), (_('Network'), 'network')):
            self.connection_combo.addItem(label, value)
        self.connection_combo.setToolTip(_("Native USB uses the operating system's serial CDC port."))
        grbl_conn_grid.addWidget(self.controller_label, 0, 0)
        grbl_conn_grid.addWidget(self.controller_combo, 0, 1, 1, 2)
        grbl_conn_grid.addWidget(self.connection_label, 1, 0)
        grbl_conn_grid.addWidget(self.connection_combo, 1, 1, 1, 2)
        self.network_host_label = FCLabel('%s:' % _("Host/IP"))
        self.network_host_entry = FCEntry()
        self.network_host_entry.setToolTip(_("grblHAL raw TCP host/IP on a trusted LAN."))
        self.network_port_label = FCLabel('%s:' % _("Network port"))
        self.network_port_entry = FCSpinner()
        self.network_port_entry.set_range(1, 65535)
        self.network_port_entry.set_value(23)
        self.network_port_entry.setToolTip(_("The TCP port of the grblHAL network connection."))
        grbl_conn_grid.addWidget(self.network_host_label, 3, 0)
        grbl_conn_grid.addWidget(self.network_host_entry, 3, 1, 1, 2)
        grbl_conn_grid.addWidget(self.network_port_label, 5, 0)
        grbl_conn_grid.addWidget(self.network_port_entry, 5, 1)
        self.ecut_usb_label = FCLabel(_("Connect the E-Cut USB board directly by USB."))
        self.ecut_usb_label.setWordWrap(True)
        grbl_conn_grid.addWidget(self.ecut_usb_label, 7, 0, 1, 3)

        # COM list
        self.com_list_label = FCLabel('%s:' % _("COM list"))
        self.com_list_label.setToolTip(
            _("Lists the available serial ports.")
        )

        self.com_list_combo = FCComboBox()
        self.com_search_button = FCButton(_("Search"))
        self.com_search_button.setToolTip(
            _("Search for the available serial ports.")
        )
        grbl_conn_grid.addWidget(self.com_list_label, 2, 0)
        grbl_conn_grid.addWidget(self.com_list_combo, 2, 1)
        grbl_conn_grid.addWidget(self.com_search_button, 2, 2)

        # BAUDRATES list
        self.baudrates_list_label = FCLabel('%s:' % _("Baud rates"))
        self.baudrates_list_label.setToolTip(
            _("Lists the baud rates of the serial connection.")
        )

        self.baudrates_list_combo = FCComboBox()
        cb_model = QtCore.QStringListModel()
        self.baudrates_list_combo.setModel(cb_model)
        self.baudrates_list_combo.addItems(
            ['9600', '19200', '38400', '57600', '115200', '230400', '460800', '500000', '576000', '921600', '1000000',
             '1152000', '1500000', '2000000'])
        self.baudrates_list_combo.setCurrentText('115200')

        grbl_conn_grid.addWidget(self.baudrates_list_label, 4, 0)
        grbl_conn_grid.addWidget(self.baudrates_list_combo, 4, 1)

        # New baudrate
        self.new_bd_label = FCLabel('%s:' % _("New"))
        self.new_bd_label.setToolTip(
            _("New, custom baudrate.")
        )

        self.new_baudrate_entry = FCSpinner()
        self.new_baudrate_entry.set_range(40, 9999999)

        self.add_bd_button = FCButton(_("Add"))
        self.add_bd_button.setToolTip(
            _("Add the specified custom baudrate to the list.")
        )
        grbl_conn_grid.addWidget(self.new_bd_label, 6, 0)
        grbl_conn_grid.addWidget(self.new_baudrate_entry, 6, 1)
        grbl_conn_grid.addWidget(self.add_bd_button, 6, 2)

        self.del_bd_button = FCButton(_("Delete selected baudrate"))
        self.del_bd_button.setToolTip(_("Remove the selected baud rate from the list."))
        grbl_conn_grid.addWidget(self.del_bd_button, 8, 0, 1, 3)

        ctrl_h_lay = QtWidgets.QHBoxLayout()
        self.controller_reset_button = FCButton(_("Reset"))
        self.controller_reset_button.setToolTip(
            _("Abort the current operation and reset the controller immediately. Homing may be required.")
        )
        self.controller_reset_button.setDisabled(True)
        ctrl_h_lay.addWidget(self.controller_reset_button)

        self.com_connect_button = FCButton()
        self.com_connect_button.setText(_("Connect"))
        self.com_connect_button.setToolTip(
            _("Connect to the selected port with the selected baud rate.")
        )
        self.com_connect_button.setStyleSheet("QPushButton {background-color: red;}")
        ctrl_h_lay.addWidget(self.com_connect_button)

        # Web pendant: the switch, the address with the token and a Copy button
        self.pendant_cb = FCCheckBox(_("Web pendant"))
        self.pendant_cb.setToolTip(
            _("Serve a web page that shows the state of the machine and gives Hold, Resume, Stop and E-Stop.\n"
              "The address and the port are in the preferences. The token in the address is always needed."))
        self.pendant_url_entry = FCEntry()
        self.pendant_url_entry.setReadOnly(True)
        self.pendant_copy_button = FCButton(_("Copy"))
        self.pendant_copy_button.setToolTip(_("Copy the address of the pendant."))
        self.pendant_note_label = FCLabel(_("Open the address of this computer on the network."))
        self.pendant_note_label.setWordWrap(True)
        self.pendant_url_entry.setVisible(False)
        self.pendant_copy_button.setVisible(False)
        self.pendant_note_label.setVisible(False)
        grbl_conn_grid.addWidget(self.pendant_cb, 9, 0)
        grbl_conn_grid.addWidget(self.pendant_url_entry, 9, 1)
        grbl_conn_grid.addWidget(self.pendant_copy_button, 9, 2)
        grbl_conn_grid.addWidget(self.pendant_note_label, 10, 0, 1, 3)

        grbl_conn_grid.addWidget(FCLabel(""), 11, 0, 1, 3)
        grbl_conn_grid.setRowStretch(11, 1)
        grbl_conn_grid.addLayout(ctrl_h_lay, 12, 0, 1, 3)
        self.controller_combo.currentIndexChanged.connect(lambda _index: self.update_connection_controls())
        self.connection_combo.currentIndexChanged.connect(lambda _index: self.update_connection_controls())
        self.update_connection_controls()

        # #############################################################################################################
        # GRBL JOG
        # #############################################################################################################
        # The only jog controls; the Sender mounts it in the fixed jog block of the dock.
        self.jog_page = QtWidgets.QWidget()
        # fixed block of the dock: the pad at the left, the settings column at the right, only the height it needs
        self.jog_page.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
        self.jog_page_layout = QtWidgets.QVBoxLayout(self.jog_page)
        self.jog_page_layout.setContentsMargins(0, 0, 0, 0)
        # top: the pad and the settings column; bottom: the zero / home / spindle row
        jog_top_layout = QtWidgets.QHBoxLayout()
        jog_top_layout.setContentsMargins(0, 0, 0, 0)
        self.jog_page_layout.addLayout(jog_top_layout)

        self.ctrl_grbl_frame = FCFrame()
        jog_top_layout.addWidget(self.ctrl_grbl_frame)
        grbl_ctrl_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1, 0])
        self.ctrl_grbl_frame.setLayout(grbl_ctrl_grid)

        self.ctrl_grbl_frame2 = FCFrame()
        jog_top_layout.addWidget(self.ctrl_grbl_frame2)
        grbl_ctrl2_grid = GLay(v_spacing=5, h_spacing=3)
        self.ctrl_grbl_frame2.setLayout(grbl_ctrl2_grid)

        self.jog_wdg = FCJog(self.app)
        # one frame only: the ctrl_grbl_frame around it
        self.jog_wdg.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        grbl_ctrl_grid.addWidget(self.jog_wdg, 0, 0)

        # STOP
        self.jog_stop_button = FCButton(_("Stop"))
        self.jog_stop_button.setToolTip(_("Feed hold, then controller reset. The spindle stops."))
        self.jog_stop_button.setStyleSheet("QPushButton {background-color: red; color: white;}")
        self.jog_stop_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                           QtWidgets.QSizePolicy.Policy.Fixed)
        grbl_ctrl2_grid.addWidget(self.jog_stop_button, 4, 0, 1, 2)

        # the step presets (mm, from the preferences) sit above the step; the Sender labels and wires them
        self.jog_presets_wdg = QtWidgets.QWidget()
        presets_layout = QtWidgets.QHBoxLayout(self.jog_presets_wdg)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        presets_layout.setSpacing(3)
        self.jog_preset_buttons = []
        for _idx in range(4):
            preset_button = QtWidgets.QToolButton()
            preset_button.setToolTip(_("Set the jog step."))
            presets_layout.addWidget(preset_button)
            self.jog_preset_buttons.append(preset_button)
        grbl_ctrl2_grid.addWidget(self.jog_presets_wdg, 0, 0, 1, 2)

        # JOG Step
        self.jog_step_label = FCLabel('%s:' % _("Step"))
        self.jog_step_label.setToolTip(
            _("Each jog action will move the axes with this value.")
        )

        self.jog_step_entry = FCSliderWithDoubleSpinner()
        self.jog_step_entry.set_precision(self.decimals)
        self.jog_step_entry.slider.set_precision(self.decimals)  # else the slider rounds the value to 3 decimals
        self.jog_step_entry.setSingleStep(0.1)
        # the smallest value is the smallest step of the precision; the largest depends on the units
        smallest = 10 ** -self.decimals
        metric = self.app.app_units.upper() == 'MM'
        self.jog_step_entry.set_range(smallest, 500 if metric else 20)

        grbl_ctrl2_grid.addWidget(self.jog_step_label, 1, 0)
        grbl_ctrl2_grid.addWidget(self.jog_step_entry, 1, 1)

        # JOG Feedrate
        self.jog_fr_label = FCLabel('%s:' % _("Feedrate"))
        self.jog_fr_label.setToolTip(
            _("Feedrate when jogging.")
        )

        self.jog_fr_entry = FCSliderWithDoubleSpinner()
        self.jog_fr_entry.set_precision(self.decimals)
        self.jog_fr_entry.slider.set_precision(self.decimals)
        self.jog_fr_entry.setSingleStep(10)
        self.jog_fr_entry.set_range(smallest, 10000 if metric else 400)

        grbl_ctrl2_grid.addWidget(self.jog_fr_label, 2, 0)
        grbl_ctrl2_grid.addWidget(self.jog_fr_entry, 2, 1)

        self.continuous_jog_cb = FCCheckBox(_("Continuous"))
        self.continuous_jog_cb.setToolTip(
            _("Hold a jog arrow to move. Release it to brake to a stop.\n"
              "A controller with native jog support (E-Cut) is required.")
        )
        self.continuous_jog_cb.setChecked(False)
        self.continuous_jog_cb.setEnabled(False)
        grbl_ctrl2_grid.addWidget(self.continuous_jog_cb, 3, 0, 1, 2)

        self.ecut_motion_button = FCButton(_("E-Cut settings"))
        self.ecut_motion_button.setToolTip(_("Motion, motor calibration, spindle and input/output settings."))
        self.ecut_motion_button.hide()
        grbl_ctrl2_grid.addWidget(self.ecut_motion_button, 5, 0, 1, 2)

        # the rotary A axis (degrees, degrees/min): shown only for an XYZA controller
        self.jog_a_presets_wdg = QtWidgets.QWidget()
        a_presets_layout = QtWidgets.QHBoxLayout(self.jog_a_presets_wdg)
        a_presets_layout.setContentsMargins(0, 0, 0, 0)
        a_presets_layout.setSpacing(3)
        self.jog_a_preset_buttons = []
        for _idx in range(4):
            preset_button = QtWidgets.QToolButton()
            preset_button.setToolTip(_("Set the A jog step."))
            a_presets_layout.addWidget(preset_button)
            self.jog_a_preset_buttons.append(preset_button)
        grbl_ctrl2_grid.addWidget(self.jog_a_presets_wdg, 6, 0, 1, 2)

        self.jog_a_step_label = FCLabel('%s:' % _("A step"))
        self.jog_a_step_label.setToolTip(_("Each A jog action will turn the A axis by this value, in degrees."))
        self.jog_a_step_entry = FCSliderWithDoubleSpinner()
        self.jog_a_step_entry.set_precision(self.decimals)
        self.jog_a_step_entry.slider.set_precision(self.decimals)
        self.jog_a_step_entry.setSingleStep(1)
        self.jog_a_step_entry.set_range(0.001, 360)
        grbl_ctrl2_grid.addWidget(self.jog_a_step_label, 7, 0)
        grbl_ctrl2_grid.addWidget(self.jog_a_step_entry, 7, 1)

        self.jog_a_fr_label = FCLabel('%s:' % _("A feedrate"))
        self.jog_a_fr_label.setToolTip(_("Feedrate when jogging the A axis, in degrees per minute."))
        self.jog_a_fr_entry = FCSliderWithDoubleSpinner()
        self.jog_a_fr_entry.set_precision(self.decimals)
        self.jog_a_fr_entry.slider.set_precision(self.decimals)
        self.jog_a_fr_entry.setSingleStep(10)
        self.jog_a_fr_entry.set_range(1, 10000)
        grbl_ctrl2_grid.addWidget(self.jog_a_fr_label, 8, 0)
        grbl_ctrl2_grid.addWidget(self.jog_a_fr_entry, 8, 1)
        self.a_axis_widgets = (self.jog_a_presets_wdg, self.jog_a_step_label, self.jog_a_step_entry,
                               self.jog_a_fr_label, self.jog_a_fr_entry)
        for widget in self.a_axis_widgets:
            widget.hide()

        # #############################################################################################################
        # GRBL CONTROL
        # #############################################################################################################
        # the zero / home buttons and the spindle controls form the bottom row of the jog block (built below)
        self.zero_axs_wdg = FCZeroAxes(self.app)
        self.zero_axs_wdg.setStyleSheet("""
                            FCZeroAxes
                            {
                                border: 1px solid lightgray;
                                border-radius: 5px
                            }
                            """)

        # EDGE / CENTRE PROBING
        self.probing_box.addWidget(FCLabel(_("Edge probe"), bold=True))
        self.edge_probe_frame = FCFrame()
        self.probing_box.addWidget(self.edge_probe_frame)
        edge_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1])
        self.edge_probe_frame.setLayout(edge_grid)

        edge_grid.addWidget(FCLabel('%s:' % _("Direction")), 1, 0)
        self.edge_probe_axis_radio = RadioSet([
            {'label': 'X+', 'value': 'xplus'},
            {'label': 'X-', 'value': 'xminus'},
            {'label': 'Y+', 'value': 'yplus'},
            {'label': 'Y-', 'value': 'yminus'},
        ])
        self.edge_probe_axis_radio.setToolTip(_("The direction of the edge probe."))
        self.edge_probe_axis_radio.set_value('xplus')
        edge_grid.addWidget(self.edge_probe_axis_radio, 1, 1)

        edge_grid.addWidget(FCLabel('%s:' % _("Probe distance")), 2, 0)
        self.edge_probe_distance_entry = FCDoubleSpinner()
        self.edge_probe_distance_entry.set_precision(self.decimals)
        self.edge_probe_distance_entry.set_range(0.0001, 10000.0)
        self.edge_probe_distance_entry.set_value(5.0 if self.app.app_units.upper() == 'MM' else 0.2)
        self.edge_probe_distance_entry.setToolTip(_(
            "The maximum travel of a directional probe, in current units."))
        edge_grid.addWidget(self.edge_probe_distance_entry, 2, 1)

        edge_grid.addWidget(FCLabel('%s:' % _("Probe feedrate")), 3, 0)
        self.edge_probe_feed_entry = FCDoubleSpinner()
        self.edge_probe_feed_entry.set_precision(self.decimals)
        self.edge_probe_feed_entry.set_range(0.0001, 910000.0)
        self.edge_probe_feed_entry.set_value(100.0 if self.app.app_units.upper() == 'MM' else 4.0)
        self.edge_probe_feed_entry.setToolTip(_("The feedrate of the probe move, in current units per minute."))
        edge_grid.addWidget(self.edge_probe_feed_entry, 3, 1)

        edge_grid.addWidget(FCLabel('%s:' % _("Pull-off")), 4, 0)
        self.edge_probe_pulloff_entry = FCDoubleSpinner()
        self.edge_probe_pulloff_entry.set_precision(self.decimals)
        self.edge_probe_pulloff_entry.set_range(0.0001, 10000.0)
        self.edge_probe_pulloff_entry.set_value(1.0 if self.app.app_units.upper() == 'MM' else 0.04)
        self.edge_probe_pulloff_entry.setToolTip(_(
            "The distance to move away from a contact, in current units."))
        edge_grid.addWidget(self.edge_probe_pulloff_entry, 4, 1)

        edge_grid.addWidget(FCLabel('%s:' % _("Probe Z travel")), 5, 0)
        self.edge_probe_travelz_entry = FCDoubleSpinner()
        self.edge_probe_travelz_entry.set_precision(self.decimals)
        self.edge_probe_travelz_entry.set_range(0.0001, 10000.0)
        self.edge_probe_travelz_entry.set_value(2.0 if self.app.app_units.upper() == 'MM' else 0.08)
        self.edge_probe_travelz_entry.setToolTip(_(
            "The safe Z used while moving between centre contacts, in current units."))
        edge_grid.addWidget(self.edge_probe_travelz_entry, 5, 1)

        edge_buttons = QtWidgets.QHBoxLayout()
        self.edge_probe_button = FCButton(_("Probe edge"))
        self.edge_probe_button.setToolTip(_(
            "Probe one bounded contact in the selected direction.\n"
            "The contact is reported in the active work coordinates."))
        edge_buttons.addWidget(self.edge_probe_button)
        self.edge_center_button = FCButton(_("Find XY centre"))
        self.edge_center_button.setToolTip(_(
            "Probe the 2 opposed X contacts and the 2 opposed Y contacts,\n"
            "then report their midpoint as the hole or ring centre."))
        edge_buttons.addWidget(self.edge_center_button)
        edge_grid.addLayout(edge_buttons, 6, 0, 1, 2)

        self.edge_probe_result_label = FCLabel('')
        self.edge_probe_result_label.setWordWrap(True)
        edge_grid.addWidget(self.edge_probe_result_label, 7, 0, 1, 2)

        edge_grid.addWidget(FCLabel('%s:' % _("Tool diameter")), 8, 0)
        self.edge_tool_dia_entry = FCDoubleSpinner()
        self.edge_tool_dia_entry.set_precision(self.decimals)
        self.edge_tool_dia_entry.set_range(0.0, 50.0)
        self.edge_tool_dia_entry.set_value(0.0)
        self.edge_tool_dia_entry.setToolTip(_(
            "The diameter of the probing tool. An edge contact is moved by half of it."))
        edge_grid.addWidget(self.edge_tool_dia_entry, 8, 1)

        self.edge_set_zero_button = FCButton(_("Set zero from result"))
        self.edge_set_zero_button.setToolTip(_(
            "Make the probed edge, or the centre, the work zero of its axes."))
        self.edge_set_zero_button.setDisabled(True)
        edge_grid.addWidget(self.edge_set_zero_button, 9, 0, 1, 2)

        # the height map group: its title and the buttons hide together in the camera mode
        self.heightmap_frame = QtWidgets.QWidget()
        heightmap_box = QtWidgets.QVBoxLayout(self.heightmap_frame)
        heightmap_box.setContentsMargins(0, 0, 0, 0)
        heightmap_box.addWidget(FCLabel(_("Height map"), bold=True))
        self.probing_box.addWidget(self.heightmap_frame)

        # #############################################################################################################
        # GRBL SENDER
        # #############################################################################################################
        self.sender_frame = FCFrame()
        self.config_tab_layout.addWidget(self.sender_frame)

        grbl_send_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[1, 0])
        self.sender_frame.setLayout(grbl_send_grid)

        # the one command entry: made without a parent, the Sender mounts it under the log of the dock Console page (like jog_page)
        self.command_row = QtWidgets.QWidget()
        command_lay = QtWidgets.QHBoxLayout(self.command_row)
        command_lay.setContentsMargins(0, 0, 0, 0)
        self.grbl_command_entry = FCEntry()
        self.grbl_command_entry.setPlaceholderText(_("Type a command ..."))
        self.grbl_command_entry.setToolTip(_("The command to send. Press Enter or Send."))

        self.grbl_send_button = QtWidgets.QToolButton()
        self.grbl_send_button.setText(_("Send"))
        self.grbl_send_button.setToolTip(
            _("Send a custom command to the controller.")
        )
        command_lay.addWidget(self.grbl_command_entry, 1)
        command_lay.addWidget(self.grbl_send_button)

        # Get Parameter
        self.grbl_get_param_label = FCLabel('%s:' % _("Get Config parameter"))
        self.grbl_get_param_label.setToolTip(
            _("A GRBL configuration parameter.")
        )
        grbl_send_grid.addWidget(self.grbl_get_param_label, 0, 0, 1, 2)

        self.grbl_parameter_entry = FCEntry()
        self.grbl_parameter_entry.setPlaceholderText(_("Type GRBL parameter ..."))

        self.grbl_get_param_button = QtWidgets.QToolButton()
        self.grbl_get_param_button.setText(_("Get"))
        self.grbl_get_param_button.setToolTip(
            _("Get the value of a specified GRBL parameter.")
        )
        grbl_send_grid.addWidget(self.grbl_parameter_entry, 1, 0)
        grbl_send_grid.addWidget(self.grbl_get_param_button, 1, 1)

        # GET Report
        self.grbl_report_button = FCButton(_("Get Report"))
        self.grbl_report_button.setToolTip(
            _("Print the controller report in the Console page of the machine panel.")
        )
        grbl_send_grid.addWidget(self.grbl_report_button, 2, 0, 1, 2)

        hm_lay = QtWidgets.QHBoxLayout()
        heightmap_box.addLayout(hm_lay)
        # GET HEIGHT MAP
        self.grbl_get_heightmap_button = FCButton(_("Probe height map"))
        self.grbl_get_heightmap_button.setToolTip(
            _("Probe the points and make the height map.")
        )
        hm_lay.addWidget(self.grbl_get_heightmap_button, stretch=1)

        # STOP
        self.stop_probing_button = FCButton(_("Stop"))
        self.stop_probing_button.setToolTip(_("Feed hold, then controller reset. The spindle stops."))
        self.stop_probing_button.setStyleSheet("QPushButton {background-color: red; color: white;}")
        # its own row, over the full width of the panel
        self.stop_probing_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                               QtWidgets.QSizePolicy.Policy.Fixed)
        heightmap_box.addWidget(self.stop_probing_button)

        self.grbl_save_height_map_button = QtWidgets.QToolButton()
        self.grbl_save_height_map_button.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.grbl_save_height_map_button.setToolTip(
            _("Will save the GRBL height map.")
        )
        hm_lay.addWidget(self.grbl_save_height_map_button, stretch=0, alignment=Qt.AlignmentFlag.AlignRight)

        self.start_button = FCButton(_("Start"), bold=True)
        self.start_button.setToolTip(_("Stream the G-code of the selected CNCJob to the controller."))
        self.hold_button = FCButton(_("Hold"))
        self.hold_button.setToolTip(_("Feed hold. Resume continues the job."))
        self.resume_button = FCButton(_("Resume"))
        self.resume_button.setToolTip(_("Resume the job after a feed hold."))
        self.stop_button = FCButton(_("Stop"))
        self.stop_button.setStyleSheet("QPushButton {background-color: red; color: white;}")
        self.stop_button.setToolTip(_("Feed hold, then controller reset. The spindle stops."))

        # the header of the dock: made without a parent, the Sender mounts it in the dock (like jog_page)
        self.operate_header = QtWidgets.QWidget()
        self.operate_header.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                          QtWidgets.QSizePolicy.Policy.Maximum)
        header_box = QtWidgets.QVBoxLayout(self.operate_header)
        header_box.setContentsMargins(0, 0, 0, 0)
        self.status_strip = SenderStatusStrip()
        header_box.addWidget(self.status_strip)
        self.recovery_button = self.status_strip.recovery_button

        self.job_row = QtWidgets.QWidget()
        job_lay = QtWidgets.QHBoxLayout(self.job_row)
        job_lay.setContentsMargins(0, 0, 0, 0)
        for button in (self.start_button, self.hold_button, self.resume_button, self.stop_button):
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            job_lay.addWidget(button, 1)
        self.check_badge = FCLabel(_("CHECK"))
        self.check_badge.setStyleSheet("QLabel { color: orange; font-weight: bold; }")
        self.check_badge.setToolTip(_("Check mode: Start validates the G-code without motion."))
        self.check_badge.setVisible(False)
        job_lay.addWidget(self.check_badge)
        self.step_button = FCButton(_("Next block"))
        self.step_button.setToolTip(_("Single block: write the next line of the G-code."))
        self.step_button.setVisible(False)
        job_lay.addWidget(self.step_button)
        self.step_badge = FCLabel(_("STEP"))
        self.step_badge.setStyleSheet("QLabel { color: orange; font-weight: bold; }")
        self.step_badge.setToolTip(_("Single block: each line waits for Next block."))
        self.step_badge.setVisible(False)
        job_lay.addWidget(self.step_badge)
        header_box.addWidget(self.job_row)

        self.unlock_button = FCButton(_("Unlock"))
        self.unlock_button.setToolTip(_("Send $X: clear the alarm lock."))
        self.sim_button = FCButton(_("Sim"))
        self.sim_button.setToolTip(_("Run the program with a virtual controller."))
        self.check_mode_cb = FCCheckBox(_("Check mode"))
        self.check_mode_cb.setToolTip(_("While checked, every Start validates G-code without motion.\n"
                                       "The controller exits check mode after each job; this request stays checked.\n"
                                       "Uncheck it explicitly before cutting."))
        self.optional_stop_cb = FCCheckBox(_("Optional stop (M1)"))
        self.optional_stop_cb.setToolTip(_("Checked: an M1 in the G-code stops the job like M0.\nUnchecked: M1 is ignored."))
        self.block_delete_cb = FCCheckBox(_("Block delete (/)"))
        self.block_delete_cb.setToolTip(_("Checked: the lines that start with '/' are skipped.\n"
                                          "Unchecked: they are sent without the '/'."))
        self.block_delete_cb.setChecked(True)
        self.single_block_cb = FCCheckBox(_("Single block"))
        self.single_block_cb.setToolTip(_("Checked: each line of the job waits for Next block.\n"
                                          "Not available for an E-Cut program."))
        # Unlock sits at the right end of the alarm row; Alarm always sets an alarm text, so the row is shown
        self.status_strip.alarm_row.addWidget(self.unlock_button)

        # the button shows the spindle state: it also changes when a command or a job starts or stops the spindle
        self.spindle_button = FCButton(_("Spindle"))
        self.spindle_button.setCheckable(True)
        self.spindle_button.setToolTip(_("Start the spindle clockwise (M3) with the RPM value, or stop it (M5).\n"
                                         "ON/OFF follows the controller-reported state, not measured rotation."))
        self.spindle_button.setStyleSheet("QPushButton:checked {background-color: green; color: white;}")
        self.spindle_rpm_entry = FCSpinner()
        self.spindle_rpm_entry.set_range(0, 100000)
        self.spindle_rpm_entry.set_step(1000)
        self.spindle_rpm_entry.set_value(10000)
        # no suffix: FCSpinner reads only the number from the text, so a ' RPM' suffix gives 0
        self.spindle_rpm_entry.setToolTip(_("The spindle speed for the Spindle button."))
        # bottom row of the jog block: zero X/Y/Z/All + Home, then the spindle button with its RPM
        zero_spindle_row = QtWidgets.QHBoxLayout()
        zero_spindle_row.setContentsMargins(0, 0, 0, 0)
        zero_spindle_row.addWidget(self.zero_axs_wdg)
        self.wcs_combo = FCComboBox()
        self.wcs_combo.addItems(['G54', 'G55', 'G56', 'G57', 'G58', 'G59'])
        self.wcs_combo.setToolTip(_("The active work coordinate system."))
        zero_spindle_row.addWidget(self.wcs_combo)
        zero_spindle_row.addStretch(1)
        zero_spindle_row.addWidget(self.spindle_button)
        zero_spindle_row.addWidget(self.spindle_rpm_entry)
        self.spindle_rpm_label = FCLabel(_("RPM"))
        zero_spindle_row.addWidget(self.spindle_rpm_label)
        self.jog_page_layout.addLayout(zero_spindle_row)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        header_box.addWidget(self.progress_bar)
        self.time_label = FCLabel("")
        header_box.addWidget(self.time_label)
        self.status_label = FCLabel(_("Idle"))
        self.status_label.setWordWrap(True)
        header_box.addWidget(self.status_label)

        # overrides: name, real-time byte
        # a page of the dock: made without a parent, the Sender mounts it in the dock (like jog_page)
        self.overrides_page = QtWidgets.QWidget()
        ov_grid = QtWidgets.QGridLayout(self.overrides_page)
        ov_grid.setContentsMargins(0, 0, 0, 0)
        self.ov_label = FCLabel('%s' % _("Overrides"), bold=True)
        ov_grid.addWidget(self.ov_label, 0, 0, 1, 2)
        self.ov_buttons = {}
        ov_rows = [
            (_("Feed"), [('-10', b'\x92'), ('-1', b'\x94'), ('100%', b'\x90'), ('+1', b'\x93'), ('+10', b'\x91')]),
            (_("Rapid"), [('25%', b'\x97'), ('50%', b'\x96'), ('100%', b'\x95')]),
            (_("Spindle"), [('-10', b'\x9b'), ('-1', b'\x9d'), ('100%', b'\x99'), ('+1', b'\x9c'), ('+10', b'\x9a')]),
        ]
        for row_idx, (name, items) in enumerate(ov_rows, start=1):
            ov_grid.addWidget(FCLabel('%s:' % name), row_idx, 0)
            h_lay = QtWidgets.QHBoxLayout()
            for text, byte in items:
                btn = QtWidgets.QToolButton()
                btn.setText(text)
                h_lay.addWidget(btn)
                self.ov_buttons[(name, text)] = (btn, byte)
            ov_grid.addLayout(h_lay, row_idx, 1)
        self.ov_values_label = FCLabel(_('F: %d%%  R: %d%%  S: %d%%') % (100, 100, 100))
        ov_grid.addWidget(self.ov_values_label, 4, 0, 1, 2)
        ov_grid.setRowStretch(5, 1)

        self.save_levelled_button = FCButton(_("Save levelled G-code"))
        self.save_levelled_button.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.save_levelled_button.setToolTip(_("Save the G-code that the sender streams, after the levelling."))

        self.view3d_button = FCButton(_("Show in Plot 3D"))
        self.view3d_button.setToolTip(_("Show the toolpath, the tool and the height map in the Plot 3D tab."))

        # the probe points group: its title, a hint while Autolevelling is off, the points and the precision probe
        self.probe_points_frame = QtWidgets.QWidget()
        autolevel_box = QtWidgets.QVBoxLayout(self.probe_points_frame)
        autolevel_box.setContentsMargins(0, 0, 0, 0)
        autolevel_box.addWidget(FCLabel(_("Probe points"), bold=True))
        self.al_hint_label = FCLabel(_("Enable Autolevelling in the Job page to set the probe points."))
        self.al_hint_label.setWordWrap(True)
        autolevel_box.addWidget(self.al_hint_label)
        self.probing_box.addWidget(self.probe_points_frame)
        self.probing_box.addStretch(1)
        self.autolevel_cb = FCCheckBox(_("Level the G-code"))
        self.autolevel_cb.setToolTip(_("Apply the height map to the G-code before streaming."))
        build_probe_points_ui(self, self.app, autolevel_box)
        self.tool_grid.removeWidget(self.plot_probing_pts_cb)
        self.tool_grid.addWidget(self.plot_probing_pts_cb, 2, 0)
        self.plot_height_map_cb = FCCheckBox(_("Plot height map"))
        self.plot_height_map_cb.setToolTip(_(
            "Show or hide the generated height-map surface in Plot 3D.\n"
            "This also applies to simulated height maps.\n"
            "Hiding the surface doesn't affect levelling."))
        self.plot_height_map_cb.setChecked(True)
        self.tool_grid.addWidget(self.plot_height_map_cb, 2, 1)
        self.avoid_exc_label = FCLabel('%s:' % _("Excellon Object"))
        self.avoid_exc_label.setToolTip(_("Avoid holes from this Excellon object when adding probe points."))
        self.avoid_exc_combo = FCComboBox()
        self.avoid_exc_combo.setModel(self.app.collection)
        self.avoid_exc_combo.setRootModelIndex(self.app.collection.index(1, 0, QtCore.QModelIndex()))
        self.avoid_exc_combo.setCurrentIndex(0)
        self.avoid_exc_combo.obj_type = "Excellon"
        self.tool_grid.addWidget(self.avoid_exc_holes_size_label, 8, 0)
        self.tool_grid.addWidget(self.avoid_exc_holes_size_entry, 8, 1)
        self.tool_grid.addWidget(self.avoid_exc_label, 5, 0, 1, 2)
        self.tool_grid.addWidget(self.avoid_exc_combo, 6, 0, 1, 2)
        self.avoid_exc_label.hide()
        self.avoid_exc_combo.hide()
        self.al_frame.setVisible(self.autolevel_cb.isChecked())
        self.autolevel_cb.toggled.connect(self.al_frame.setVisible)
        self.al_hint_label.setVisible(not self.autolevel_cb.isChecked())
        self.autolevel_cb.toggled.connect(lambda checked: self.al_hint_label.setVisible(not checked))

        self.precision_probe_cb = FCCheckBox(_("Precision probing (two touches)"))
        self.precision_probe_cb.setToolTip(_(
            "GRBL only: retract after the coarse touch and probe again at the fine feed. "
            "Only the second measurement is used."))
        self.precision_probe_cb.set_value(False)
        self.al_box.addWidget(self.precision_probe_cb)
        precision_grid = GLay(v_spacing=5, h_spacing=3)
        self.al_box.addLayout(precision_grid)
        precision_grid.addWidget(FCLabel('%s:' % _("Fine probe feedrate")), 0, 0)
        self.precision_feed_entry = FCDoubleSpinner()
        self.precision_feed_entry.set_precision(max(self.decimals, 4))
        self.precision_feed_entry.set_range(0.0001, 910000.0)
        self.precision_feed_entry.set_value(10.0 if app.app_units.upper() == 'MM' else 10.0 / 25.4)
        self.precision_feed_entry.setToolTip(_("The feedrate of the second, fine touch, in current units per minute."))
        self.precision_feed_entry.setEnabled(False)
        precision_grid.addWidget(self.precision_feed_entry, 0, 1)
        precision_grid.addWidget(FCLabel('%s:' % _("Precision retract")), 1, 0)
        self.precision_retract_entry = FCDoubleSpinner()
        self.precision_retract_entry.set_precision(max(self.decimals, 4))
        self.precision_retract_entry.set_range(0.0001, 910000.0)
        self.precision_retract_entry.setToolTip(_(
            "Distance above the coarse contact, in current units. "
            "Must release the probe without exceeding Probe Z travel."))
        self.precision_retract_entry.set_value(0.5 if app.app_units.upper() == 'MM' else 0.5 / 25.4)
        self.precision_retract_entry.setEnabled(False)
        precision_grid.addWidget(self.precision_retract_entry, 1, 1)

        # the Job page, top to bottom
        self.obj_combo_title = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.obj_combo_title.setToolTip(_("The CNCJob object to send."))
        self.job_tab_layout.addWidget(self.obj_combo_title)
        self.job_tab_layout.addWidget(self.object_combo)
        self.job_tab_layout.addWidget(self.fit_view_cb)
        self.job_tab_layout.addWidget(self.check_mode_cb)
        self.job_tab_layout.addWidget(self.autolevel_cb)
        self.job_tab_layout.addWidget(self.optional_stop_cb)
        self.job_tab_layout.addWidget(self.block_delete_cb)
        self.job_tab_layout.addWidget(self.single_block_cb)
        sim_row = QtWidgets.QHBoxLayout()
        sim_row.addWidget(self.sim_button)
        sim_row.addWidget(self.view3d_button)
        self.job_tab_layout.addLayout(sim_row)
        self.job_tab_layout.addWidget(self.save_levelled_button)
        self.job_tab_layout.addStretch(1)

        self.tools_box.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.tools_box.addWidget(self.reset_button)

        GLay.set_common_column_size([self.tool_grid, self.param_grid], 0)

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
