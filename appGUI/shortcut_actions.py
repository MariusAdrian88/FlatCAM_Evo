# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# License:  MIT Licence                                    #
# ##########################################################
"""
The table of the keyboard actions (appGUI/shortcuts.py): the contexts, and for each context its actions with the
factory key and the function that runs. A function gets the MainGUI (ui) and the key as MainGUI.keyPressEvent got it
(an int from the Project tab, a vispy key from the plot area); it returns None when the key goes on to the next phase
of the context (the active tool of an editor), STOP when the key is used up, or a value for keyPressEvent.

An action without a function is read-only: its key is handled by code of its own (a drawing tool of an editor, a
plugin that takes the keys of the plot area, a text field); it is listed so that the F3 list and the check for a key in
use know it.

To add a shortcut: write its function and add a Shortcut(...) line to the list of its context.
"""

from appGUI.shortcuts import (Context, Shortcut, STOP, SOURCE_CANVAS, SOURCE_PROJECT, PHASE_TOOL, PHASE_LATE)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def contexts():
    drawing = _("while drawing")
    return [
        # the keys of the main window that come first in every context (MainGUI.eventFilter)
        Context('global', _("Everywhere")),
        Context('app', _("Application")),
        Context('geo_editor', _("Geometry Editor"), keypad_plain=True, tool_editor='geo_editor',
                select_button='geo_select_btn', passthrough=('',)),
        Context('grb_editor', _("Gerber Editor"), keypad_plain=True, tool_editor='grb_editor',
                select_button='grb_select_btn', passthrough=('',)),
        Context('exc_editor', _("Excellon Editor"), keypad_plain=True, tool_editor='exc_editor',
                select_button='select_drill_btn', passthrough=('', 'Ctrl')),
        Context('gcode_editor', _("GCode Editor")),
        Context('measurement', _("Distance Tool")),
        Context('geometry', _("Exclusion areas")),
        Context('qrcode_tool', _("QRCode")),
        Context('copper_thieving_tool', _("Copper Thieving")),
        Context('2_sided_tool', _("2-Sided")),
        Context('fiducials_tool', _("Fiducials")),
        Context('markers_tool', _("Markers")),
        # read-only: the keys of the drawing tools of the editors (their on_key()), of the plugins that take the keys
        # of the plot area, and of the text fields
        Context('geo_drawing', '%s: %s' % (_("Geometry Editor"), drawing), editable=False),
        Context('grb_drawing', '%s: %s' % (_("Gerber Editor"), drawing), editable=False),
        Context('exc_drawing', '%s: %s' % (_("Excellon Editor"), drawing), editable=False),
        Context('cutout_tool', _("Cutout"), editable=False),
        Context('align_tool', _("Align Objects"), editable=False),
        Context('levelling_tool', _("Levelling"), editable=False),
        Context('selection_tools', _("Area selection"), editable=False),
        Context('move_tool', _("Move"), editable=False),
        Context('code_editor', _("Code Editor"), editable=False, text=True),
        Context('tcl_shell', _("TCL Shell"), editable=False, text=True),
        Context('preferences', _("Preferences"), editable=False, text=True),
    ]


def actions():
    return (global_actions() + app_actions() + geo_editor_actions() + grb_editor_actions() + exc_editor_actions() +
            plugin_actions() + read_only_actions())


# ---------------------------------------------------------------------------------------------------------------------
# Everywhere (MainGUI.eventFilter): before the plot area, an editor or a plugin, unless the focus is in a text field
# ---------------------------------------------------------------------------------------------------------------------
def global_actions():
    return []


# ---------------------------------------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------------------------------------
def app_actions():
    return []


# ---------------------------------------------------------------------------------------------------------------------
# Geometry Editor
# ---------------------------------------------------------------------------------------------------------------------
def geo_editor_actions():
    return []


# ---------------------------------------------------------------------------------------------------------------------
# Gerber Editor
# ---------------------------------------------------------------------------------------------------------------------
def grb_editor_actions():
    return []


# ---------------------------------------------------------------------------------------------------------------------
# Excellon Editor
# ---------------------------------------------------------------------------------------------------------------------
def exc_editor_actions():
    return []


# ---------------------------------------------------------------------------------------------------------------------
# The plugins that MainGUI.keyPressEvent serves (app.call_source)
# ---------------------------------------------------------------------------------------------------------------------
def plugin_actions():
    return []


