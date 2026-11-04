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

from PyQt6 import QtGui, QtWidgets

from appDatabase import _database_option
from appGUI.GUIElements import FCMessageBox, FCInputDoubleSpinner
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
    geo = 'geo_editor'
    return [
        Shortcut('geo_editor.undo', _("Undo"), geo, 'Ctrl+Z', editor_undo('geo_editor')),
        Shortcut('geo_editor.redo', _("Redo"), geo, 'Ctrl+Y', editor_redo('geo_editor')),
        Shortcut('geo_editor.redo_alt', '%s (%s)' % (_("Redo"), _("Alternative")), geo, 'Ctrl+Shift+Z',
                 editor_redo('geo_editor')),
        # Ctrl
        Shortcut('geo_editor.exit', _("Save Object and Exit Editor"), geo, 'Ctrl+S', exit_editor),
        Shortcut('geo_editor.distance', _("Distance Tool"), geo, 'Ctrl+M', stop(lambda ui: ui.app.distance_tool.run())),
        Shortcut('geo_editor.cut', _("Polygon Cut Tool"), geo, 'Ctrl+X', geo_cut),
        # Shift
        Shortcut('geo_editor.distance_min', _("Distance Minimum Tool"), geo, 'Shift+M',
                 stop(lambda ui: ui.app.distance_min_tool.run())),
        Shortcut('geo_editor.skew_x', _("Skew shape on X axis"), geo, 'Shift+X',
                 stop(lambda ui: ui.app.geo_editor.transform_tool.on_skewx_key())),
        Shortcut('geo_editor.skew_y', _("Skew shape on Y axis"), geo, 'Shift+Y',
                 stop(lambda ui: ui.app.geo_editor.transform_tool.on_skewy_key())),
        # Alt
        Shortcut('geo_editor.transform', _("Editor Transformation Tool"), geo, 'Alt+R',
                 stop(lambda ui: ui.app.geo_editor.select_tool('transform'))),
        Shortcut('geo_editor.offset_x', _("Offset shape on X axis"), geo, 'Alt+X',
                 stop(lambda ui: ui.app.geo_editor.transform_tool.on_offx_key())),
        Shortcut('geo_editor.offset_y', _("Offset shape on Y axis"), geo, 'Alt+Y',
                 stop(lambda ui: ui.app.geo_editor.transform_tool.on_offy_key())),
        # no modifier: also with a drawing tool active (the tool gets the key after them)
        Shortcut('geo_editor.notebook', _("(left to Key_1)Toggle Notebook Area (Left Side)"), geo, '`',
                 lambda ui, key: ui.on_toggle_notebook()),
        Shortcut('geo_editor.finish', _("Finish drawing for certain tools"), geo, 'Return', geo_finish),
        Shortcut('geo_editor.abort', _("Abort and return to Select"), geo, 'Esc', geo_abort),
        Shortcut('geo_editor.delete', _("Delete"), geo, 'Del', geo_delete),
        Shortcut('geo_editor.rotate', _("Rotate Geometry"), geo, 'Space',
                 lambda ui, key: ui.app.geo_editor.transform_tool.on_rotate_key()),
        Shortcut('geo_editor.zoom_out', _("Zoom Out"), geo, '-', editor_zoom('geo_editor', out=True)),
        Shortcut('geo_editor.zoom_in', _("Zoom In"), geo, '=', editor_zoom('geo_editor', out=False)),
        Shortcut('geo_editor.grid_snap', _("Grid On/Off"), geo, 'G', geo_grid_snap),
        Shortcut('geo_editor.corner_snap', _("Toggle Corner Snap"), geo, 'K',
                 lambda ui, key: ui.app.geo_editor.on_corner_snap()),
        Shortcut('geo_editor.zoom_fit', _("Zoom Fit"), geo, 'V', lambda ui, key: ui.app.on_zoom_fit()),
        # no modifier, when no drawing tool is active
        Shortcut('geo_editor.arc', _("Draw an Arc"), geo, 'A', select_tool('geo_editor', 'arc'), phase=PHASE_TOOL),
        Shortcut('geo_editor.buffer', _("Buffer Tool"), geo, 'B', select_tool('geo_editor', 'buffer'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.copy', _("Copy Geo Item"), geo, 'C', select_tool('geo_editor', 'copy'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.intersection', _("Polygon Intersection Tool"), geo, 'E',
                 geo_selection_command('intersection', _("Please select geometry items \n"
                                                         "on which to perform Intersection Tool.")),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.paint', _("Geo Paint Tool"), geo, 'I', select_tool('geo_editor', 'paint'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.jump', _("Jump to Location (x, y)"), geo, 'J', lambda ui, key: ui.app.on_jump_to(),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.move', _("Move Geo Item"), geo, 'M', select_tool('geo_editor', 'move'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.polygon', _("Draw a Polygon"), geo, 'N', select_tool('geo_editor', 'polygon'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.circle', _("Draw a Circle"), geo, 'O', select_tool('geo_editor', 'circle'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.path', _("Draw a Path"), geo, 'P', select_tool('geo_editor', 'path'), phase=PHASE_TOOL),
        Shortcut('geo_editor.rectangle', _("Draw Rectangle"), geo, 'R', select_tool('geo_editor', 'rectangle'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.subtract', _("Polygon Subtraction Tool"), geo, 'S',
                 geo_selection_command('subtract', _("Please select geometry items \n"
                                                     "on which to perform Subtraction.")),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.text', _("Add Text Tool"), geo, 'T', select_tool('geo_editor', 'text'),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.union', _("Polygon Union Tool"), geo, 'U',
                 geo_selection_command('union', _("Please select geometry items \n"
                                                  "on which to perform union.")),
                 phase=PHASE_TOOL),
        Shortcut('geo_editor.flip_x', _("Flip shape on X axis"), geo, 'X',
                 stop(lambda ui: ui.app.geo_editor.transform_tool.on_flipx()), phase=PHASE_TOOL),
        Shortcut('geo_editor.flip_y', _("Flip shape on Y axis"), geo, 'Y',
                 stop(lambda ui: ui.app.geo_editor.transform_tool.on_flipy()), phase=PHASE_TOOL),
        # after the drawing tool got the key
        Shortcut('geo_editor.shortcut_list', _("Shortcuts List"), geo, 'F3', lambda ui, key: ui.on_shortcut_list(),
                 source=SOURCE_CANVAS, phase=PHASE_LATE),
    ]


def stop(function):
    """An action that runs function(ui) and uses up the key."""
    def action(ui, key):
        function(ui)
        return STOP
    return action


def select_tool(editor, tool):
    return lambda ui, key: getattr(ui.app, editor).select_tool(tool)


def focus_in_text():
    """True if the keyboard focus is in a text or number field (Ctrl+Z belongs to the field then)."""
    from appGUI.MainGUI import MainGUI
    return MainGUI.app_focus_in_text()


def editor_undo(editor):
    def action(ui, key):
        if not focus_in_text():
            getattr(ui.app, editor).undo()
        return STOP
    return action


def editor_redo(editor):
    def action(ui, key):
        if not focus_in_text():
            getattr(ui.app, editor).redo()
        return STOP
    return action


def editor_zoom(editor, out):
    def action(ui, key=None):
        ratio = _database_option(ui.app, 'global_zoom_ratio')
        draw = getattr(ui.app, editor)
        ui.app.plotcanvas.zoom(ratio if out else 1 / ratio, [draw.snap_x, draw.snap_y])
    return action


def exit_editor(ui, key):
    # save (update) the edited object and return to the App
    ui.app.on_editing_finished()
    return STOP


def warning_box(ui, msg):
    messagebox = FCMessageBox(parent=ui)
    title = _("Warning")
    messagebox.setWindowTitle(title)  # taskbar still shows it
    messagebox.setWindowIcon(QtGui.QIcon(ui.app.resource_location + '/app128.png'))
    messagebox.setText('<b>%s</b>' % title)
    messagebox.setInformativeText(msg)
    messagebox.setIcon(QtWidgets.QMessageBox.Icon.Warning)
    messagebox.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
    messagebox.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Ok)
    messagebox.exec()


def geo_cut(ui, key):
    if ui.app.geo_editor.get_selected() is not None:
        ui.app.geo_editor.cutpath()
    else:
        warning_box(ui, _('Please first select a geometry item to be cut\n'
                          'then select the geometry item that will be cut\n'
                          'out of the first item. In the end press ~X~ key or\n'
                          'the toolbar button.'))
    return STOP


def geo_selection_command(method, msg):
    """Union, Intersection, Subtraction of the selected shapes; a message when nothing is selected."""
    def action(ui, key):
        if ui.app.geo_editor.get_selected() is not None:
            getattr(ui.app.geo_editor, method)()
        else:
            warning_box(ui, msg)
    return action


def geo_finish(ui, key):
    """Finish the current action. Use with tools that do not complete automatically, like a polygon or path."""
    from appEditors.appGeoEditor import AppGeoEditor, FCShapeTool
    editor = ui.app.geo_editor
    if AppGeoEditor.reject_busy(editor):
        return Stop('fail')
    if isinstance(editor.active_tool, FCShapeTool):
        tool = editor.active_tool
        if tool.name in ('path', 'circle', 'rectangle', 'move', 'copy'):
            if not getattr(tool, 'points', ()) and getattr(tool, 'origin', None) is None:
                ui.app.inform.emit('[WARNING_NOTCL] %s' %
                                   _("Click on the first construction or reference point before Enter."))
                return Stop('fail')
            if tool.name != 'path' or tool.path_tool.length != 0.0:
                response = tool.on_key(key)
                if response is not None and response != 'fail':
                    ui.app.inform.emit(response)
                return Stop(response)
        if editor.active_tool.name == 'rotate':
            if editor.active_tool.make() == 'fail':
                return Stop('fail')

            if editor.active_tool.complete:
                if editor.on_shape_complete() == 'fail':
                    return Stop('fail')
                ui.app.inform.emit('[success] %s' % _("Done."))
            # automatically make the selection tool active after completing current action
            editor.select_tool('select')
            return STOP
        else:
            if editor.active_tool.name == 'polygon' and editor.active_tool.polygon_tool.length != 0.0:
                pass
            else:
                if editor.active_tool.click(editor.snap(editor.x, editor.y)) == 'fail':
                    return Stop('fail')

                if editor.active_tool.make() == 'fail':
                    return Stop('fail')

                if editor.active_tool.complete:
                    if editor.on_shape_complete() == 'fail':
                        return Stop('fail')
                    ui.app.inform.emit('[success] %s' % _("Done."))
                # automatically make the selection tool active after completing current action
                editor.select_tool('select')


def geo_abort(ui, key):
    ui.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
    ui.app.geo_editor.delete_utility_geometry()
    if ui.app.geo_editor.active_tool.name not in ('path', 'circle', 'rectangle', 'move', 'copy'):
        ui.app.geo_editor.active_tool.clean_up()
    ui.app.geo_editor.select_tool('select')
    return STOP


def geo_delete(ui, key):
    ui.app.geo_editor.delete_selected()
    ui.app.geo_editor.plot_all()


def geo_grid_snap(ui, key):
    ui.app.ui.grid_snap_btn.trigger()
    # make sure that the cursor shape is enabled/disabled, too
    ui.app.app_cursor.enabled = ui.app.geo_editor.editor_options['grid_snap'] is True


# ---------------------------------------------------------------------------------------------------------------------
# Gerber Editor
# ---------------------------------------------------------------------------------------------------------------------
def grb_editor_actions():
    grb = 'grb_editor'
    return [
        Shortcut('grb_editor.undo', _("Undo"), grb, 'Ctrl+Z', editor_undo('grb_editor')),
        Shortcut('grb_editor.redo', _("Redo"), grb, 'Ctrl+Y', editor_redo('grb_editor')),
        Shortcut('grb_editor.redo_alt', '%s (%s)' % (_("Redo"), _("Alternative")), grb, 'Ctrl+Shift+Z',
                 editor_redo('grb_editor')),
        # Ctrl
        Shortcut('grb_editor.select_all', _("Select All"), grb, 'Ctrl+A',
                 stop(lambda ui: ui.app.grb_editor.ui.apertures_table.selectAll())),
        Shortcut('grb_editor.eraser', _("Eraser Tool"), grb, 'Ctrl+E', stop(lambda ui: ui.app.grb_editor.on_eraser())),
        Shortcut('grb_editor.exit', _("Save Object and Exit Editor"), grb, 'Ctrl+S', exit_editor),
        Shortcut('grb_editor.distance', _("Distance Tool"), grb, 'Ctrl+M', stop(lambda ui: ui.app.distance_tool.run())),
        # Shift
        Shortcut('grb_editor.distance_min', _("Distance Minimum Tool"), grb, 'Shift+M',
                 stop(lambda ui: ui.app.distance_min_tool.run())),
        # Alt
        Shortcut('grb_editor.mark_area', _("Mark Area Tool"), grb, 'Alt+A',
                 stop(lambda ui: ui.app.grb_editor.on_markarea())),
        Shortcut('grb_editor.poligonize', _("Poligonize Tool"), grb, 'Alt+N',
                 stop(lambda ui: ui.app.grb_editor.on_poligonize())),
        Shortcut('grb_editor.transform', _("Transformation Tool"), grb, 'Alt+R',
                 stop(lambda ui: ui.app.grb_editor.on_transform())),
        # no modifier: also with a drawing tool active
        Shortcut('grb_editor.abort', _("Abort and return to Select"), grb, 'Esc', grb_abort),
        Shortcut('grb_editor.delete', _("Delete"), grb, 'Del', grb_delete, source=SOURCE_CANVAS),
        Shortcut('grb_editor.delete_aperture', _("Alternate: Delete Apertures"), grb, 'Del',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.on_aperture_delete()),
                 source=SOURCE_PROJECT),
        Shortcut('grb_editor.zoom_out', _("Zoom Out"), grb, '-',
                 shortcut_flag('grb_editor', editor_zoom('grb_editor', out=True))),
        Shortcut('grb_editor.zoom_in', _("Zoom In"), grb, '=',
                 shortcut_flag('grb_editor', editor_zoom('grb_editor', out=False))),
        Shortcut('grb_editor.notebook', _("(left to Key_1)Toggle Notebook Area (Left Side)"), grb, '`',
                 shortcut_flag('grb_editor', lambda ui: ui.on_toggle_notebook())),
        # no modifier, when no drawing tool is active
        Shortcut('grb_editor.rotate', _("Rotate"), grb, 'Space',
                 lambda ui, key: ui.app.grb_editor.transform_tool.on_rotate_key(), phase=PHASE_TOOL),
        Shortcut('grb_editor.pad_array', _("Add Pad Array"), grb, 'A', grb_add_at_cursor('array', 'add_pad_ar_btn'),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.buffer', _("Buffer"), grb, 'B',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.select_tool('buffer')), phase=PHASE_TOOL),
        Shortcut('grb_editor.copy', _("Copy"), grb, 'C', editor_from_selection('grb_editor', 'copy', 'aperture_copy_btn',
                                                                               _("Click on target point.")),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.disc', _("Add Disc"), grb, 'D',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.select_tool('disc')), phase=PHASE_TOOL),
        Shortcut('grb_editor.semidisc', _("Add SemiDisc"), grb, 'E',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.select_tool('semidisc')),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.grid_snap', _("Grid On/Off"), grb, 'G', editor_grid_snap('grb_editor'),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.jump', _("Jump to Location (x, y)"), grb, 'J', lambda ui, key: ui.app.on_jump_to(),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.corner_snap', _("Toggle Corner Snap"), grb, 'K',
                 shortcut_flag('grb_editor', lambda ui: ui.app.ui.corner_snap_btn.trigger()), phase=PHASE_TOOL),
        Shortcut('grb_editor.move', _("Move"), grb, 'M', editor_from_selection('grb_editor', 'move', 'aperture_move_btn',
                                                                               _("Click on target point.")),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.region', _("Add Region"), grb, 'N',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.select_tool('region')), phase=PHASE_TOOL),
        Shortcut('grb_editor.pad', _("Add Pad"), grb, 'P', grb_add_at_cursor('pad', 'add_pad_ar_btn'),
                 phase=PHASE_TOOL),
        Shortcut('grb_editor.scale', _("Scale"), grb, 'S',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.select_tool('scale')), phase=PHASE_TOOL),
        Shortcut('grb_editor.track', _("Add Track"), grb, 'T',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.select_tool('track')), phase=PHASE_TOOL),
        Shortcut('grb_editor.zoom_fit', _("Zoom Fit"), grb, 'V',
                 shortcut_flag('grb_editor', lambda ui: ui.app.grb_editor.on_zoom_fit()), phase=PHASE_TOOL),
        # after the drawing tool got the key
        Shortcut('grb_editor.shortcut_list', _("Shortcuts List"), grb, 'F3',
                 stop(lambda ui: ui.on_shortcut_list()), phase=PHASE_LATE),
    ]


def shortcut_flag(editor, function):
    """
    An action of the Gerber or Excellon Editor that tells the editor that it comes from a key
    (launched_from_shortcuts), runs function(ui) and uses up the key.
    """
    def action(ui, key):
        getattr(ui.app, editor).launched_from_shortcuts = True
        function(ui)
        return STOP
    return action


def editor_grid_snap(editor):
    def action(ui, key):
        draw = getattr(ui.app, editor)
        draw.launched_from_shortcuts = True
        # make sure that the cursor shape is enabled/disabled, too
        ui.app.app_cursor.enabled = draw.editor_options['grid_snap'] is not True
        ui.app.ui.grid_snap_btn.trigger()
        return STOP
    return action


def editor_from_selection(editor, tool, button, msg):
    """Copy and Move of the Gerber and Excellon Editors: start the tool at the cursor if something is selected."""
    def action(ui, key):
        draw = getattr(ui.app, editor)
        draw.launched_from_shortcuts = True
        if draw.selected:
            ui.app.inform.emit(msg)
            getattr(ui.app.ui, button).setChecked(True)
            draw.on_tool_select(tool)
            if draw.active_tool is not None:
                draw.active_tool.set_origin((draw.snap_x, draw.snap_y))
        else:
            ui.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Nothing selected."))
        return STOP
    return action


def editor_add_at_cursor(editor, tool, button, msg):
    """Add a pad or a drill (or an array) of the Gerber and Excellon Editors at the mouse position."""
    def action(ui, key):
        draw = getattr(ui.app, editor)
        draw.launched_from_shortcuts = True
        ui.app.inform.emit(msg)
        getattr(ui.app.ui, button).setChecked(True)
        draw.x = ui.app.mouse_pos[0]
        draw.y = ui.app.mouse_pos[1]
        draw.select_tool(tool)
        return STOP
    return action


def grb_add_at_cursor(tool, button):
    return editor_add_at_cursor('grb_editor', tool, button, _("Click on target point."))


def grb_abort(ui, key):
    ui.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
    ui.app.grb_editor.delete_utility_geometry()
    ui.app.grb_editor.active_tool.clean_up()
    ui.app.grb_editor.select_tool('select')
    return STOP


def grb_delete(ui, key):
    # Del on the plot area deletes the selected shapes
    ui.app.grb_editor.launched_from_shortcuts = True
    if ui.app.grb_editor.selected:
        ui.app.grb_editor.delete_selected()
        ui.app.grb_editor.plot_all()
    else:
        ui.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Nothing selected."))
    return STOP


# ---------------------------------------------------------------------------------------------------------------------
# Excellon Editor
# ---------------------------------------------------------------------------------------------------------------------
def exc_editor_actions():
    exc = 'exc_editor'
    flag = lambda function: shortcut_flag('exc_editor', function)    # noqa: E731
    return [
        Shortcut('exc_editor.undo', _("Undo"), exc, 'Ctrl+Z', editor_undo('exc_editor')),
        Shortcut('exc_editor.redo', _("Redo"), exc, 'Ctrl+Y', editor_redo('exc_editor')),
        Shortcut('exc_editor.redo_alt', '%s (%s)' % (_("Redo"), _("Alternative")), exc, 'Ctrl+Shift+Z',
                 editor_redo('exc_editor')),
        # Ctrl (another Ctrl key goes to the active tool: Ctrl+Space of the Slot Array)
        Shortcut('exc_editor.select_all', _("Select All"), exc, 'Ctrl+A',
                 stop(lambda ui: ui.app.exc_editor.ui.tools_table_exc.selectAll())),
        Shortcut('exc_editor.exit', _("Save Object and Exit Editor"), exc, 'Ctrl+S', exit_editor),
        Shortcut('exc_editor.distance', _("Distance Tool"), exc, 'Ctrl+M', stop(lambda ui: ui.app.distance_tool.run())),
        # Shift
        Shortcut('exc_editor.distance_min', _("Distance Minimum Tool"), exc, 'Shift+M',
                 stop(lambda ui: ui.app.distance_min_tool.run())),
        # no modifier: also with a drawing tool active
        Shortcut('exc_editor.abort', _("Abort and return to Select"), exc, 'Esc', exc_abort),
        Shortcut('exc_editor.finish', _("Finish drawing for certain tools"), exc, 'Return', exc_finish),
        Shortcut('exc_editor.delete', _("Delete"), exc, 'Del', exc_delete, source=SOURCE_CANVAS),
        Shortcut('exc_editor.delete_tool', _("Alternate: Delete Tool"), exc, 'Del',
                 flag(lambda ui: ui.app.exc_editor.on_tool_delete()), source=SOURCE_PROJECT),
        Shortcut('exc_editor.zoom_out', _("Zoom Out"), exc, '-', flag(editor_zoom('exc_editor', out=True))),
        Shortcut('exc_editor.zoom_in', _("Zoom In"), exc, '=', flag(editor_zoom('exc_editor', out=False))),
        Shortcut('exc_editor.notebook', _("(left to Key_1)Toggle Notebook Area (Left Side)"), exc, '`',
                 flag(lambda ui: ui.on_toggle_notebook())),
        Shortcut('exc_editor.grid_snap', _("Grid On/Off"), exc, 'G', editor_grid_snap('exc_editor')),
        Shortcut('exc_editor.corner_snap', _("Toggle Corner Snap"), exc, 'K',
                 flag(lambda ui: ui.app.ui.corner_snap_btn.trigger())),
        Shortcut('exc_editor.zoom_fit', _("Zoom Fit"), exc, 'V', flag(lambda ui: ui.app.on_zoom_fit())),
        Shortcut('exc_editor.slot', _("Add Slot"), exc, 'W',
                 editor_add_at_cursor('exc_editor', 'slot_add', 'add_slot_btn', _("Click on target point."))),
        Shortcut('exc_editor.shortcut_list', _("Shortcuts List"), exc, 'F3', stop(lambda ui: ui.on_shortcut_list())),
        # no modifier, when no drawing tool is active
        Shortcut('exc_editor.drill_array', _("Add Drill Array"), exc, 'A',
                 editor_add_at_cursor('exc_editor', 'drill_array', 'add_drill_array_btn', _("Click on target point.")),
                 phase=PHASE_TOOL),
        Shortcut('exc_editor.copy', _("Copy"), exc, 'C',
                 editor_from_selection('exc_editor', 'drill_copy', 'copy_drill_btn', _("Click on target point.")),
                 phase=PHASE_TOOL),
        Shortcut('exc_editor.drill', _("Add Drill"), exc, 'D',
                 editor_add_at_cursor('exc_editor', 'drill_add', 'add_drill_btn', _("Click on target point.")),
                 phase=PHASE_TOOL),
        Shortcut('exc_editor.jump', _("Jump to Location (x, y)"), exc, 'J', lambda ui, key: ui.app.on_jump_to(),
                 phase=PHASE_TOOL),
        Shortcut('exc_editor.move', _("Move"), exc, 'M',
                 editor_from_selection('exc_editor', 'drill_move', 'move_drill_btn',
                                       _("Click on target location ...")),
                 phase=PHASE_TOOL),
        Shortcut('exc_editor.slot_array', _("Add Slot Array"), exc, 'Q',
                 editor_add_at_cursor('exc_editor', 'slot_array', 'add_slot_array_btn', _("Click on target point.")),
                 phase=PHASE_TOOL),
        Shortcut('exc_editor.resize', _("Resize Drill"), exc, 'R',
                 flag(lambda ui: ui.app.exc_editor.select_tool('drill_resize')), phase=PHASE_TOOL),
        Shortcut('exc_editor.add_tool', _("Add a new Tool"), exc, 'T', exc_add_tool, phase=PHASE_TOOL),
    ]


def exc_abort(ui, key):
    ui.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
    ui.app.exc_editor.delete_utility_geometry()
    ui.app.exc_editor.active_tool.clean_up()
    ui.app.exc_editor.select_tool('drill_select')
    return STOP


def exc_finish(ui, key):
    """Finish the current action. Use with tools that do not complete automatically."""
    from appEditors.appGeoEditor import FCShapeTool
    editor = ui.app.exc_editor
    tool = editor.active_tool
    if not isinstance(tool, FCShapeTool):
        return
    # a typed distance in the panel of the tool: the tool takes Enter itself
    # ('drill_move' has no panel for a typed distance (no move_tool): Enter clicks at the cursor)
    if tool.name == 'drill_add' and tool.drill_tool.length != 0.0:
        return
    if tool.name == 'slot_add' and tool.slot_tool.length != 0.0:
        return
    if tool.name == 'drill_copy' and tool.copy_tool.length != 0.0:
        return
    editor.on_canvas_click_left_handler((editor.snap_x, editor.snap_y))
    if editor.active_tool.complete:
        editor.on_shape_complete()
        ui.app.inform.emit('[success] %s' % _("Done."))


def exc_delete(ui, key):
    # Del on the plot area deletes the selected drills and slots
    ui.app.exc_editor.launched_from_shortcuts = True
    if ui.app.exc_editor.selected:
        ui.app.exc_editor.delete_selected()
        ui.app.exc_editor.replot()
    else:
        ui.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Nothing selected."))
    return STOP


def exc_add_tool(ui, key):
    ui.app.exc_editor.launched_from_shortcuts = True
    # ## Current application units in Upper Case
    ui.units = "MM"
    tool_add_popup = FCInputDoubleSpinner(title='%s ...' % _("New Tool"), text='%s:' % _('Enter a Tool Diameter'),
                                          min=0.0000, max=99.9999, decimals=ui.decimals)
    tool_add_popup.set_icon(QtGui.QIcon(ui.app.resource_location + '/letter_t_32.png'))

    val, ok = tool_add_popup.get_value()
    if ok:
        ui.app.exc_editor.on_tool_add(tooldia=val)
        formated_val = '%.*f' % (ui.decimals, float(val))
        ui.app.inform.emit('[success] %s: %s %s' % (_("Added new tool with dia"), formated_val, str(ui.units)))
    else:
        ui.app.inform.emit('[WARNING_NOTCL] %s...' % _("Adding Tool cancelled"))
    return STOP


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
