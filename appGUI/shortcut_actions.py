# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# License:  MIT Licence                                    #
# ##########################################################
"""
The table of the keyboard actions (appGUI/shortcuts.py): the contexts, and for each context its actions with the
factory key and the function that runs. A function gets the MainGUI (ui) and the key as MainGUI.keyPressEvent got it
(an int from the Project tab, a vispy key from the plot area). It returns STOP (or Stop(value), a value for
keyPressEvent) when the key is used up; with any other return value the key goes on to the next phase of the context
(the active tool of an editor).

An action without a function is read-only: its key is handled by code of its own (a drawing tool of an editor, a
plugin that takes the keys of the plot area, a text field); it is listed so that the F3 list and the check for a key in
use know it.

To add a shortcut: write its function and add a Shortcut(...) line to the list of its context.
"""

import webbrowser

from PyQt6 import QtWidgets

from appDatabase import _database_option
from appGUI.shortcuts import (Context, Shortcut, Stop, STOP, SOURCE_CANVAS, SOURCE_PROJECT, PHASE_TOOL, PHASE_LATE)

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
    app = 'app'
    return [
        Shortcut('app.undo', _("Undo"), app, 'Ctrl+Z', app_undo),
        Shortcut('app.redo', _("Redo"), app, 'Ctrl+Y', app_redo),
        Shortcut('app.redo_alt', '%s (%s)' % (_("Redo"), _("Alternative")), app, 'Ctrl+Shift+Z', app_redo),
        Shortcut('app.abort_tasks', _("Abort current task (gracefully)"), app, 'Ctrl+Alt+X', abort_all_tasks),
        Shortcut('app.save_project_as', _("Save Project As"), app, 'Ctrl+Shift+S',
                 lambda ui, key: ui.app.f_handlers.on_file_save_project_as()),
        # Ctrl
        Shortcut('app.select_all', _("Select All"), app, 'Ctrl+A', lambda ui, key: ui.app.on_selectall()),
        Shortcut('app.copy', _("Copy Obj"), app, 'Ctrl+C', app_copy),
        Shortcut('app.tools_database', _("Open Tools Database"), app, 'Ctrl+D',
                 lambda ui, key: ui.app.on_tools_database()),
        Shortcut('app.open_excellon', _("Open Excellon File"), app, 'Ctrl+E',
                 lambda ui, key: ui.app.f_handlers.on_file_open_excellon()),
        Shortcut('app.open_gerber', _("Open Gerber File"), app, 'Ctrl+G', app_open_gerber),
        Shortcut('app.import', _("Import File"), app, 'Ctrl+I', lambda ui, key: ui.app.project_selector.on_import()),
        Shortcut('app.distance', _("Distance Tool"), app, 'Ctrl+M', lambda ui, key: ui.app.distance_tool.run()),
        Shortcut('app.new_project', _("New Project"), app, 'Ctrl+N',
                 lambda ui, key: ui.app.f_handlers.on_file_new_click()),
        Shortcut('app.open_project', _("Open Project"), app, 'Ctrl+O',
                 lambda ui, key: ui.app.f_handlers.on_file_open_project()),
        Shortcut('app.print_pdf', _("Print (PDF)"), app, 'Ctrl+P',
                 lambda ui, key: ui.app.f_handlers.on_file_save_objects_pdf(use_thread=True)),
        Shortcut('app.save_project', _("Save Project"), app, 'Ctrl+S', app_save_project),
        Shortcut('app.toggle_plot_area', _("Toggle Plot Area"), app, 'Ctrl+F10',
                 lambda ui, key: ui.on_toggle_plotarea()),
        # Shift
        Shortcut('app.copy_name', _("Copy Obj_Name"), app, 'Shift+C', lambda ui, key: ui.app.on_copy_name()),
        Shortcut('app.code_editor', _("Toggle Code Editor"), app, 'Shift+E',
                 lambda ui, key: ui.app.on_toggle_code_editor()),
        Shortcut('app.locate', _("Locate in Object"), app, 'Shift+J',
                 lambda ui, key: ui.app.on_locate(obj=ui.app.collection.get_active())),
        Shortcut('app.distance_min', _("Distance Minimum Tool"), app, 'Shift+M',
                 lambda ui, key: ui.app.distance_min_tool.run()),
        Shortcut('app.preferences', _("Open Preferences Window"), app, 'Shift+P',
                 lambda ui, key: ui.app.on_preferences()),
        Shortcut('app.rotate_ccw', _("Rotate by 90 degree CCW"), app, 'Shift+R',
                 lambda ui, key: ui.app.on_rotate(
                     silent=True, preset=-float(_database_option(ui.app, 'tools_transform_rotate')))),
        Shortcut('app.run_script', _("Run a Script"), app, 'Shift+S',
                 lambda ui, key: ui.app.f_handlers.on_file_run_script()),
        Shortcut('app.skew_x', _("Skew on X axis"), app, 'Shift+X', lambda ui, key: ui.app.on_skewx()),
        Shortcut('app.skew_y', _("Skew on Y axis"), app, 'Shift+Y', lambda ui, key: ui.app.on_skewy()),
        # Alt
        Shortcut('app.enable_all', _("Enable all"), app, 'Alt+1', lambda ui, key: ui.app.enable_all_plots()),
        Shortcut('app.disable_all', _("Disable all"), app, 'Alt+2', lambda ui, key: ui.app.disable_all_plots()),
        Shortcut('app.enable_others', _("Enable Non-selected Objects"), app, 'Alt+3',
                 lambda ui, key: ui.app.enable_other_plots()),
        Shortcut('app.disable_others', _("Disable Non-selected Objects"), app, 'Alt+4',
                 lambda ui, key: ui.app.disable_other_plots()),
        Shortcut('app.compare', _("Compare"), app, 'Alt+5', plugin('compare_tool')),
        Shortcut('app.align', _("Align Objects"), app, 'Alt+A', plugin('align_objects_tool')),
        Shortcut('app.markers', _("Markers"), app, 'Alt+B', plugin('markers_tool')),
        Shortcut('app.calculators', _("Calculators"), app, 'Alt+C', plugin('calculator_tool')),
        Shortcut('app.drilling', _("Drilling"), app, 'Alt+D', plugin('drilling_tool')),
        Shortcut('app.extract', _("Extract"), app, 'Alt+E', plugin('extract_tool')),
        Shortcut('app.fiducials', _("Fiducials"), app, 'Alt+F', plugin('fiducial_tool')),
        Shortcut('app.invert', _("Invert Gerber"), app, 'Alt+G', plugin('invert_tool')),
        Shortcut('app.punch', _("Punch Gerber"), app, 'Alt+H', plugin('punch_tool')),
        Shortcut('app.isolation', _("Isolation"), app, 'Alt+I', plugin('isolation_tool')),
        Shortcut('app.copper_thieving', _("Copper Thieving"), app, 'Alt+J', plugin('copper_thieving_tool')),
        Shortcut('app.solder_paste', _("Solder Paste Dispensing"), app, 'Alt+K', plugin('paste_tool')),
        Shortcut('app.film', _("Film PCB"), app, 'Alt+L', plugin('film_tool')),
        Shortcut('app.milling', _("Milling"), app, 'Alt+M', plugin('milling_tool')),
        Shortcut('app.ncc', _("Non-Copper Clearing"), app, 'Alt+N', plugin('ncclear_tool')),
        Shortcut('app.optimal', _("Optimal"), app, 'Alt+O', plugin('optimal_tool')),
        Shortcut('app.paint', _("Paint Area"), app, 'Alt+P', plugin('paint_tool')),
        Shortcut('app.qrcode', _("QRCode"), app, 'Alt+Q', lambda ui, key: ui.app.qrcode_tool.run()),
        Shortcut('app.rules', _("Rules Check"), app, 'Alt+R', plugin('rules_tool')),
        Shortcut('app.view_source', _("View File Source"), app, 'Alt+S', lambda ui, key: ui.app.on_view_source()),
        Shortcut('app.transform', _("Transform"), app, 'Alt+T', plugin('transform_tool')),
        Shortcut('app.two_sided', _("2-Sided PCB"), app, 'Alt+U', plugin('dblsidedtool')),
        Shortcut('app.photo_carve', _("Photo Carve"), app, 'Alt+V', plugin('photocarve_tool')),
        Shortcut('app.subtract', _("Subtract"), app, 'Alt+W', plugin('sub_tool')),
        Shortcut('app.cutout', _("Cutout PCB"), app, 'Alt+X', plugin('cutout_tool')),
        Shortcut('app.follow', _("Follow"), app, 'Alt+Y', plugin('follow_tool')),
        Shortcut('app.panelize', _("Panelize PCB"), app, 'Alt+Z', plugin('panelize_tool')),
        Shortcut('app.full_screen', _("Toggle Full Screen"), app, 'Alt+F10',
                 lambda ui, key: ui.on_full_screen_toggled()),
        # no modifier
        Shortcut('app.manual', _("Open Online Manual"), app, 'F1', lambda ui, key: webbrowser.open(ui.app.manual_url)),
        Shortcut('app.rename', _("Rename Objects"), app, 'F2', app_rename, source=SOURCE_PROJECT),
        Shortcut('app.shortcut_list', _("Shortcuts List"), app, 'F3', lambda ui, key: ui.on_shortcut_list(),
                 f3_name=_("SHOW SHORTCUT LIST")),
        Shortcut('app.tutorials', _("Open Online Tutorials"), app, 'F4',
                 lambda ui, key: webbrowser.open(ui.app.video_url)),
        Shortcut('app.refresh', _("Refresh Plots"), app, 'F5', lambda ui, key: ui.app.plot_all()),
        Shortcut('app.project_tab', _("Switch to Project Tab"), app, '1',
                 lambda ui, key: ui.on_select_tab('project')),
        Shortcut('app.properties_tab', _("Switch to Selected Tab"), app, '2',
                 lambda ui, key: ui.on_select_tab('properties')),
        Shortcut('app.tool_tab', _("Switch to Tool Tab"), app, '3', lambda ui, key: ui.on_select_tab('tool')),
        Shortcut('app.delete_in_list', _("Alternate: Delete Tool"), app, 'Del', app_delete_in_list,
                 source=SOURCE_PROJECT),
        Shortcut('app.delete', _("Delete Object"), app, 'Del', app_delete, source=SOURCE_CANVAS),
        Shortcut('app.deselect_all', _("Deselects all objects"), app, 'Esc', app_escape),
        Shortcut('app.toggle_plot', _("En(Dis)able Obj Plot"), app, 'Space', app_toggle_plot),
        Shortcut('app.select_previous', _("Select the previous object"), app, 'Up', app_select_previous,
                 source=SOURCE_PROJECT),
        Shortcut('app.select_next', _("Select the next object"), app, 'Down', app_select_next,
                 source=SOURCE_PROJECT),
        Shortcut('app.new_gerber', _("New Gerber"), app, 'B', lambda ui, key: ui.app.app_obj.new_gerber_object()),
        Shortcut('app.new_document', _("New Document"), app, 'D',
                 lambda ui, key: ui.app.app_obj.new_document_object()),
        Shortcut('app.edit', _("Edit Object (if selected)"), app, 'E', lambda ui, key: ui.app.on_editing_start()),
        Shortcut('app.grid_snap', _("Grid On/Off"), app, 'G', lambda ui, key: ui.app.ui.grid_snap_btn.trigger()),
        Shortcut('app.jump', _("Jump to Coordinates"), app, 'J', lambda ui, key: ui.app.on_jump_to()),
        Shortcut('app.new_excellon', _("New Excellon"), app, 'L',
                 lambda ui, key: ui.app.app_obj.new_excellon_object()),
        Shortcut('app.move', _("Move Obj"), app, 'M', lambda ui, key: ui.app.move_tool.toggle()),
        Shortcut('app.new_geometry', _("New Geometry"), app, 'N',
                 lambda ui, key: ui.app.app_obj.new_geometry_object()),
        Shortcut('app.set_origin', _("Set Origin"), app, 'O', lambda ui, key: ui.app.on_set_origin()),
        Shortcut('app.properties', _("Open Properties Plugin"), app, 'P', plugin('report_tool')),
        Shortcut('app.rotate_cw', _("Rotate by 90 degree CW"), app, 'R',
                 lambda ui, key: ui.app.on_rotate(silent=True,
                                                  preset=_database_option(ui.app, 'tools_transform_rotate'))),
        Shortcut('app.shell', _("Shell Toggle"), app, 'S', lambda ui, key: ui.toggle_shell_ui()),
        Shortcut('app.add_tool', _("Add a Tool (when in Geometry Selected Tab or in Tools NCC or Tools Paint)"),
                 app, 'T', app_add_tool),
        Shortcut('app.zoom_fit', _("Zoom Fit"), app, 'V', lambda ui, key: ui.app.on_zoom_fit()),
        Shortcut('app.flip_x', _("Flip on X_axis"), app, 'X', lambda ui, key: ui.app.on_flipx()),
        Shortcut('app.flip_y', _("Flip on Y_axis"), app, 'Y', lambda ui, key: ui.app.on_flipy()),
        Shortcut('app.zoom_in', _("Zoom In"), app, '=',
                 lambda ui, key: ui.app.plotcanvas.zoom(1 / _database_option(ui.app, 'global_zoom_ratio'),
                                                        ui.app.mouse_pos)),
        Shortcut('app.zoom_out', _("Zoom Out"), app, '-',
                 lambda ui, key: ui.app.plotcanvas.zoom(_database_option(ui.app, 'global_zoom_ratio'),
                                                        ui.app.mouse_pos)),
        Shortcut('app.notebook', _("(left to Key_1)Toggle Notebook Area (Left Side)"), app, '`',
                 lambda ui, key: ui.on_toggle_notebook()),
    ]