# ---------------------------------------------------------------------------------------------------------------------
# Read-only: keys handled by code of their own
# ---------------------------------------------------------------------------------------------------------------------
def read_only_actions():
    jump = _("Jump to Location (x, y)")
    cursor_data = _("Toggle the cursor data")
    end_selection = _("End the selection")
    return [
        # the drawing tools of the Geometry Editor (appGeoEditor.py, on_key() of the tools)
        Shortcut('geo_drawing.arc_direction', _("Within Add Arc will toogle the ARC direction: CW or CCW"),
                 'geo_drawing', 'D'),
        Shortcut('geo_drawing.arc_mode', _("Within Add Arc will cycle through the ARC modes"), 'geo_drawing', 'M'),
        Shortcut('geo_drawing.corner_forward',
                 _("Within Draw a Path / Polygon will cycle forward through the Corner Modes"), 'geo_drawing', 'T'),
        Shortcut('geo_drawing.corner_backward',
                 _("Within Draw a Path / Polygon will cycle backward through the Corner Modes"), 'geo_drawing', 'R'),
        Shortcut('geo_drawing.remove_point', _("Remove the last point"), 'geo_drawing', 'Backspace', f3=False),
        Shortcut('geo_drawing.cursor_data', cursor_data, 'geo_drawing', 'C', f3=False),
        Shortcut('geo_drawing.jump', jump, 'geo_drawing', 'J', f3=False),

        # the drawing tools of the Gerber Editor (appGerberEditor.py and grb_plugins/)
        Shortcut('grb_drawing.bend_backward', _("Within Track & Region Tools will cycle in REVERSE the bend modes"),
                 'grb_drawing', 'R'),
        Shortcut('grb_drawing.bend_forward', _("Within Track & Region Tools will cycle FORWARD the bend modes"),
                 'grb_drawing', 'T'),
        Shortcut('grb_drawing.array_direction', _("Toggle array direction"), 'grb_drawing', 'Space'),
        Shortcut('grb_drawing.remove_point', _("Remove the last point"), 'grb_drawing', 'Backspace', f3=False),
        Shortcut('grb_drawing.cursor_data', cursor_data, 'grb_drawing', 'C', f3=False),
        Shortcut('grb_drawing.grid_snap', _("Grid On/Off"), 'grb_drawing', 'G', f3=False),
        Shortcut('grb_drawing.jump', jump, 'grb_drawing', 'J', f3=False),

        # the drawing tools of the Excellon Editor (appExcEditor.py)
        Shortcut('exc_drawing.slot_direction', _("Toggle Slot direction"), 'exc_drawing', 'Space'),
        Shortcut('exc_drawing.array_direction', _("Toggle array direction"), 'exc_drawing', 'Ctrl+Space'),
        Shortcut('exc_drawing.cursor_data', cursor_data, 'exc_drawing', 'C', f3=False),
        Shortcut('exc_drawing.jump', jump, 'exc_drawing', 'J', f3=False),

        # the plugins that take the keys of the plot area while they wait for clicks
        Shortcut('cutout_tool.delete_gap', _("Delete a manual gap"), 'cutout_tool', 'Del', f3=False),
        Shortcut('cutout_tool.cancel', _("Cancel"), 'cutout_tool', 'Esc', f3=False),
        Shortcut('cutout_tool.grid_snap', _("Grid On/Off"), 'cutout_tool', 'G', f3=False),
        Shortcut('cutout_tool.jump', jump, 'cutout_tool', 'J', f3=False),
        Shortcut('align_tool.cancel', _("Cancel"), 'align_tool', 'Esc', f3=False),
        Shortcut('align_tool.remove_point', _("Remove the last point"), 'align_tool', 'Backspace', f3=False),
        Shortcut('align_tool.apply', _("Apply"), 'align_tool', 'Return', f3=False),
        Shortcut('levelling_tool.finish', _("End the selection"), 'levelling_tool', 'Esc', f3=False),
        Shortcut('levelling_tool.grid_snap', _("Grid On/Off"), 'levelling_tool', 'G', f3=False),
        Shortcut('levelling_tool.jump', jump, 'levelling_tool', 'J', f3=False),
        # Isolation, NCC, Paint, Follow, Punch Gerber: area or polygon selection
        Shortcut('selection_tools.end', end_selection, 'selection_tools', 'Esc', f3=False),
        Shortcut('move_tool.cancel', _("Cancel"), 'move_tool', 'Esc', f3=False),

        # the text fields
        Shortcut('code_editor.find', _("Find"), 'code_editor', 'Ctrl+F', f3=False),
        Shortcut('code_editor.replace', _("Replace"), 'code_editor', 'Ctrl+H', f3=False),
        Shortcut('code_editor.find_next', _("Find next"), 'code_editor', 'F3', f3=False),
        Shortcut('code_editor.find_previous', _("Find previous"), 'code_editor', 'Shift+F3', f3=False),
        Shortcut('code_editor.duplicate', _("Duplicate"), 'code_editor', 'Ctrl+D', f3=False),
        Shortcut('code_editor.comment', _("Comment or uncomment the lines"), 'code_editor', 'Ctrl+/', f3=False),
        Shortcut('code_editor.zoom_in', _("Zoom In"), 'code_editor', 'Ctrl+=', f3=False),
        Shortcut('code_editor.zoom_out', _("Zoom Out"), 'code_editor', 'Ctrl+-', f3=False),
        Shortcut('tcl_shell.paste_special',
                 _("Paste Special. Will convert a Windows path style to the one required in Tcl Shell"),
                 'tcl_shell', 'Ctrl+Shift+V'),
        Shortcut('preferences.apply', _("Apply"), 'preferences', 'Ctrl+S', f3=False),
        Shortcut('preferences.close', _("Close"), 'preferences', 'Esc', f3=False),
    ]
