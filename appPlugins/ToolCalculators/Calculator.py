# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolCalculators.CalculatorUI import CalcUI
from appPlugins.ToolCalculators.CalculatorGen import (
    MM_PER_INCH, G_PER_OZ, ML_PER_FLOZ, v_shape_diameter, v_shape_depth,
    plating_current, plating_time, tinning_quantities, solve_feeds_speeds
)
from defaults import AppDefaults

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


# Values of the calculation selector, in the order of its items.
FEED_TARGETS = ('feed', 'chip', 'rpm_feed', 'surface', 'rpm_surface')


class ToolCalculator(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.decimals = self.app.decimals

        self.ui = CalcUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolCalculators()")

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)

                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()
        self.app.ui.notebook.setTabText(2, _("Calculators"))

    def connect_signals_at_init(self):
        # (source entry, destination entry, destination = source * factor)
        conversions = [
            (self.ui.mm_entry, self.ui.inch_entry, 1.0 / MM_PER_INCH),
            (self.ui.inch_entry, self.ui.mm_entry, MM_PER_INCH),
            (self.ui.g_entry, self.ui.oz_entry, 1.0 / G_PER_OZ),
            (self.ui.oz_entry, self.ui.g_entry, G_PER_OZ),
            (self.ui.ml_entry, self.ui.fl_oz_entry, 1.0 / ML_PER_FLOZ),
            (self.ui.fl_oz_entry, self.ui.ml_entry, ML_PER_FLOZ),
        ]
        for src_entry, dst_entry, factor in conversions:
            src_entry.editingFinished.connect(
                lambda s=src_entry, d=dst_entry, f=factor: self.convert(s, d, f))
        self.ui.sides_radio.activated_custom.connect(lambda: self.ep_calculate_output())

        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        self.ui.calculate_tin_button.clicked.connect(lambda: self.on_tin_solution_calculation())
        self.ui.sol_radio.activated_custom.connect(self.on_tin_solution_type)
        self.ui.area_sel_radio.activated_custom.connect(self.on_area_calculation_radio)
        self.ui.feed_target_combo.currentIndexChanged.connect(self.on_feed_target_changed)
        self.ui.feed_units_radio.activated_custom.connect(self.on_feed_units_changed)
        self.ui.calculate_feed_button.clicked.connect(self.on_calculate_feed)

    def on_area_calculation_radio(self, val):
        self.ui_disconnect()
        self.ui_connect()

        if val == 'a':
            self.ui.pcbwidthlabel.hide()
            self.ui.pcbwidth_entry.hide()
            self.ui.width_unit.hide()
            self.ui.pcblengthlabel.hide()
            self.ui.pcblength_entry.hide()
            self.ui.length_unit.hide()
            self.ui.area_label.show()
            self.ui.area_entry.show()
            self.ui.area_unit.show()
        else:
            self.ui.pcbwidthlabel.show()
            self.ui.pcbwidth_entry.show()
            self.ui.width_unit.show()
            self.ui.pcblengthlabel.show()
            self.ui.pcblength_entry.show()
            self.ui.length_unit.show()
            self.ui.area_label.hide()
            self.ui.area_entry.hide()
            self.ui.area_unit.hide()

        self.ep_calculate_output()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+C', **kwargs)

    def set_tool_ui(self):
        self.clear_ui(self.layout)
        self.ui = CalcUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName

        # Units Calculator
        self.ui.mm_entry.set_value('%.*f' % (self.decimals, 0))
        self.ui.inch_entry.set_value('%.*f' % (self.decimals, 0))
        self.ui.g_entry.set_value('%.*f' % (self.decimals, 0))
        self.ui.oz_entry.set_value('%.*f' % (self.decimals, 0))
        self.ui.ml_entry.set_value('%.*f' % (self.decimals, 0))
        self.ui.fl_oz_entry.set_value('%.*f' % (self.decimals, 0))

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        def pref(key):
            # options first, then the saved defaults, then the factory defaults
            return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

        self.ui.pcblength_entry.set_value(pref("tools_calc_electro_length"))
        self.ui.pcbwidth_entry.set_value(pref("tools_calc_electro_width"))
        self.ui.area_entry.set_value(pref("tools_calc_electro_area"))
        self.ui.cdensity_entry.set_value(pref("tools_calc_electro_cdensity"))
        self.ui.growth_entry.set_value(pref("tools_calc_electro_growth"))
        self.ui.sides_radio.set_value(2)
        self.ui.cvalue_entry.set_value(0.00)
        self.ui.time_entry.set_value(0.0)
        self.ui.tipDia_entry.set_value(pref("tools_calc_vshape_tip_dia"))
        self.ui.tipAngle_entry.set_value(pref("tools_calc_vshape_tip_angle"))
        self.ui.cutDepth_entry.set_value(pref("tools_calc_vshape_cut_z"))
        self.ui.area_sel_radio.set_value('d')
        self.ui.sol_radio.set_value("sol1")

        # Feeds and speeds: no material-specific cutting parameters are assumed.
        self.feed_units = self.app.app_units.upper()
        self.ui.feed_units_radio.set_value(self.feed_units)
        self.ui.feed_flutes_entry.set_value(2)
        self.ui.set_feed_units(self.feed_units)
        self.on_feed_target_changed()

        self.connect_signals_at_init()
        self.on_calculate_tool_dia()
        self.on_area_calculation_radio(val=self.ui.area_sel_radio.get_value())
        self.on_tin_solution_type('sol1')
        self.ui_disconnect()
        self.ui_connect()

    def on_calculate_tool_dia(self):
        self.ui_disconnect()
        try:
            diameter = v_shape_diameter(self.ui.tipDia_entry.get_value(), self.ui.tipAngle_entry.get_value(),
                                        self.ui.cutDepth_entry.get_value())
            self.ui.effectiveToolDia_entry.set_value(self.app.dec_format(diameter, self.decimals))
            self.app.inform.emit('[success] %s' % _("Cut width (tool diameter) calculated."))
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % err)
        finally:
            self.ui_connect()

    def on_calculate_cutz(self):
        self.ui_disconnect()
        try:
            depth = v_shape_depth(self.ui.tipDia_entry.get_value(), self.ui.tipAngle_entry.get_value(),
                                  self.ui.effectiveToolDia_entry.get_value())
            self.ui.cutDepth_entry.set_value(self.app.dec_format(depth, self.decimals))
            self.app.inform.emit('[success] %s' % _("Cut depth (Cut Z) calculated."))
        except ValueError as err:
            if self.ui.tipDia_entry.get_value() > self.ui.effectiveToolDia_entry.get_value():
                self.ui.cutDepth_entry.set_value(0.0)
            self.app.inform.emit('[ERROR_NOTCL] %s' % err)
        finally:
            self.ui_connect()

    def convert(self, src_entry, dst_entry, factor):
        value = src_entry.get_value()
        if value is None:
            return
        dst_entry.set_value('%.*f' % (self.decimals, float(value) * factor))

    def ep_calculate_current(self):
        self.ui_disconnect()
        if self.ui.area_sel_radio.get_value() == 'd':
            area = self.ui.pcblength_entry.get_value() * self.ui.pcbwidth_entry.get_value()
        else:
            area = self.ui.area_entry.get_value()
        current = plating_current(area, self.ui.sides_radio.get_value(), self.ui.cdensity_entry.get_value())
        self.ui.cvalue_entry.set_value('%.3f' % current)
        self.ui_connect()

    def ep_calculate_procedure_time(self):
        self.ui_disconnect()
        time = plating_time(self.ui.growth_entry.get_value(), self.ui.cdensity_entry.get_value())
        self.ui.time_entry.set_value('%.1f' % time)
        self.ui_connect()

    def ep_calculate_output(self):
        self.ep_calculate_procedure_time()
        self.ep_calculate_current()

    def on_tin_solution_type(self, val):
        self.set_tin_quantities(val, 100)
        self.ui.vol_entry.set_value(100)

    def on_tin_solution_calculation(self):
        self.set_tin_quantities(self.ui.sol_radio.get_value(), self.ui.vol_entry.get_value())

    def set_tin_quantities(self, solution_type, volume):
        quantities = tinning_quantities(solution_type, volume)
        self.ui.sn_cl_entry.set_value(quantities['sncl2'])
        self.ui.th_entry.set_value(quantities['thiourea'])
        self.ui.sa_entry.set_value(quantities['sulfamic'])
        self.ui.h2o_entry.set_value(quantities['water'])
        self.ui.soap_entry.set_value(quantities['soap'])
        self.ui.hypo_entry.set_value(quantities['hypo'])

    def on_feed_target_changed(self):
        target = FEED_TARGETS[self.ui.feed_target_combo.currentIndex()]
        self.ui.feed_rate_entry.setReadOnly(target == 'feed')
        self.ui.feed_chip_entry.setReadOnly(target == 'chip')
        self.ui.feed_rpm_entry.setReadOnly(target in ('rpm_feed', 'rpm_surface'))
        self.ui.feed_surface_entry.setReadOnly(target != 'rpm_surface')

    def on_feed_units_changed(self, units):
        if units == self.feed_units:
            return
        length_factor = MM_PER_INCH if units == 'MM' else 1.0 / MM_PER_INCH
        surface_factor = 0.3048 if units == 'MM' else 1.0 / 0.3048
        conversions = [
            (self.ui.feed_diameter_entry, length_factor),
            (self.ui.feed_chip_entry, length_factor),
            (self.ui.feed_rate_entry, length_factor),
            (self.ui.feed_surface_entry, surface_factor)
        ]
        values = [(entry, entry.get_value() * factor) for entry, factor in conversions]
        if any(value > entry.maximum() or (value > 0 and round(value, entry.decimals()) == 0)
               for entry, value in values):
            self.ui.feed_units_radio.blockSignals(True)
            self.ui.feed_units_radio.set_value(self.feed_units)
            self.ui.feed_units_radio.blockSignals(False)
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Converted value is outside the supported range."))
            return
        for entry, value in values:
            entry.set_value(value)
        self.feed_units = units
        self.ui.set_feed_units(units)

    def on_calculate_feed(self):
        target = FEED_TARGETS[self.ui.feed_target_combo.currentIndex()]
        try:
            result = solve_feeds_speeds(
                target, diameter=self.ui.feed_diameter_entry.get_value(),
                flutes=self.ui.feed_flutes_entry.get_value(), rpm=self.ui.feed_rpm_entry.get_value(),
                chip_load=self.ui.feed_chip_entry.get_value(), feed=self.ui.feed_rate_entry.get_value(),
                surface=self.ui.feed_surface_entry.get_value(), units=self.feed_units
            )
            entries = {'feed': self.ui.feed_rate_entry, 'chip_load': self.ui.feed_chip_entry,
                       'rpm': self.ui.feed_rpm_entry, 'surface': self.ui.feed_surface_entry}
            # Validate every output before writing any, so a failed calculation leaves the form intact.
            for name, value in result.items():
                entry = entries[name]
                if value > entry.maximum() or round(value, entry.decimals()) <= 0:
                    raise ValueError(_("Calculated result is outside the supported range."))
            for name, value in result.items():
                entries[name].set_value(value)
        except (ValueError, OverflowError, ZeroDivisionError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % err)
            return
        self.app.inform.emit('[success] %s' % _("Feeds and speeds calculated."))

    def ui_connect(self):
        self.ui.cutDepth_entry.valueChanged.connect(self.on_calculate_tool_dia)
        self.ui.cutDepth_entry.returnPressed.connect(self.on_calculate_tool_dia)
        self.ui.effectiveToolDia_entry.valueChanged.connect(self.on_calculate_cutz)
        self.ui.effectiveToolDia_entry.returnPressed.connect(self.on_calculate_cutz)
        self.ui.tipDia_entry.valueChanged.connect(self.on_calculate_tool_dia)
        self.ui.tipDia_entry.returnPressed.connect(self.on_calculate_tool_dia)
        self.ui.tipAngle_entry.valueChanged.connect(self.on_calculate_tool_dia)
        self.ui.tipAngle_entry.returnPressed.connect(self.on_calculate_tool_dia)
        self.ui.calculate_vshape_button.clicked.connect(self.on_calculate_tool_dia)
        self.ui.cdensity_entry.valueChanged.connect(self.ep_calculate_output)
        self.ui.growth_entry.valueChanged.connect(self.ep_calculate_output)
        if self.ui.area_sel_radio.get_value() == 'a':
            self.ui.area_entry.valueChanged.connect(self.ep_calculate_output)
        else:
            self.ui.pcblength_entry.valueChanged.connect(self.ep_calculate_output)
            self.ui.pcbwidth_entry.valueChanged.connect(self.ep_calculate_output)

    def ui_disconnect(self):
        for signal in (
            self.ui.cutDepth_entry.valueChanged, self.ui.cutDepth_entry.returnPressed,
            self.ui.effectiveToolDia_entry.valueChanged, self.ui.effectiveToolDia_entry.returnPressed,
            self.ui.tipDia_entry.valueChanged, self.ui.tipDia_entry.returnPressed,
            self.ui.tipAngle_entry.valueChanged, self.ui.tipAngle_entry.returnPressed,
            self.ui.calculate_vshape_button.clicked, self.ui.cdensity_entry.valueChanged,
            self.ui.growth_entry.valueChanged, self.ui.area_entry.valueChanged,
            self.ui.pcblength_entry.valueChanged, self.ui.pcbwidth_entry.valueChanged
        ):
            try:
                signal.disconnect()
            except (AttributeError, TypeError):
                pass
