# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtGui
from appGUI.GUIElements import FCLabel, FCButton, FCFrame, GLay, NumericalEvalEntry, RadioSet, \
    FCDoubleSpinner, FCSpinner, FCComboBox

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

class CalcUI:

    pluginName = _("Calculators")
    v_shapeName = _("V-Shape Tool")
    unitsName = _("Units Conversion")
    eplateName = _("ElectroPlating")
    tinningName = _("Tinning")
    feedsName = _("Feeds & Speeds")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        self.layout.addWidget(title_label)

        # #####################
        # ## Units Calculator #
        # #####################

        # ## Title of the Units Calculator
        units_label = FCLabel('%s' % self.unitsName, color='blue', bold=True)

        self.layout.addWidget(units_label)

        units_frame = FCFrame()
        self.layout.addWidget(units_frame)

        # #############################################################################################################
        # Units Calculators
        # #############################################################################################################
        # Grid Layout
        grid_units_layout = GLay(v_spacing=5, h_spacing=3)
        units_frame.setLayout(grid_units_layout)

        # Length conversion
        inch_label = FCLabel(_("inch"))
        mm_label = FCLabel(_("mm"))
        grid_units_layout.addWidget(mm_label, 0, 0)
        grid_units_layout.addWidget(inch_label, 0, 1)

        self.inch_entry = NumericalEvalEntry(border_color='#0069A9')
        self.inch_entry.setToolTip(_("Here you enter the value to be converted from imperial to metric"))

        self.mm_entry = NumericalEvalEntry(border_color='#0069A9')
        self.mm_entry.setToolTip(_("Here you enter the value to be converted from metric to imperial"))

        grid_units_layout.addWidget(self.mm_entry, 2, 0)
        grid_units_layout.addWidget(self.inch_entry, 2, 1)

        # Weight conversion
        oz_label = FCLabel(_("oz"))
        gram_label = FCLabel(_("gram"))
        grid_units_layout.addWidget(gram_label, 4, 0)
        grid_units_layout.addWidget(oz_label, 4, 1)

        self.oz_entry = NumericalEvalEntry(border_color='#0069A9')
        self.oz_entry.setToolTip(_("Here you enter the value to be converted from imperial to metric"))

        self.g_entry = NumericalEvalEntry(border_color='#0069A9')
        self.g_entry.setToolTip(_("Here you enter the value to be converted from metric to imperial"))

        grid_units_layout.addWidget(self.g_entry, 6, 0)
        grid_units_layout.addWidget(self.oz_entry, 6, 1)

        # Liquid volume conversion
        fl_oz_label = FCLabel(_("fl oz"))
        ml_label = FCLabel(_("mL"))
        grid_units_layout.addWidget(ml_label, 8, 0)
        grid_units_layout.addWidget(fl_oz_label, 8, 1)

        self.fl_oz_entry = NumericalEvalEntry(border_color='#0069A9')
        self.fl_oz_entry.setToolTip(_("Here you enter the value to be converted from imperial to metric"))

        self.ml_entry = NumericalEvalEntry(border_color='#0069A9')
        self.ml_entry.setToolTip(_("Here you enter the value to be converted from metric to imperial"))

        grid_units_layout.addWidget(self.ml_entry, 10, 0)
        grid_units_layout.addWidget(self.fl_oz_entry, 10, 1)

        # #############################################################################################################
        # ################################ V-shape Tool Calculator ####################################################
        # #############################################################################################################
        # ## Title of the V-shape Tools Calculator
        v_shape_title_label = FCLabel('%s' % self.v_shapeName, color='green', bold=True)

        self.layout.addWidget(v_shape_title_label)

        v_frame = FCFrame()
        self.layout.addWidget(v_frame)

        grid_vshape = GLay(v_spacing=5, h_spacing=3)
        v_frame.setLayout(grid_vshape)

        # self.v_shape_spacer_label = FCLabel(" ")
        # grid_vshape.addWidget(self.v_shape_spacer_label, 0, 0, 1, 2)

        # Tip Diameter
        self.tipDia_label = FCLabel('%s:' % _("Tip Diameter"))
        self.tipDia_label.setToolTip(
            _("This is the tool tip diameter.\n"
              "It is specified by manufacturer.")
        )

        self.tipDia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tipDia_entry.set_precision(self.decimals)
        self.tipDia_entry.set_range(0.0, 10000.0000)
        self.tipDia_entry.setSingleStep(0.1)
        self.tipDia_entry.setKeyboardTracking(False)

        grid_vshape.addWidget(self.tipDia_label, 4, 0)
        grid_vshape.addWidget(self.tipDia_entry, 4, 1)

        # Tip Angle
        self.tipAngle_label = FCLabel('%s:' % _("Tip Angle"))
        self.tipAngle_label.setToolTip(_("This is the angle of the tip of the tool.\n"
                                         "It is specified by manufacturer."))

        self.tipAngle_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.tipAngle_entry.set_precision(self.decimals)
        self.tipAngle_entry.set_range(0.0, 180.0)
        self.tipAngle_entry.setSingleStep(5)
        self.tipAngle_entry.setKeyboardTracking(False)

        grid_vshape.addWidget(self.tipAngle_label, 6, 0)
        grid_vshape.addWidget(self.tipAngle_entry, 6, 1)

        # Cut Z
        self.cutDepth_label = FCLabel('%s:' % _("Cut Z"))
        self.cutDepth_label.setToolTip(_("This is the depth to cut into the material."))

        self.cutDepth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cutDepth_entry.set_range(-10000.0000, 10000.0000)
        self.cutDepth_entry.set_precision(self.decimals)
        self.cutDepth_entry.setKeyboardTracking(False)

        grid_vshape.addWidget(self.cutDepth_label, 8, 0)
        grid_vshape.addWidget(self.cutDepth_entry, 8, 1)

        # Tool Diameter
        self.effectiveToolDia_label = FCLabel('%s:' % _("Tool Diameter"))
        self.effectiveToolDia_label.setToolTip(_("This is the actual tool diameter\n"
                                                 "at the desired depth of cut."))
        self.effectiveToolDia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.effectiveToolDia_entry.set_precision(self.decimals)
        self.effectiveToolDia_entry.set_range(0.0, 10000.0000)
        self.effectiveToolDia_entry.setKeyboardTracking(False)

        grid_vshape.addWidget(self.effectiveToolDia_label, 10, 0)
        grid_vshape.addWidget(self.effectiveToolDia_entry, 10, 1)

        # ## Buttons
        self.calculate_vshape_button = FCButton(_("Calculate"))
        self.calculate_vshape_button.setIcon(QtGui.QIcon(self.app.resource_location + '/calculator32.png'))

        self.calculate_vshape_button.setToolTip(
            _("Calculate either the depth of cut or the effective tool diameter.")
        )

        grid_vshape.addWidget(self.calculate_vshape_button, 12, 0, 1, 2)

        # #############################################################################################################
        # Feeds and Speeds Calculator
        # #############################################################################################################
        feeds_label = FCLabel(self.feedsName, color='indigo', bold=True)
        feeds_label.setToolTip(_("Calculate feed rate, chip load, spindle speed and surface speed.\n"
                                "Choose cutting parameters for your tool, material and machine."))
        self.layout.addWidget(feeds_label)

        feeds_frame = FCFrame()
        self.layout.addWidget(feeds_frame)
        grid_feeds = GLay(v_spacing=5, h_spacing=3)
        feeds_frame.setLayout(grid_feeds)

        self.feed_units_radio = RadioSet([
            {'label': _("Metric"), 'value': 'MM'},
            {'label': _("Imperial"), 'value': 'IN'}
        ], compact=True)
        self.feed_units_radio.setToolTip(_("Change units and convert the values in this calculator."))
        grid_feeds.addWidget(FCLabel('%s:' % _("Units")), 0, 0)
        grid_feeds.addWidget(self.feed_units_radio, 0, 1)

        self.feed_target_combo = FCComboBox()
        self.feed_target_combo.addItems([
            _("Feed Rate"), _("Chip Load"), _("RPM from Feed"),
            _("Surface Speed"), _("RPM from Surface Speed")
        ])
        self.feed_target_combo.setToolTip(_("Choose the unknown quantity. Its field becomes read-only.\n"
                                           "Surface speed is also updated from diameter and RPM."))
        grid_feeds.addWidget(FCLabel('%s:' % _("Calculate for")), 2, 0)
        grid_feeds.addWidget(self.feed_target_combo, 2, 1)

        self.feed_diameter_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feed_flutes_entry = FCSpinner()
        self.feed_flutes_entry.set_range(1, 1000)
        self.feed_flutes_entry.setKeyboardTracking(False)
        self.feed_flutes_entry.set_value(2)
        self.feed_rpm_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feed_chip_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feed_rate_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.feed_surface_entry = FCDoubleSpinner(callback=self.confirmation_message)

        self.feed_diameter_unit = FCLabel('mm')
        self.feed_chip_unit = FCLabel('mm/tooth')
        self.feed_rate_unit = FCLabel('mm/min')
        self.feed_surface_unit = FCLabel('m/min')

        self.feed_diameter_entry.setToolTip(_("Cutting diameter of the tool."))
        self.feed_flutes_entry.setToolTip(_("Number of cutting edges on the tool."))
        self.feed_rpm_entry.setToolTip(_("Spindle speed in revolutions per minute."))
        self.feed_chip_entry.setToolTip(_("Distance advanced per cutting edge per revolution."))
        self.feed_rate_entry.setToolTip(_("Feed rate = RPM x flute count x chip load."))
        self.feed_surface_entry.setToolTip(_("Surface speed = pi x tool diameter x RPM, converted to m/min or ft/min."))

        for entry in (self.feed_diameter_entry, self.feed_rpm_entry, self.feed_chip_entry,
                      self.feed_rate_entry, self.feed_surface_entry):
            entry.set_precision(max(self.decimals, 8))
            entry.set_range(0.0, 1000000000000.0)
            entry.setSingleStep(0.1)
            entry.setKeyboardTracking(False)
            entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                QtWidgets.QSizePolicy.Policy.Preferred)

        for row, label, entry, unit in (
            (4, _("Tool Diameter"), self.feed_diameter_entry, self.feed_diameter_unit),
            (6, _("Flutes"), self.feed_flutes_entry, FCLabel('')),
            (8, _("Spindle Speed"), self.feed_rpm_entry, FCLabel('RPM')),
            (10, _("Chip Load"), self.feed_chip_entry, self.feed_chip_unit),
            (12, _("Feed Rate"), self.feed_rate_entry, self.feed_rate_unit),
            (14, _("Surface Speed"), self.feed_surface_entry, self.feed_surface_unit)
        ):
            entry_layout = QtWidgets.QHBoxLayout()
            entry_layout.addWidget(entry)
            entry_layout.addWidget(unit)
            grid_feeds.addWidget(FCLabel('%s:' % label), row, 0)
            grid_feeds.addLayout(entry_layout, row, 1)

        self.calculate_feed_button = FCButton(_("Calculate"))
        self.calculate_feed_button.setIcon(QtGui.QIcon(self.app.resource_location + '/calculator32.png'))
        self.calculate_feed_button.setToolTip(_("Calculate the selected quantity using the other inputs."))
        grid_feeds.addWidget(self.calculate_feed_button, 16, 0, 1, 2)

        # #############################################################################################################
        # ############################## ElectroPlating Tool Calculator ###############################################
        # #############################################################################################################
        # ## Title of the ElectroPlating Tools Calculator
        ep_title_label = FCLabel('%s' % self.eplateName, color='purple', bold=True)
        ep_title_label.setToolTip(
            _("This calculator is useful for those who plate the via/pad/drill holes,\n"
              "using a method like graphite ink or calcium hypophosphite ink or palladium chloride.")
        )
        self.layout.addWidget(ep_title_label)

        ep_frame = FCFrame()
        self.layout.addWidget(ep_frame)

        grid_electro = GLay(v_spacing=5, h_spacing=3)
        ep_frame.setLayout(grid_electro)

        # grid_electro.addWidget(FCLabel(""), 0, 0, 1, 2)

        # Area Calculation
        self.area_sel_label = FCLabel('%s:' % _("Area Calculation"), bold=True)
        grid_electro.addWidget(self.area_sel_label, 4, 0)

        # Area Choice
        self.area_sel_label.setToolTip(
            _("Determine the board area.")
        )
        self.area_sel_radio = RadioSet([
            {'label': _('Dimensions'), 'value': 'd'},
            {"label": _("Area"), "value": "a"}
        ], compact=True)

        grid_electro.addWidget(self.area_sel_radio, 6, 0, 1, 2)

        # BOARD LENGTH
        self.pcblengthlabel = FCLabel('%s:' % _("Board Length"))
        self.pcblengthlabel.setToolTip(_('Board Length.'))
        self.pcblength_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.pcblength_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                           QtWidgets.QSizePolicy.Policy.Preferred)
        self.pcblength_entry.set_precision(self.decimals)
        self.pcblength_entry.set_range(0.0, 10000.0000)
        self.pcblength_entry.setKeyboardTracking(False)

        self.length_unit = FCLabel('%s' % _("cm"))
        self.length_unit.setMinimumWidth(25)

        l_hlay = QtWidgets.QHBoxLayout()
        l_hlay.addWidget(self.pcblength_entry)
        l_hlay.addWidget(self.length_unit)

        grid_electro.addWidget(self.pcblengthlabel, 8, 0)
        grid_electro.addLayout(l_hlay, 8, 1)

        # BOARD WIDTH
        self.pcbwidthlabel = FCLabel('%s:' % _("Board Width"))
        self.pcbwidthlabel.setToolTip(_('Board Width'))
        self.pcbwidth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.pcbwidth_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
        self.pcbwidth_entry.set_precision(self.decimals)
        self.pcbwidth_entry.set_range(0.0, 10000.0000)
        self.pcbwidth_entry.setKeyboardTracking(False)

        self.width_unit = FCLabel('%s' % _("cm"))
        self.width_unit.setMinimumWidth(25)

        w_hlay = QtWidgets.QHBoxLayout()
        w_hlay.addWidget(self.pcbwidth_entry)
        w_hlay.addWidget(self.width_unit)

        grid_electro.addWidget(self.pcbwidthlabel, 10, 0)
        grid_electro.addLayout(w_hlay, 10, 1)

        # AREA
        self.area_label = FCLabel('%s:' % _("Area"))
        self.area_label.setToolTip(_('Board area.'))
        self.area_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.area_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self.area_entry.set_precision(self.decimals)
        self.area_entry.set_range(0.0, 10000.0000)
        self.area_entry.setKeyboardTracking(False)

        self.area_unit = FCLabel('%s<sup>2</sup>' % _("cm"))
        self.area_unit.setMinimumWidth(25)

        a_hlay = QtWidgets.QHBoxLayout()
        a_hlay.addWidget(self.area_entry)
        a_hlay.addWidget(self.area_unit)

        grid_electro.addWidget(self.area_label, 12, 0)
        grid_electro.addLayout(a_hlay, 12, 1)

        # SIDES
        self.sides_label = FCLabel('%s:' % _("Sides"))
        self.sides_label.setToolTip(_("Number of board sides to plate."))
        self.sides_radio = RadioSet([
            {'label': '1', 'value': 1},
            {'label': '2', 'value': 2}
        ], compact=True)
        self.sides_radio.set_value(2)

        grid_electro.addWidget(self.sides_label, 13, 0)
        grid_electro.addWidget(self.sides_radio, 13, 1)

        ep_separator_1 = QtWidgets.QFrame()
        ep_separator_1.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        ep_separator_1.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid_electro.addWidget(ep_separator_1, 14, 0, 1, 2)

        # DENSITY
        self.cdensity_label = FCLabel('%s:' % _("Current Density"))
        self.cdensity_label.setToolTip(_("Current density applied to the board. \n"
                                         "In Amperes per Square Feet ASF."))
        self.cdensity_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cdensity_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
        self.cdensity_entry.set_precision(self.decimals)
        self.cdensity_entry.set_range(0.0, 10000.0000)
        self.cdensity_entry.setSingleStep(0.1)
        self.cdensity_entry.setKeyboardTracking(False)

        density_unit = FCLabel('%s' % "ASF")
        density_unit.setMinimumWidth(25)

        d_hlay = QtWidgets.QHBoxLayout()
        d_hlay.addWidget(self.cdensity_entry)
        d_hlay.addWidget(density_unit)

        grid_electro.addWidget(self.cdensity_label, 16, 0)
        grid_electro.addLayout(d_hlay, 16, 1)

        # COPPER GROWTH
        self.growth_label = FCLabel('%s:' % _("Copper Growth"))
        self.growth_label.setToolTip(_("Thickness of the deposited copper."))
        self.growth_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.growth_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                        QtWidgets.QSizePolicy.Policy.Preferred)
        self.growth_entry.set_precision(self.decimals)
        self.growth_entry.set_range(0.0, 10000.0000)
        self.growth_entry.setSingleStep(0.01)
        self.growth_entry.setKeyboardTracking(False)

        growth_unit = FCLabel('%s' % _("um"))
        growth_unit.setMinimumWidth(25)

        g_hlay = QtWidgets.QHBoxLayout()
        g_hlay.addWidget(self.growth_entry)
        g_hlay.addWidget(growth_unit)

        grid_electro.addWidget(self.growth_label, 18, 0)
        grid_electro.addLayout(g_hlay, 18, 1)

        ep_separator_2 = QtWidgets.QFrame()
        ep_separator_2.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        ep_separator_2.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid_electro.addWidget(ep_separator_2, 20, 0, 1, 2)

        # Output values
        self.output_lbl = FCLabel('%s' % _("Output"), bold=True)
        grid_electro.addWidget(self.output_lbl, 22, 0)

        # CURRENT
        self.cvaluelabel = FCLabel('%s:' % _("Current Value"))
        self.cvaluelabel.setToolTip(_('This is the current intensity value\n'
                                      'to be set on the Power Supply.'))
        self.cvalue_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.cvalue_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                        QtWidgets.QSizePolicy.Policy.Preferred)
        self.cvalue_entry.set_precision(self.decimals)
        self.cvalue_entry.set_range(0.0, 10000.0000)
        self.cvalue_entry.setSingleStep(0.1)

        current_unit = FCLabel('%s' % "A")
        current_unit.setMinimumWidth(25)
        self.cvalue_entry.setReadOnly(True)

        c_hlay = QtWidgets.QHBoxLayout()
        c_hlay.addWidget(self.cvalue_entry)
        c_hlay.addWidget(current_unit)

        grid_electro.addWidget(self.cvaluelabel, 24, 0)
        grid_electro.addLayout(c_hlay, 24, 1)

        # TIME
        self.timelabel = FCLabel('%s:' % _("Time"))
        self.timelabel.setToolTip(_('The time calculated to deposit copper.'))
        self.time_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.time_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self.time_entry.set_precision(self.decimals)
        self.time_entry.set_range(0.0, 10000.0000)
        self.time_entry.setSingleStep(0.1)

        time_unit = FCLabel('%s' % "min")
        time_unit.setMinimumWidth(25)
        self.time_entry.setReadOnly(True)

        t_hlay = QtWidgets.QHBoxLayout()
        t_hlay.addWidget(self.time_entry)
        t_hlay.addWidget(time_unit)

        grid_electro.addWidget(self.timelabel, 26, 0)
        grid_electro.addLayout(t_hlay, 26, 1)

        # #############################################################################################################
        # ############################## Tinning Calculator ###############################################
        # #############################################################################################################
        # ## Title of the Tinning Calculator
        tin_title_label = FCLabel('%s' % self.tinningName, color='orange', bold=True)
        tin_title_label.setToolTip(
            _("Calculator for chemical quantities\n"
              "required for tinning PCB's.")
        )
        self.layout.addWidget(tin_title_label)

        tin_frame = FCFrame()
        self.layout.addWidget(tin_frame)

        grid_tin = GLay(v_spacing=5, h_spacing=3)
        tin_frame.setLayout(grid_tin)

        # Solution
        self.solution_lbl = FCLabel('%s:' % _("Solution"))
        self.solution_lbl.setToolTip(
            _("Choose one solution for tinning.")
        )
        self.sol_radio = RadioSet([
            {'label': '1', 'value': 'sol1'},
            {"label": '2', "value": "sol2"}
        ], compact=True)

        grid_tin.addWidget(self.solution_lbl, 4, 0)
        grid_tin.addWidget(self.sol_radio, 4, 1)

        # Stannous Chloride
        self.sn_cl_lbl = FCLabel('%s :' % "SnCl<sub>2</sub>")
        self.sn_cl_lbl.setToolTip(_('Stannous Chloride.'))
        self.sn_cl_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.sn_cl_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                       QtWidgets.QSizePolicy.Policy.Preferred)
        self.sn_cl_entry.lineEdit().setReadOnly(True)
        self.sn_cl_entry.set_precision(self.decimals)
        self.sn_cl_entry.set_range(0.0, 10000.0000)

        self.sncl_unit = FCLabel('%s' % _("g"))
        self.sncl_unit.setMinimumWidth(25)

        sncl_hlay = QtWidgets.QHBoxLayout()
        sncl_hlay.addWidget(self.sn_cl_entry)
        sncl_hlay.addWidget(self.sncl_unit)

        grid_tin.addWidget(self.sn_cl_lbl, 8, 0)
        grid_tin.addLayout(sncl_hlay, 8, 1)

        # Thiourea
        self.th_label = FCLabel('%s:' % _("Thiourea"))
        self.th_label.setToolTip('%s.' % _('Thiourea'))
        self.th_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.th_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                    QtWidgets.QSizePolicy.Policy.Preferred)
        self.th_entry.lineEdit().setReadOnly(True)
        self.th_entry.set_precision(self.decimals)
        self.th_entry.set_range(0.0, 10000.0000)

        self.th_unit = FCLabel('%s' % _("g"))
        self.th_unit.setMinimumWidth(25)

        th_hlay = QtWidgets.QHBoxLayout()
        th_hlay.addWidget(self.th_entry)
        th_hlay.addWidget(self.th_unit)

        grid_tin.addWidget(self.th_label, 12, 0)
        grid_tin.addLayout(th_hlay, 12, 1)

        # Sulfamic Acid
        self.sa_label = FCLabel('%s :' % "H<sub>3</sub>NSO<sub>3</sub>")
        self.sa_label.setToolTip(_('Sulfamic Acid.'))
        self.sa_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.sa_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                    QtWidgets.QSizePolicy.Policy.Preferred)
        self.sa_entry.lineEdit().setReadOnly(True)
        self.sa_entry.set_precision(self.decimals)
        self.sa_entry.set_range(0.0, 10000.0000)

        self.sa_unit = FCLabel('%s' % _("g"))
        self.sa_unit.setMinimumWidth(25)

        sa_hlay = QtWidgets.QHBoxLayout()
        sa_hlay.addWidget(self.sa_entry)
        sa_hlay.addWidget(self.sa_unit)

        grid_tin.addWidget(self.sa_label, 14, 0)
        grid_tin.addLayout(sa_hlay, 14, 1)

        # Water
        self.h2o_label = FCLabel("H<sub>2</sub>O :")
        self.h2o_label.setToolTip(_('Distilled Water.'))
        self.h2o_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.h2o_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                     QtWidgets.QSizePolicy.Policy.Preferred)
        self.h2o_entry.lineEdit().setReadOnly(True)
        self.h2o_entry.set_precision(self.decimals)
        self.h2o_entry.set_range(0.0, 10000.0000)

        self.h20_unit = FCLabel('%s' % _("mL"))
        self.h20_unit.setMinimumWidth(25)

        h2o_hlay = QtWidgets.QHBoxLayout()
        h2o_hlay.addWidget(self.h2o_entry)
        h2o_hlay.addWidget(self.h20_unit)

        grid_tin.addWidget(self.h2o_label, 16, 0)
        grid_tin.addLayout(h2o_hlay, 16, 1)

        # Soap
        self.soap_label = FCLabel('%s:' % _("Soap"))
        self.soap_label.setToolTip(_('Liquid soap.'))
        self.soap_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.soap_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self.soap_entry.lineEdit().setReadOnly(True)
        self.soap_entry.set_precision(self.decimals)
        self.soap_entry.set_range(0.0, 10000.0000)

        self.soap_unit = FCLabel('%s' % _("mL"))
        self.soap_unit.setMinimumWidth(25)

        soap_hlay = QtWidgets.QHBoxLayout()
        soap_hlay.addWidget(self.soap_entry)
        soap_hlay.addWidget(self.soap_unit)

        grid_tin.addWidget(self.soap_label, 18, 0)
        grid_tin.addLayout(soap_hlay, 18, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid_tin.addWidget(separator_line, 20, 0, 1, 2)

        self.tin_opt_label = FCLabel('%s:' % _("Optional"))
        grid_tin.addWidget(self.tin_opt_label, 22, 0)

        # Sodium hypophosphite
        self.hypo_label = FCLabel("NaPO<sub>2</sub>H<sub>2</sub> :")
        self.hypo_label.setToolTip(
            _('Sodium hypophosphite.\n'
              'Optional, for solution stability.\n'
              'Warning: List 1 chemical in USA.'))
        self.hypo_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.hypo_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self.hypo_entry.lineEdit().setReadOnly(True)
        self.hypo_entry.set_precision(self.decimals)
        self.hypo_entry.set_range(0.0, 10000.0000)

        self.hypo_unit = FCLabel('%s' % _("g"))
        self.hypo_unit.setMinimumWidth(25)

        hypo_hlay = QtWidgets.QHBoxLayout()
        hypo_hlay.addWidget(self.hypo_entry)
        hypo_hlay.addWidget(self.hypo_unit)

        grid_tin.addWidget(self.hypo_label, 24, 0)
        grid_tin.addLayout(hypo_hlay, 24, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid_tin.addWidget(separator_line, 26, 0, 1, 2)

        # Volume
        self.vol_lbl = FCLabel('%s' % _("Volume"), color='red', bold=True)
        self.vol_lbl.setToolTip(_('Desired volume of tinning solution.'))
        self.vol_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.vol_entry.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding,
                                     QtWidgets.QSizePolicy.Policy.Preferred)
        self.vol_entry.set_precision(self.decimals)
        self.vol_entry.set_range(0.0, 10000.0000)

        self.vol_unit = FCLabel('%s' % _("mL"))
        self.vol_unit.setMinimumWidth(25)

        vol_hlay = QtWidgets.QHBoxLayout()
        vol_hlay.addWidget(self.vol_entry)
        vol_hlay.addWidget(self.vol_unit)

        grid_tin.addWidget(self.vol_lbl, 28, 0)
        grid_tin.addLayout(vol_hlay, 28, 1)

        # ## Buttons
        self.calculate_tin_button = FCButton(_("Calculate"))
        self.calculate_tin_button.setIcon(QtGui.QIcon(self.app.resource_location + '/calculator32.png'))
        self.calculate_tin_button.setToolTip(
            _("Calculate the chemical quantities for the desired volume of tinning solution.")
        )
        grid_tin.addWidget(self.calculate_tin_button, 30, 0, 1, 2)

        GLay.set_common_column_size([grid_units_layout, grid_electro, grid_vshape, grid_feeds, grid_tin], 0)

        self.layout.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.layout.addWidget(self.reset_button)

        # #################################### FINISHED GUI ###########################
        # #############################################################################

    def set_feed_units(self, units):
        metric = units == 'MM'
        self.feed_diameter_unit.setText('mm' if metric else 'in')
        self.feed_chip_unit.setText('mm/tooth' if metric else 'in/tooth')
        self.feed_rate_unit.setText('mm/min' if metric else 'in/min')
        self.feed_surface_unit.setText('m/min' if metric else 'ft/min')

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
