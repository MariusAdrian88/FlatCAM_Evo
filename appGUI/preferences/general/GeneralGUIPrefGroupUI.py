
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import QSettings
from pathlib import Path
import re

from appGUI.GUIElements import RadioSet, FCCheckBox, FCComboBox, FCSliderWithSpinner, FCColorEntry, FCLabel, \
    GLay, FCFrame, FCButton, FCSpinner, FCMessageBox
from appCommon.IconColors import recolor_icons
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from appTranslation import restart_program
from appDatabase import _database_option

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class GeneralGUIPrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):
        super(GeneralGUIPrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("GUI Preferences")))
        self.decimals = app.decimals
        self.options = app.options

        self.param_lbl = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.layout.addWidget(self.param_lbl)

        # #############################################################################################################
        # Grid0 Frame
        # #############################################################################################################
        par_frame = FCFrame()
        self.layout.addWidget(par_frame)

        grid0 = GLay(v_spacing=5, h_spacing=3)
        par_frame.setLayout(grid0)

        # Theme selection
        self.appearance_label = FCLabel('%s' % _("Appearance"), bold=True)
        self.appearance_label.setToolTip(
            _("Select the application color scheme.\n"
              "Auto matches the operating system.")
        )

        self.appearance_radio = RadioSet([
            {"label": _("Auto"), "value": "auto"},
            {"label": _("Light"), "value": "light"},
            {"label": _("Dark"), "value": "dark"}
        ], compact=True)
        self.appearance_radio.setToolTip(
            _("The appearance can be:\n"
              "Auto: Matches mode from OS\n"
              "Light: Light mode\n"
              "Dark: Dark mode")
        )

        # Dark Canvas
        self.dark_canvas_cb = FCCheckBox('%s' % _('Dark Canvas'))
        self.dark_canvas_cb.setToolTip(
            _("Check this box to force the use of dark canvas\n"
              "even if a dark theme is not selected.")
        )

        # Dim Text
        self.dim_text_cb = FCCheckBox('%s' % _('Dim text'))
        self.dim_text_cb.setToolTip(
            _("Show the GUI text (menus, toolbars, tabs etc) in a darker gray\n"
              "instead of bright white. Text with its own color is not changed.\n"
              "It is useful for the Dark theme and it has no effect on the Light theme.")
        )

        grid0.addWidget(self.appearance_label, 0, 0, 1, 2)
        grid0.addWidget(self.appearance_radio, 1, 0, 1, 3)
        grid0.addWidget(self.dark_canvas_cb, 2, 0)
        grid0.addWidget(self.dim_text_cb, 2, 1, 1, 2)

        # Custom Title Bar
        self.custom_titlebar_cb = FCCheckBox('%s' % _('Custom Title Bar'))
        self.custom_titlebar_cb.setToolTip(
            _("Use the customized title bar with the hamburger menu and panel toggle.\n"
              "When unchecked, use the operating system's title bar and the original menu.\n"
              "Changing this option requires an application restart.")
        )
        grid0.addWidget(self.custom_titlebar_cb, 3, 0)

        # Hamburger Menu
        self.hamburger_cb = FCCheckBox('%s' % _('Hamburger Menu'))
        self.hamburger_cb.setToolTip(
            _("Collapse the menu of the custom title bar into a hamburger button.\n"
              "When unchecked, the menu is always shown.")
        )
        grid0.addWidget(self.hamburger_cb, 3, 1, 1, 2)
        # the hamburger button exists only in the custom title bar
        self.hamburger_cb.setEnabled(self.custom_titlebar_cb.get_value())
        self.custom_titlebar_cb.toggled.connect(self.hamburger_cb.setEnabled)

        # self.theme_button = FCButton(_("Apply Theme"))
        # self.theme_button.setToolTip(
        #     _("Select a theme for FlatCAM.\n"
        #       "It will theme the plot area.\n"
        #       "The application will restart after change.")
        # )
        # grid0.addWidget(self.theme_button, 2, 0, 1, 3)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid0.addWidget(separator_line, 4, 0, 1, 2)

        self.styling_label = FCLabel('%s:' % _('Styling'))
        self.styling_combo = FCComboBox()
        self.styling_combo.addItem(_('Default'), 'default')
        self.styling_combo.addItem(_('Custom'), 'custom')
        self.styling_combo.get_value = self.styling_combo.currentData
        self.styling_combo.set_value = lambda val: self.styling_combo.setCurrentIndex(
            self.styling_combo.findData(val))
        self.styling_combo.setToolTip(
            _("Default uses the Qt widget style without an application stylesheet.\n"
              "Custom applies the light or dark application stylesheet.\n"
              "Changing this option requires an application restart.")
        )
        grid0.addWidget(self.styling_label, 5, 0)
        grid0.addWidget(self.styling_combo, 5, 1)

        # Layout selection
        self.layout_label = FCLabel('%s:' % _('Layout'))
        self.layout_label.setToolTip(
            _("Select a layout for the application.\n"
              "It is applied immediately.")
        )
        self.layout_combo = FCComboBox()
        # don't translate the QCombo items as they are used in QSettings and identified by name
        self.layout_combo.addItem("standard")
        self.layout_combo.addItem("compact")
        self.layout_combo.addItem("minimal")

        grid0.addWidget(self.layout_label, 6, 0)
        grid0.addWidget(self.layout_combo, 6, 1)

        # Set the current index for layout_combo
        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("layout"):
            layout = q_settings.value('layout', type=str)
            idx = self.layout_combo.findText(layout)
            self.layout_combo.setCurrentIndex(idx)

        # Style selection
        self.style_label = FCLabel('%s:' % _('Qt Widget Style'))
        self.style_label.setToolTip(
            _("Select a style for the application.\n"
              "It will be applied at the next app start.")
        )
        self.style_combo = FCComboBox()
        self.style_combo.addItems(QtWidgets.QStyleFactory.keys())
        # find current style
        current_style = (QtWidgets.QApplication.instance().property('flatcam_style') or
                         QtWidgets.QApplication.style().objectName())
        index = self.style_combo.findText(current_style, QtCore.Qt.MatchFlag.MatchFixedString)
        self.style_combo.setCurrentIndex(index)
        self.style_combo.textActivated.connect(self.handle_style)

        grid0.addWidget(self.style_label, 8, 0)
        grid0.addWidget(self.style_combo, 8, 1)

        # Enable Hover box
        self.hover_cb = FCCheckBox('%s' % _('Hover Shape'))
        self.hover_cb.setToolTip(
            _("Enable display of a hover shape for the application objects.\n"
              "It is displayed whenever the mouse cursor is hovering\n"
              "over any kind of not-selected object.")
        )
        grid0.addWidget(self.hover_cb, 10, 0)

        # Enable Selection box
        self.selection_cb = FCCheckBox('%s' % _('Selection Shape'))
        self.selection_cb.setToolTip(
            _("Enable the display of a selection shape for the application objects.\n"
              "It is displayed whenever the mouse selects an object\n"
              "either by clicking or dragging mouse from left to right or\n"
              "right to left.")
        )
        grid0.addWidget(self.selection_cb, 12, 0)

        # Enable Selection box
        self.selection_outline_cb = FCCheckBox('%s' % _('Selection Outline'))
        self.selection_outline_cb.setToolTip(
            _("If checked, the selection shape is an outline.")
        )
        grid0.addWidget(self.selection_outline_cb, 12, 1, 1, 2)

        # Joined redraws of the object plots
        self.joined_redraws_cb = FCCheckBox('%s' % _('Joined Redraws'))
        self.joined_redraws_cb.setToolTip(
            _("Checked: the canvas joins the redraws of many object plots into one update.\n"
              "Opening many objects is faster, but a plot can show up to 0.15 s later.\n"
              "Unchecked: the canvas updates after each object plot.\n"
              "Each plot shows at once, but many objects plot slower.")
        )
        grid0.addWidget(self.joined_redraws_cb, 10, 1, 1, 2)
        self.joined_redraws_cb.toggled.connect(self.on_joined_redraws)

        # Font Size
        self.font_size_lbl = FCLabel('%s:' % _("Font Size"))
        self.font_size_lbl.setToolTip(
            _("Setting the Font Size for the entire application.")
        )
        self.app_font_size_entry = FCSpinner()
        self.app_font_size_entry.set_range(9, 18)

        grid0.addWidget(self.font_size_lbl, 16, 0)
        grid0.addWidget(self.app_font_size_entry, 16, 1)

        # Apply UI parameters
        self.apply_app_font_size_btn = FCButton(_("Apply and Restart"), bold=True)
        self.apply_app_font_size_btn.setToolTip(
            _("Setting the Font Size for the entire application.")
        )
        grid0.addWidget(self.apply_app_font_size_btn, 18, 0, 1, 2)

        # #############################################################################################################
        # Grid1 Frame
        # #############################################################################################################
        self.color_lbl = FCLabel('%s' % _("Colors"), color='teal', bold=True)
        self.layout.addWidget(self.color_lbl)

        color_frame = FCFrame()
        self.layout.addWidget(color_frame)

        grid1 = GLay(v_spacing=5, h_spacing=3)
        color_frame.setLayout(grid1)

        # Plot Selection (left - right) Color
        self.sel_lr_label = FCLabel('%s' % _('Left-Right Selection Color'), bold=True)
        grid1.addWidget(self.sel_lr_label, 0, 0, 1, 2)

        self.sl_color_label = FCLabel('%s:' % _('Outline'))
        self.sl_color_label.setToolTip(
            _("Set the line color for the 'left to right' selection box.")
        )
        self.sl_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.sl_color_label, 2, 0)
        grid1.addWidget(self.sl_color_entry, 2, 1)

        self.sf_color_label = FCLabel('%s:' % _('Fill'))
        self.sf_color_label.setToolTip(
            _("Set the fill color for the selection box\n"
              "in case that the selection is done from left to right.\n"
              "First 6 digits are the color and the last 2\n"
              "digits are for alpha (transparency) level.")
        )
        self.sf_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.sf_color_label, 4, 0)
        grid1.addWidget(self.sf_color_entry, 4, 1)

        # Plot Selection (left - right) Fill Transparency Level
        self.left_right_alpha_label = FCLabel('%s:' % _('Alpha'))
        self.left_right_alpha_label.setToolTip(
            _("Set the fill transparency for the 'left to right' selection box.")
        )
        self.left_right_alpha_entry = FCSliderWithSpinner(0, 255, 1)

        grid1.addWidget(self.left_right_alpha_label, 6, 0)
        grid1.addWidget(self.left_right_alpha_entry, 6, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 8, 0, 1, 2)

        # Plot Selection (left - right) Color
        self.sel_rl_label = FCLabel('%s' % _('Right-Left Selection Color'), bold=True)
        grid1.addWidget(self.sel_rl_label, 10, 0, 1, 2)

        # Plot Selection (right - left) Line Color
        self.alt_sl_color_label = FCLabel('%s:' % _('Outline'))
        self.alt_sl_color_label.setToolTip(
            _("Set the line color for the 'right to left' selection box.")
        )
        self.alt_sl_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.alt_sl_color_label, 12, 0)
        grid1.addWidget(self.alt_sl_color_entry, 12, 1)

        # Plot Selection (right - left) Fill Color
        self.alt_sf_color_label = FCLabel('%s:' % _('Fill'))
        self.alt_sf_color_label.setToolTip(
            _("Set the fill color for the selection box\n"
              "in case that the selection is done from right to left.\n"
              "First 6 digits are the color and the last 2\n"
              "digits are for alpha (transparency) level.")
        )
        self.alt_sf_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.alt_sf_color_label, 14, 0)
        grid1.addWidget(self.alt_sf_color_entry, 14, 1)

        # Plot Selection (right - left) Fill Transparency Level
        self.right_left_alpha_label = FCLabel('%s:' % _('Alpha'))
        self.right_left_alpha_label.setToolTip(
            _("Set the fill transparency for selection 'right to left' box.")
        )
        self.right_left_alpha_entry = FCSliderWithSpinner(0, 255, 1)

        grid1.addWidget(self.right_left_alpha_label, 16, 0)
        grid1.addWidget(self.right_left_alpha_entry, 16, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 18, 0, 1, 2)

        # ------------------------------------------------------------------
        # ----------------------- Editor Color -----------------------------
        # ------------------------------------------------------------------

        self.editor_color_label = FCLabel('%s' % _('Editor Color'), bold=True)
        grid1.addWidget(self.editor_color_label, 20, 0, 1, 2)

        # Editor Draw Color
        self.draw_color_label = FCLabel('%s:' % _('Drawing'))
        self.alt_sf_color_label.setToolTip(
            _("Set the color for the shape.")
        )
        self.draw_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.draw_color_label, 22, 0)
        grid1.addWidget(self.draw_color_entry, 22, 1)

        # Editor Draw Selection Color
        self.sel_draw_color_label = FCLabel('%s:' % _('Selection'))
        self.sel_draw_color_label.setToolTip(
            _("Set the color of the shape when selected.")
        )
        self.sel_draw_color_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.sel_draw_color_label, 24, 0)
        grid1.addWidget(self.sel_draw_color_entry, 24, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 26, 0, 1, 2)

        proj_item_lbl = FCLabel('%s' % _("Project Items Color"), bold=True, color='green')
        grid1.addWidget(proj_item_lbl, 27, 0, 1, 2)
        # ------------------------------------------------------------------
        # ----------------------- Project Settings -----------------------------
        # ------------------------------------------------------------------
        # Light Theme
        self.proj_settings_l_label = FCLabel('%s %s' % (_("Light"), _("Theme")), bold=True)
        grid1.addWidget(self.proj_settings_l_label, 28, 0, 1, 2)

        # Project Tab items color
        self.proj_color_l_label = FCLabel('%s:' % _('Enabled'))
        self.proj_color_l_label.setToolTip(
            '%s %s' % (_("Set the color of the items in Project Tab Tree."), _("Light Theme."))
        )
        self.proj_color_light_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.proj_color_l_label, 30, 0)
        grid1.addWidget(self.proj_color_light_entry, 30, 1)

        self.proj_color_dis_l_label = FCLabel('%s:' % _('Disabled'))
        self.proj_color_dis_l_label.setToolTip(
            '%s %s' % (
                _("Set the color of the items in Project Tab Tree,\n"
                  "for the case when the items are disabled."),
                _("Light Theme."))
        )
        self.proj_color_dis_light_entry = FCColorEntry(icon=QtGui.QIcon(
            self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.proj_color_dis_l_label, 32, 0)
        grid1.addWidget(self.proj_color_dis_light_entry, 32, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 33, 0, 1, 2)

        # Dark Theme
        self.proj_settings_d_label = FCLabel('%s %s' % (_("Dark"), _("Theme")), bold=True)
        grid1.addWidget(self.proj_settings_d_label, 34, 0, 1, 2)

        # Project Tab items color
        self.proj_color_d_label = FCLabel('%s:' % _('Enabled'))
        self.proj_color_d_label.setToolTip(
            '%s %s' % (_("Set the color of the items in Project Tab Tree."), _("Dark Theme."))
        )
        self.proj_color_dark_entry = FCColorEntry(icon=QtGui.QIcon(self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.proj_color_d_label, 36, 0)
        grid1.addWidget(self.proj_color_dark_entry, 36, 1)

        self.proj_color_dis_d_label = FCLabel('%s:' % _('Disabled'))
        self.proj_color_dis_d_label.setToolTip(
            '%s %s' % (
                _("Set the color of the items in Project Tab Tree,\n"
                  "for the case when the items are disabled."),
                _("Dark Theme."))
        )
        self.proj_color_dis_dark_entry = FCColorEntry(icon=QtGui.QIcon(
            self.app.resource_location + '/set_colors64.png'))

        grid1.addWidget(self.proj_color_dis_d_label, 38, 0)
        grid1.addWidget(self.proj_color_dis_dark_entry, 38, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 40, 0, 1, 2)

        self.icon_color_label = FCLabel('%s' % _("Icons Color"), bold=True, color='green')
        grid1.addWidget(self.icon_color_label, 41, 0, 1, 2)

        self.icon_settings_l_label = FCLabel('%s %s' % (_("Light"), _("Theme")), bold=True)
        grid1.addWidget(self.icon_settings_l_label, 42, 0, 1, 2)
        self.icon_color_l_label = FCLabel('%s:' % _('Color'))
        self.icon_color_light_entry = FCColorEntry(icon=QtGui.QIcon(
            self.app.resource_location + '/set_colors64.png'))
        self.icon_color_light_entry.set_value(_database_option(self.app, 'global_icon_color_light'))
        grid1.addWidget(self.icon_color_l_label, 44, 0)
        grid1.addWidget(self.icon_color_light_entry, 44, 1)
        self.icon_color_light_apply_btn = FCButton(_("Apply"))
        grid1.addWidget(self.icon_color_light_apply_btn, 46, 0, 1, 2)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        grid1.addWidget(separator_line, 48, 0, 1, 2)

        self.icon_settings_d_label = FCLabel('%s %s' % (_("Dark"), _("Theme")), bold=True)
        grid1.addWidget(self.icon_settings_d_label, 49, 0, 1, 2)
        self.icon_color_d_label = FCLabel('%s:' % _('Color'))
        self.icon_color_dark_entry = FCColorEntry(icon=QtGui.QIcon(
            self.app.resource_location + '/set_colors64.png'))
        self.icon_color_dark_entry.set_value(_database_option(self.app, 'global_icon_color_dark'))
        grid1.addWidget(self.icon_color_d_label, 51, 0)
        grid1.addWidget(self.icon_color_dark_entry, 51, 1)
        self.icon_color_dark_apply_btn = FCButton(_("Apply"))
        grid1.addWidget(self.icon_color_dark_apply_btn, 53, 0, 1, 2)

        icon_tooltip = _("Recolor single-color icons in this theme's resource files.\n"
                         "Mixed-color icons, Project Tab icons and status indicators are protected.\n"
                         "Transparency is preserved; the color picker's alpha value is ignored.\n"
                         "Changes become visible after restarting the application.")
        for widget in (self.icon_color_light_entry, self.icon_color_dark_entry,
                       self.icon_color_light_apply_btn, self.icon_color_dark_apply_btn):
            widget.setToolTip(icon_tooltip)

        # Project autohide CB
        self.project_autohide_cb = FCCheckBox(label=_('Project AutoHide'))
        self.project_autohide_cb.setToolTip(
            _("Check this box if you want the project/selected/tool tab area to\n"
              "hide automatically when there are no objects loaded and\n"
              "to show whenever a new object is created.")
        )

        row = grid0.rowCount()
        grid0.addWidget(self.project_autohide_cb, row, 0, 1, 3)

        self.object_autohide_cb = FCCheckBox(label=_('Object AutoHide'))
        self.object_autohide_cb.setToolTip(
            _("When checked, once a new object is generated the source object is hidden on canvas."))
        grid0.addWidget(self.object_autohide_cb, row + 1, 0)

        self.object_autoswitch_cb = FCCheckBox(label=_('Object Autoswitch'))
        self.object_autoswitch_cb.setToolTip(
            _("When checked, on new object creation, switch to Properties."))
        grid0.addWidget(self.object_autoswitch_cb, row + 1, 1, 1, 2)

        GLay.set_common_column_size([grid0, grid1], 0)

        self.layout.addStretch()

        # #############################################################################
        # ############################# GUI COLORS SIGNALS ############################
        # #############################################################################

        # Setting selection (left - right) colors signals
        self.sf_color_entry.editingFinished.connect(self.on_sf_color_entry)
        self.sl_color_entry.editingFinished.connect(self.on_sl_color_entry)

        self.left_right_alpha_entry.valueChanged.connect(self.on_left_right_alpha_changed)  # alpha

        # Setting selection (right - left) colors signals
        self.alt_sf_color_entry.editingFinished.connect(self.on_alt_sf_color_entry)
        self.alt_sl_color_entry.editingFinished.connect(self.on_alt_sl_color_entry)

        self.right_left_alpha_entry.valueChanged.connect(self.on_right_left_alpha_changed)  # alpha

        # Setting Editor Draw colors signals
        self.draw_color_entry.editingFinished.connect(self.on_draw_color_entry)
        self.sel_draw_color_entry.editingFinished.connect(self.on_sel_draw_color_entry)

        self.proj_color_light_entry.editingFinished.connect(self.on_proj_color_light_entry)
        self.proj_color_dis_light_entry.editingFinished.connect(self.on_proj_color_dis_light_entry)

        self.proj_color_dark_entry.editingFinished.connect(self.on_proj_color_dark_entry)
        self.proj_color_dis_dark_entry.editingFinished.connect(self.on_proj_color_dis_dark_entry)

        self.icon_color_light_apply_btn.clicked.connect(
            lambda: self.on_icon_color_apply('light', self.icon_color_light_entry.get_value()))
        self.icon_color_dark_apply_btn.clicked.connect(
            lambda: self.on_icon_color_apply('dark', self.icon_color_dark_entry.get_value()))

        self.layout_combo.activated.connect(self.app.on_layout)

        self.apply_app_font_size_btn.clicked.connect(
            lambda: self.handle_font_size(self.app, self.app_font_size_entry.get_value()))

        # Set UI
        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("font_size"):
            font_size = int(q_settings.value("font_size", type=str))  # noqa
            self.app_font_size_entry.set_value(font_size)

    def on_icon_color_apply(self, theme, value):
        if (theme not in ('light', 'dark') or not isinstance(value, str) or
                re.fullmatch(r'#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', value) is None):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Enter a valid hex color in #RRGGBB format."))
            return
        color = value[:7].lower()
        directory = Path(self.app.resource_location)
        if directory.name == 'dark_resources':
            directory = directory.parent
        if theme == 'dark':
            directory /= 'dark_resources'

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            count = recolor_icons(directory, color)
        except (OSError, ValueError) as error:
            self.app.log.error('Icon recoloring failed: %s', error)
            self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Could not recolor icons."), str(error)))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        key = 'global_icon_color_%s' % theme
        self.app.options[key] = color
        self.app.defaults[key] = color
        self.app.defaults.current_defaults[key] = color
        getattr(self, 'icon_color_%s_entry' % theme).set_value(color)
        self.app.preferencesUiManager.save_defaults(silent=True)
        self.app.inform.emit('[success] %s %s' % (_("Icons recolored:"), count))

        msgbox = FCMessageBox(parent=self.app.ui)
        msgbox.setWindowTitle(_("Restart application"))
        msgbox.setText(_("The icon color will be visible after restarting the application."))
        msgbox.setInformativeText(_("Restart now?"))
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Question)
        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_later = msgbox.addButton(_('Later'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        msgbox.setDefaultButton(bt_later)
        msgbox.exec()
        if msgbox.clickedButton() == bt_yes:
            self.app.on_app_restart()

    def on_joined_redraws(self, state):
        # the legacy (Matplotlib) canvas has no joined redraws
        collection = getattr(getattr(self.app, 'plotcanvas', None), 'shape_collection', None)
        if hasattr(collection, 'set_deferred'):
            collection.set_deferred(state)

    @staticmethod
    def handle_font_size(app, val):
        settings = QSettings("Open Source", "FlatCAM_EVO")
        settings.setValue('font_size', str(val))
        # This will write the setting to the platform specific storage.
        del settings

        restart_program(app=app)

    @staticmethod
    def handle_style(style):
        # set current style
        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        q_settings.setValue('style', style)

        QtWidgets.QApplication.setStyle(style)
        QtWidgets.QApplication.instance().setProperty('flatcam_style', style)

        # This will write the setting to the platform specific storage.
        del q_settings

    # Setting selection colors (left - right) handlers
    def on_sf_color_entry(self):
        self.app.options['global_sel_fill'] = self.sf_color_entry.get_value()[:7] + \
            _database_option(self.app, 'global_sel_fill')[7:9]

    def on_sl_color_entry(self):
        self.app.options['global_sel_line'] = self.sl_color_entry.get_value()[:7] + \
            _database_option(self.app, 'global_sel_line')[7:9]

    def on_left_right_alpha_changed(self, spinner_value):
        """
        Change the alpha level for the color of the selection box when selection is done left to right.
        Called on valueChanged of a FCSliderWithSpinner.

        :param spinner_value:   passed value within [0, 255]
        :type spinner_value:    int
        :return:                None
        :rtype:
        """

        # always 2 digits: hex() gave 1 digit for 1..15, a colour that the canvas cannot draw
        alpha = '%02x' % spinner_value
        self.app.options['global_sel_fill'] = _database_option(self.app, 'global_sel_fill')[:7] + alpha
        self.app.options['global_sel_line'] = _database_option(self.app, 'global_sel_line')[:7] + alpha
        # Save stores the colour fields, so they get the alpha too
        self.sf_color_entry.set_value(self.sf_color_entry.get_value()[:7] + alpha)
        self.sl_color_entry.set_value(self.sl_color_entry.get_value()[:7] + alpha)

    # Setting selection colors (right - left) handlers
    def on_alt_sf_color_entry(self):
        self.app.options['global_alt_sel_fill'] = self.alt_sf_color_entry.get_value()[:7] + \
                                                   _database_option(self.app, 'global_alt_sel_fill')[7:9]

    def on_alt_sl_color_entry(self):
        self.app.options['global_alt_sel_line'] = self.alt_sl_color_entry.get_value()[:7] + \
                                                   _database_option(self.app, 'global_alt_sel_line')[7:9]

    def on_right_left_alpha_changed(self, spinner_value):
        """
        Change the alpha level for the color of the selection box when selection is done right to left.
        Called on valueChanged of a FCSliderWithSpinner.

        :param spinner_value:   passed value within [0, 255]
        :type spinner_value:    int
        :return:                None
        :rtype:
        """

        # always 2 digits: hex() gave 1 digit for 1..15, a colour that the canvas cannot draw
        alpha = '%02x' % spinner_value
        self.app.options['global_alt_sel_fill'] = _database_option(self.app, 'global_alt_sel_fill')[:7] + alpha
        self.app.options['global_alt_sel_line'] = _database_option(self.app, 'global_alt_sel_line')[:7] + alpha
        # Save stores the colour fields, so they get the alpha too
        self.alt_sf_color_entry.set_value(self.alt_sf_color_entry.get_value()[:7] + alpha)
        self.alt_sl_color_entry.set_value(self.alt_sl_color_entry.get_value()[:7] + alpha)

    # Setting Editor colors
    def on_draw_color_entry(self):
        self.app.options['global_draw_color'] = self.draw_color_entry.get_value()

    def on_sel_draw_color_entry(self):
        self.app.options['global_sel_draw_color'] = self.sel_draw_color_entry.get_value()

    def on_proj_color_light_entry(self):
        self.app.options['global_proj_item_color_light'] = self.proj_color_light_entry.get_value()

    def on_proj_color_dis_light_entry(self):
        self.app.options['global_proj_item_dis_color_light'] = self.proj_color_dis_light_entry.get_value()

    def on_proj_color_dark_entry(self):
        self.app.options['global_proj_item_color_dark'] = self.proj_color_dark_entry.get_value()

    def on_proj_color_dis_dark_entry(self):
        self.app.options['global_proj_item_dis_color_dark'] = self.proj_color_dis_dark_entry.get_value()