def plugin(attribute):
    """The action that opens or closes a plugin (its run(toggle=True))."""
    return lambda ui, key: getattr(ui.app, attribute).run(toggle=True)


def current_tab_name(ui):
    current_tab = ui.plot_tab_area.currentWidget()
    return current_tab.objectName() if current_tab is not None else ''


def app_undo(ui, key):
    if not ui.app_focus_in_text():
        ui.app.history.undo()


def app_redo(ui, key):
    if not ui.app_focus_in_text():
        ui.app.history.redo()


def abort_all_tasks(ui, key):
    ui.app.abort_all_tasks()


def app_copy(ui, key):
    if current_tab_name(ui) == 'database_tab':
        # Tools DB saved, update flag
        ui.app.tools_db_changed_flag = True
        ui.app.tools_db_tab.on_tool_copy()
        return
    ui.app.on_copy_command()


def app_open_gerber(ui, key):
    if 'editor' in current_tab_name(ui).lower():
        ui.goto_text_line()
    else:
        ui.app.f_handlers.on_file_open_gerber()


def app_save_project(ui, key):
    if current_tab_name(ui) == 'database_tab':
        ui.app.tools_db_tab.on_save_tools_db()
        return
    ui.app.f_handlers.on_file_save_project()


def app_rename(ui, key):
    ui.app.collection.view.edit(ui.app.collection.view.currentIndex())


