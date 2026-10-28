# ###########################################################
# FlatCAM: 2D Post-processing for Manufacturing             #
# http://flatcam.org                                        #
# Author: Juan Pablo Caram (c)                              #
# Date: 2/5/2014                                            #
# MIT Licence                                               #
# Modified by Marius Stanciu (2019)                          #
# ###########################################################

import webbrowser

from PyQt6 import QtCore

import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AppSignalConnector(QtCore.QObject):
    """Handler for all signal connection methods in App."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.log = app.log
        self.ui = app.ui

    def connect_filemenu_signals(self):
        # ### Menu
        self.ui.menufilenewproject.triggered.connect(self.app.f_handlers.on_file_new_click)
        self.ui.menufilenewgeo.triggered.connect(lambda: self.app.app_obj.new_geometry_object())
        self.ui.menufilenewgrb.triggered.connect(lambda: self.app.app_obj.new_gerber_object())
        self.ui.menufilenewexc.triggered.connect(lambda: self.app.app_obj.new_excellon_object())
        self.ui.menufilenewdoc.triggered.connect(lambda: self.app.app_obj.new_document_object())

        self.ui.menufileopengerber.triggered.connect(lambda: self.app.f_handlers.on_file_open_gerber())
        self.ui.menufileopenexcellon.triggered.connect(lambda: self.app.f_handlers.on_file_open_excellon())
        self.ui.menufileopengcode.triggered.connect(lambda: self.app.f_handlers.on_file_open_gcode())
        self.ui.menufileopenproject.triggered.connect(lambda: self.app.f_handlers.on_file_open_project())
        self.ui.menufileopenconfig.triggered.connect(lambda: self.app.f_handlers.on_file_open_config())

        self.ui.menufilenewscript.triggered.connect(self.app.f_handlers.on_file_new_script)
        self.ui.menufileopenscript.triggered.connect(self.app.f_handlers.on_file_open_script)
        self.ui.menufileopenscriptexample.triggered.connect(self.app.f_handlers.on_file_open_script_example)

        self.ui.menufilerunscript.triggered.connect(self.app.f_handlers.on_file_run_script)

        self.ui.menufileimportsvg.triggered.connect(lambda: self.app.f_handlers.on_file_import_svg("geometry"))
        self.ui.menufileimportsvg_as_gerber.triggered.connect(lambda: self.app.f_handlers.on_file_import_svg("gerber"))

        self.ui.menufileimportdxf.triggered.connect(lambda: self.app.f_handlers.on_file_import_dxf("geometry"))
        self.ui.menufileimportdxf_as_gerber.triggered.connect(lambda: self.app.f_handlers.on_file_import_dxf("gerber"))
        self.ui.menufileimport_hpgl2_as_geo.triggered.connect(lambda: self.app.f_handlers.on_file_open_hpgl2())
        self.ui.menufileimportpcbdoc.triggered.connect(lambda: self.app.f_handlers.on_file_import_pcbdoc())
        self.ui.menufileimportproteus.triggered.connect(lambda: self.app.f_handlers.on_file_import_proteus())
        self.ui.menufileimportkicad.triggered.connect(lambda: self.app.f_handlers.on_file_import_kicad_pcb())
        self.ui.menufileimportsprint.triggered.connect(lambda: self.app.f_handlers.on_file_import_sprint_layout())
        self.ui.menufileimporteagle.triggered.connect(lambda: self.app.f_handlers.on_file_import_eagle_brd())
        self.ui.menufileimportallegro.triggered.connect(lambda: self.app.f_handlers.on_file_import_allegro_brd())
        self.ui.menufileimportdiptrace.triggered.connect(lambda: self.app.f_handlers.on_file_import_diptrace())
        self.ui.menufileexportsvg.triggered.connect(self.app.f_handlers.on_file_export_svg)
        self.ui.menufileexportpng.triggered.connect(self.app.f_handlers.on_file_export_png)
        self.ui.menufileexportexcellon.triggered.connect(self.app.f_handlers.on_file_export_excellon)
        self.ui.menufileexportgerber.triggered.connect(self.app.f_handlers.on_file_export_gerber)

        self.ui.menufileexportdxf.triggered.connect(self.app.f_handlers.on_file_export_dxf)

        self.ui.menufile_print.triggered.connect(lambda: self.app.f_handlers.on_file_save_objects_pdf(use_thread=True))

        self.ui.menufilesaveproject.triggered.connect(self.app.f_handlers.on_file_save_project)
        self.ui.menufilesaveprojectas.triggered.connect(self.app.f_handlers.on_file_save_project_as)
        # self.ui.menufilesaveprojectcopy.triggered.connect(lambda: self.on_file_save_project_as(make_copy=True))
        self.ui.menufilesavedefaults.triggered.connect(self.app.f_handlers.on_file_save_defaults)

        self.ui.menufileexportpref.triggered.connect(self.app.f_handlers.on_export_preferences)
        self.ui.menufileimportpref.triggered.connect(self.app.f_handlers.on_import_preferences)

    def connect_editmenu_signals(self):
        self.ui.menufile_exit.triggered.connect(self.ui.close)

        self.ui.menueditedit.triggered.connect(lambda: self.app.on_editing_start())
        self.ui.menueditok.triggered.connect(lambda: self.app.on_editing_finished())

        def history_changed(undo_label, redo_label):
            if self.app.call_source == 'app':
                self.ui.on_history_changed(undo_label, redo_label)

        def replay(redo=False):
            if self.ui.app_focus_in_text():
                return
            if self.app.call_source == 'geo_editor':
                history = self.app.geo_editor
            elif self.app.call_source == 'grb_editor':
                history = self.app.grb_editor
            elif self.app.call_source == 'app':
                history = self.app.history
            else:
                return
            history.redo() if redo else history.undo()

        self.app.history_changed.connect(history_changed)
        self.ui.menuedit_undo.triggered.connect(lambda: replay())
        self.ui.menuedit_redo.triggered.connect(lambda: replay(True))
        self.ui.undo_btn.triggered.connect(lambda: replay())
        self.ui.redo_btn.triggered.connect(lambda: replay(True))

        self.ui.menuedit_join2geo.triggered.connect(self.app.edit_class.on_edit_join)
        self.ui.menuedit_join_exc2exc.triggered.connect(self.app.edit_class.on_edit_join_exc)
        self.ui.menuedit_join_grb2grb.triggered.connect(self.app.edit_class.on_edit_join_grb)

        self.ui.menuedit_convert_sg2mg.triggered.connect(self.app.edit_class.on_convert_singlegeo_to_multigeo)
        self.ui.menuedit_convert_mg2sg.triggered.connect(self.app.edit_class.on_convert_multigeo_to_singlegeo)

        self.ui.menueditdelete.triggered.connect(self.app.on_delete)

        self.ui.menueditcopyobject.triggered.connect(self.app.on_copy_command)
        self.ui.menueditconvert_any2geo.triggered.connect(lambda: self.app.edit_class.convert_any2geo())
        self.ui.menueditconvert_any2gerber.triggered.connect(lambda: self.app.edit_class.convert_any2gerber())
        self.ui.menueditconvert_any2excellon.triggered.connect(lambda: self.app.edit_class.convert_any2excellon())

        self.ui.menuedit_numeric_move.triggered.connect(lambda: self.app.on_numeric_move())

        self.ui.menueditorigin.triggered.connect(self.app.on_set_origin)
        self.ui.menuedit_move2origin.triggered.connect(lambda: self.app.on_move2origin())
        self.ui.menuedit_center_in_origin.triggered.connect(lambda: self.app.edit_class.on_custom_origin())

        self.ui.menueditjump.triggered.connect(self.app.on_jump_to)
        self.ui.menueditlocate.triggered.connect(lambda: self.app.on_locate(obj=self.app.collection.get_active()))

        self.ui.menueditselectall.triggered.connect(self.app.on_selectall)
        self.ui.menueditpreferences.triggered.connect(self.app.on_preferences)

    def connect_optionsmenu_signals(self):
        self.ui.menuoptions_transform_rotate.triggered.connect(self.app.on_rotate)

        self.ui.menuoptions_transform_skewx.triggered.connect(self.app.on_skewx)
        self.ui.menuoptions_transform_skewy.triggered.connect(self.app.on_skewy)

        self.ui.menuoptions_transform_flipx.triggered.connect(self.app.on_flipx)
        self.ui.menuoptions_transform_flipy.triggered.connect(self.app.on_flipy)
        self.ui.menuoptions_view_source.triggered.connect(self.app.on_view_source)
        self.ui.menuoptions_tools_db.triggered.connect(
            lambda: self.app.on_tools_database(source='app')
        )
        self.ui.menuoptions_experimental_3D_area.triggered.connect(self.app.on_3d_area)

    def connect_menuview_signals(self):
        self.ui.menuviewenable.triggered.connect(self.app.enable_all_plots)
        self.ui.menuviewdisableall.triggered.connect(self.app.disable_all_plots)
        self.ui.menuviewenableother.triggered.connect(self.app.enable_other_plots)
        self.ui.menuviewdisableother.triggered.connect(self.app.disable_other_plots)

        self.ui.menuview_zoom_fit.triggered.connect(self.app.on_zoom_fit)
        self.ui.menuview_zoom_in.triggered.connect(self.app.on_zoom_in)
        self.ui.menuview_zoom_out.triggered.connect(self.app.on_zoom_out)
        self.ui.menuview_replot.triggered.connect(lambda: self.app.plot_all())

        self.ui.menuview_toggle_code_editor.triggered.connect(self.app.on_toggle_code_editor)
        self.ui.menuview_toggle_fscreen.triggered.connect(self.ui.on_full_screen_toggled)
        self.ui.menuview_toggle_parea.triggered.connect(self.ui.on_toggle_plotarea)
        self.ui.menuview_toggle_notebook.triggered.connect(self.ui.on_toggle_notebook)
        self.ui.menu_toggle_nb.triggered.connect(self.ui.on_toggle_notebook)
        self.ui.menuview_toggle_grid.triggered.connect(self.ui.on_toggle_grid)
        self.ui.menuview_toggle_workspace.triggered.connect(self.app.on_workspace_toggle)

        self.ui.menuview_toggle_grid_lines.triggered.connect(self.ui.on_toggle_grid_lines)
        self.ui.menuview_toggle_axis.triggered.connect(self.app.plotcanvas.on_toggle_axis)
        self.ui.menuview_toggle_hud.triggered.connect(self.app.plotcanvas.on_toggle_hud)
        self.ui.menuview_show_log.triggered.connect(self.app.on_show_log)

    def connect_menuhelp_signals(self):
        self.ui.menuhelp_about.triggered.connect(self.app.on_about)
        self.ui.menuhelp_readme.triggered.connect(self.app.on_howto)
        check_updates = getattr(self.ui, "menuhelp_check_updates", None)
        if check_updates is not None:
            check_updates.triggered.connect(self.app.on_check_for_updates)
        revert_update = getattr(self.ui, "menuhelp_revert_update", None)
        if revert_update is not None:
            revert_update.triggered.connect(self.app.on_revert_update)
        self.ui.menuhelp_donate.triggered.connect(lambda: webbrowser.open(self.app.donate_url))
        self.ui.menuhelp_manual.triggered.connect(lambda: webbrowser.open(self.app.manual_url))
        self.ui.menuhelp_report_bug.triggered.connect(lambda: webbrowser.open(self.app.bug_report_url))
        self.ui.menuhelp_exc_spec.triggered.connect(lambda: webbrowser.open(self.app.excellon_spec_url))
        self.ui.menuhelp_gerber_spec.triggered.connect(lambda: webbrowser.open(self.app.gerber_spec_url))
        self.ui.menuhelp_videohelp.triggered.connect(lambda: webbrowser.open(self.app.video_url))
        self.ui.menuhelp_shortcut_list.triggered.connect(self.ui.on_shortcut_list)

    def connect_project_context_signals(self):
        self.ui.menuprojectenable.triggered.connect(lambda: self.app.on_enable_sel_plots())
        self.ui.menuprojectdisable.triggered.connect(self.app.on_disable_sel_plots)
        self.ui.menuprojectviewsource.triggered.connect(self.app.on_view_source)

        self.ui.menuprojectcopy.triggered.connect(self.app.on_copy_command)
        self.ui.menuprojectedit.triggered.connect(self.app.on_editing_start)

        self.ui.menuprojectdelete.triggered.connect(self.app.on_delete)
        self.ui.menuprojectsave.triggered.connect(self.app.on_project_context_save)
        self.ui.menuprojectexport_dxf.triggered.connect(self.app.f_handlers.on_file_export_dxf)
        self.ui.menuprojectexport_svg.triggered.connect(self.app.f_handlers.on_file_export_svg)
        self.ui.menuprojectexport_png.triggered.connect(self.app.f_handlers.on_file_export_object_png)
        self.ui.menuprojectexport_pdf.triggered.connect(self.app.f_handlers.on_file_export_object_pdf)
        self.ui.menuprojectproperties.triggered.connect(self.app.obj_properties)
        self.ui.menuprojectjoin_gerber.triggered.connect(lambda: self.app.edit_class.on_join_as('gerber'))
        self.ui.menuprojectjoin_excellon.triggered.connect(lambda: self.app.edit_class.on_join_as('excellon'))
        self.ui.menuprojectjoin_geometry.triggered.connect(lambda: self.app.edit_class.on_join_as('geometry'))

        # Project Context Menu -> Color Setting
        for act in self.ui.menuprojectcolor.actions():
            act.triggered.connect(self.app.on_set_color_action_triggered)

    def connect_canvas_context_signals(self):
        self.ui.popmenu_disable.triggered.connect(lambda: self.app.toggle_plots(self.app.collection.get_selected()))
        self.ui.popmenu_panel_toggle.triggered.connect(self.ui.on_toggle_notebook)

        # New
        self.ui.popmenu_new_geo.triggered.connect(lambda: self.app.app_obj.new_geometry_object())
        self.ui.popmenu_new_grb.triggered.connect(lambda: self.app.app_obj.new_gerber_object())
        self.ui.popmenu_new_exc.triggered.connect(lambda: self.app.app_obj.new_excellon_object())
        self.ui.popmenu_new_prj.triggered.connect(lambda: self.app.f_handlers.on_file_new_project())

        # View
        self.ui.zoomfit.triggered.connect(self.app.on_zoom_fit)
        self.ui.clearplot.triggered.connect(self.app.clear_plots)
        self.ui.replot.triggered.connect(lambda: self.app.plot_all())

        # Colors
        for act in self.ui.pop_menucolor.actions():
            act.triggered.connect(self.app.on_set_color_action_triggered)

        self.ui.popmenu_copy.triggered.connect(self.app.on_copy_command)
        self.ui.popmenu_delete.triggered.connect(self.app.on_delete)
        self.ui.popmenu_edit.triggered.connect(self.app.on_editing_start)
        self.ui.popmenu_save.triggered.connect(lambda: self.app.on_editing_finished())
        self.ui.popmenu_numeric_move.triggered.connect(lambda: self.app.on_numeric_move())
        self.ui.popmenu_move.triggered.connect(self.app.obj_move)
        self.ui.popmenu_move2origin.triggered.connect(lambda: self.app.on_move2origin())

        self.ui.popmenu_properties.triggered.connect(self.app.obj_properties)

    def connect_tools_signals_to_toolbar(self):
        self.log.debug(" -> Connecting Plugin Toolbar Signals")

        self.ui.drill_btn.triggered.connect(lambda: self.app.drilling_tool.run(toggle=True))
        self.ui.mill_btn.triggered.connect(lambda: self.app.milling_tool.run(toggle=True))

        self.ui.isolation_btn.triggered.connect(lambda: self.app.isolation_tool.run(toggle=True))
        self.ui.ncc_btn.triggered.connect(lambda: self.app.ncclear_tool.run(toggle=True))
        self.ui.paint_btn.triggered.connect(lambda: self.app.paint_tool.run(toggle=True))

        self.ui.cutout_btn.triggered.connect(lambda: self.app.cutout_tool.run(toggle=True))
        self.ui.panelize_btn.triggered.connect(lambda: self.app.panelize_tool.run(toggle=True))
        self.ui.film_btn.triggered.connect(lambda: self.app.film_tool.run(toggle=True))
        self.ui.dblsided_btn.triggered.connect(lambda: self.app.dblsidedtool.run(toggle=True))

        self.ui.align_btn.triggered.connect(lambda: self.app.align_objects_tool.run(toggle=True))
        self.ui.copperfill_btn.triggered.connect(lambda: self.app.copper_thieving_tool.run(toggle=True))
        self.ui.markers_tool_btn.triggered.connect(lambda: self.app.markers_tool.run(toggle=True))
        self.ui.punch_btn.triggered.connect(lambda: self.app.punch_tool.run(toggle=True))
        self.ui.calculators_btn.triggered.connect(lambda: self.app.calculator_tool.run(toggle=True))

    def connect_editors_toolbar_signals(self):
        self.log.debug(" -> Connecting Editors Toolbar Signals")

        # Geometry Editor Toolbar Signals
        if self.app.geo_editor is not None:
            self.app.geo_editor.connect_geo_toolbar_signals()

        # Gerber Editor Toolbar Signals
        if self.app.grb_editor is not None:
            self.app.grb_editor.connect_grb_toolbar_signals()

        # Excellon Editor Toolbar Signals
        if self.app.exc_editor is not None:
            self.app.exc_editor.connect_exc_toolbar_signals()

    def connect_toolbar_signals(self):
        """
        Reconnect the signals to the actions in the toolbar.
        This has to be done each time after the FlatCAM tools are removed/installed.

        :return: None
        """
        self.log.debug(" -> Connecting Toolbar Signals")
        # Toolbar

        # File Toolbar Signals
        self.ui.file_open_gerber_btn.triggered.connect(lambda: self.app.f_handlers.on_file_open_gerber())
        self.ui.file_open_excellon_btn.triggered.connect(lambda: self.app.f_handlers.on_file_open_excellon())

        # View Toolbar Signals
        self.ui.clear_plot_btn.triggered.connect(self.app.clear_plots)
        self.ui.replot_btn.triggered.connect(lambda: self.app.plot_all())
        self.ui.zoom_fit_btn.triggered.connect(self.app.on_zoom_fit)
        self.ui.zoom_in_btn.triggered.connect(lambda: self.app.plotcanvas.zoom(1 / 1.5))
        self.ui.zoom_out_btn.triggered.connect(lambda: self.app.plotcanvas.zoom(1.5))

        # Edit Toolbar Signals
        self.ui.editor_start_btn.triggered.connect(self.app.on_editing_start)
        self.ui.editor_exit_btn.clicked.connect(lambda: self.app.on_editing_finished(force_cancel=True))
        self.ui.copy_btn.triggered.connect(self.app.on_copy_command)
        self.ui.delete_btn.triggered.connect(self.app.on_delete)

        self.ui.distance_btn.triggered.connect(lambda: self.app.distance_tool.run(toggle=True))
        # self.ui.distance_min_btn.triggered.connect(lambda: self.distance_min_tool.run(toggle=True))
        self.ui.origin_btn.triggered.connect(self.app.on_set_origin)
        # self.ui.move2origin_btn.triggered.connect(self.on_move2origin)
        # self.ui.center_in_origin_btn.triggered.connect(self.on_custom_origin)

        self.ui.jmp_btn.triggered.connect(self.app.on_jump_to)
        self.ui.locate_btn.triggered.connect(lambda: self.app.on_locate(obj=self.app.collection.get_active()))

        # Scripting Toolbar Signals
        self.ui.shell_btn.triggered.connect(self.ui.toggle_shell_ui)
        self.ui.new_script_btn.triggered.connect(self.app.f_handlers.on_file_new_script)
        self.ui.open_script_btn.triggered.connect(self.app.f_handlers.on_file_open_script)
        self.ui.run_script_btn.triggered.connect(self.app.f_handlers.on_file_run_script)

        # Tools Toolbar Signals
        try:
            self.connect_tools_signals_to_toolbar()
        except Exception as c_err:
            self.log.error("App.connect_toolbar_signals() tools signals -> %s" % str(c_err))
