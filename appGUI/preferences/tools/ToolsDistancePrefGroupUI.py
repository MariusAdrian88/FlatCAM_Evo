from PyQt6 import QtGui

from appGUI.GUIElements import FCLabel, FCFrame, GLay, FCColorEntry, FCDoubleSpinner, FCSpinner, FCCheckBox
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolsDistancePrefGroupUI(OptionsGroupUI):
    def __init__(self, app, parent=None):

        super(ToolsDistancePrefGroupUI, self).__init__(self, parent=parent)

        self.setTitle(str(_("Distance Plugin")))
        self.decimals = app.decimals
        self.options = app.options
        icon = QtGui.QIcon(app.resource_location + '/set_colors64.png')

        # Dimension lines
        dim_label = FCLabel('%s' % _("Dimension Lines"), bold=True)
        dim_label.setToolTip(_("The look of the dimension lines and of their text."))
        self.layout.addWidget(dim_label)

        dim_frame = FCFrame()
        self.layout.addWidget(dim_frame)
        dim_grid = GLay(v_spacing=5, h_spacing=3)
        dim_frame.setLayout(dim_grid)

        self.line_width_label = FCLabel('%s:' % _("Line Width"))
        self.line_width_label.setToolTip(_("The thickness of the dimension lines, in screen pixels.\n"
                                           "The arrowheads grow with it."))
        self.line_width_entry = FCDoubleSpinner()
        self.line_width_entry.set_range(1.0, 10.0)
        self.line_width_entry.set_precision(1)
        self.line_width_entry.setSingleStep(0.5)
        dim_grid.addWidget(self.line_width_label, 0, 0)
        dim_grid.addWidget(self.line_width_entry, 0, 1)

        self.text_size_label = FCLabel('%s:' % _("Text Size"))
        self.text_size_label.setToolTip(_("The size of the text of the dimension lines, in points."))
        self.text_size_entry = FCSpinner()
        self.text_size_entry.set_range(6, 36)
        dim_grid.addWidget(self.text_size_label, 1, 0)
        dim_grid.addWidget(self.text_size_entry, 1, 1)

        self.text_bold_cb = FCCheckBox('%s' % _("Bold Text"))
        self.text_bold_cb.setToolTip(_("Draw the text of the dimension lines with a bold font."))
        dim_grid.addWidget(self.text_bold_cb, 2, 0, 1, 2)

        app_tip = _("The color of the dimension lines when the plugin is used outside the editors.")
        editor_tip = _("The color of the dimension lines when the plugin is used in an editor.\n"
                       "It contrasts with the color of the shapes in the editor.")

        # (title, tooltip, attribute prefix)
        sections = (
            (_("Light Theme"), _("Colors used on a light canvas."), 'light'),
            (_("Dark Theme"), _("Colors used on a dark canvas (the Dark theme or the Dark Canvas option)."), 'dark'),
        )
        for title, tip, key in sections:
            label = FCLabel('%s' % title, bold=True)
            label.setToolTip(tip)
            self.layout.addWidget(label)

            frame = FCFrame()
            self.layout.addWidget(frame)
            grid = GLay(v_spacing=5, h_spacing=3)
            frame.setLayout(grid)

            app_label = FCLabel('%s:' % _("App Color"))
            app_label.setToolTip(app_tip)
            app_entry = FCColorEntry(icon=icon)
            grid.addWidget(app_label, 0, 0)
            grid.addWidget(app_entry, 0, 1)

            editor_label = FCLabel('%s:' % _("Editor Color"))
            editor_label.setToolTip(editor_tip)
            editor_entry = FCColorEntry(icon=icon)
            grid.addWidget(editor_label, 1, 0)
            grid.addWidget(editor_entry, 1, 1)

            setattr(self, 'app_color_%s_entry' % key, app_entry)
            setattr(self, 'editor_color_%s_entry' % key, editor_entry)

        self.layout.addStretch()