def app_delete_in_list(ui, key):
    # Del in the Project tab; in the Tools Database it deletes the tool
    if current_tab_name(ui) == 'database_tab':
        # Tools DB saved, update flag
        ui.app.tools_db_changed_flag = True
        ui.app.tools_db_tab.on_tool_delete()
        return
    ui.app.on_delete_keypress()


def app_delete(ui, key):
    # Del on the plot area: delete via the application to ensure cleanup of the appGUI
    active = ui.app.collection.get_active()
    if active:
        active.app.on_delete()


def app_escape(ui, key):
    ui.app.on_deselect_all()

    # if in full screen, exit to normal view
    if ui.isFullScreen():
        ui.on_full_screen_toggled(disable=True)

    # try to disconnect the slot from Set Origin
    try:
        ui.app.plotcanvas.graph_event_disconnect('mouse_press', ui.app.on_set_zero_click)
    except TypeError:
        pass
    ui.app.inform.emit("")


def app_toggle_plot(ui, key):
    for select in ui.app.collection.get_selected():
        try:
            select.ui.plot_cb.toggle()
        except RuntimeError:
            # the UI of the object was deleted by Qt; rebuild it, then change the option and plot
            # directly because not all objects connect the plot checkbox in set_ui()
            select.set_ui(select.ui_type(app=ui.app))
            select.muted_ui = True
            select.obj_options['plot'] = not select.obj_options['plot']
            select.muted_ui = False
            kind = ui.app.options.get("cncjob_plot_kind", "all") if select.kind == 'cncjob' else None
            if not select.show_plotted_shapes(select.obj_options['plot'], kind=kind):
                select.plot(kind=kind) if kind else select.plot()
        QtWidgets.QApplication.processEvents()
    ui.app.collection.update_view()
    ui.app.delete_selection_shape()


def _select_neighbour(ui, step):
    """Up and Down in the Project tab: select the object above or below the active one (around the ends)."""
    # make sure it works only for the Project Tab who is an instance of EventSensitiveListView
    from appObjects.ObjectCollection import EventSensitiveListView
    if isinstance(QtWidgets.QApplication.focusWidget(), EventSensitiveListView):
        active = ui.app.collection.get_active()
        names_list = ui.app.collection.get_names()
        ui.app.collection.set_all_inactive()
        if active is None:
            return
        index = names_list.index(active.obj_options['name'])
        ui.app.collection.set_active(names_list[(index + step) % len(names_list)])


def app_select_previous(ui, key):
    _select_neighbour(ui, -1)


def app_select_next(ui, key):
    _select_neighbour(ui, 1)


def app_add_tool(ui, key):
    if current_tab_name(ui) == 'database_tab':
        # Tools DB saved, update flag
        ui.app.tools_db_changed_flag = True
        ui.app.tools_db_tab.on_tool_add()
        return
    ui.app.on_tool_add_keypress()


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
