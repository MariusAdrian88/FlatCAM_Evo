# FlatCAM: 2D Post-processing for Manufacturing
# http://flatcam.org
# Author: Juan Pablo Caram (c)
# Date: 2/5/2014
# MIT Licence
# Modified by Marius Stanciu (2019)

import sys
import os
import json
import traceback

import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext

from PyQt6 import QtCore
from appGUI.GUIElements import FCMessageBox
from appGUI.VisPyVisuals import stop_deferred_updates
from appCommon.Headless import HeadlessExit


class AppLifecycle(QtCore.QObject):
    """Handler for app lifecycle methods: startup, shutdown, version check."""

    # start_delayed_quit() called in a worker thread: the timer is made in the GUI thread (delay, filename, should_quit)
    delayed_quit_requested = QtCore.pyqtSignal(int, str, object)

    def __init__(self, app):
        """Cache only dependencies available during early construction.

        App creates this handler before UI, collection, and plotcanvas setup.
        Don't access those attributes here; lifecycle methods may use self.app after setup.
        """
        super().__init__()
        self.app = app
        self.log = app.log
        self.inform = app.inform
        self.defaults = app.defaults
        self.options = app.options

        # queued when emitted in a worker thread: this object lives in the GUI thread
        self.delayed_quit_requested.connect(self.start_delayed_quit)

    # --------------------------------------------------------------------------
    # Method 1: on_options_value_changed
    # --------------------------------------------------------------------------
    def on_options_value_changed(self, key_changed):
        # when changing those properties the associated keys change, so we get an updated Properties default Tab
        if key_changed in [
            "global_grid_lines", "global_grid_snap", "global_axis", "global_workspace", "global_workspaceT",
            "global_workspace_orientation", "global_hud"
        ]:
            self.app.on_properties_tab_click()

        # TODO handle changing the units in the Preferences
        # if key_changed == "units":
        #     self.on_toggle_units(no_pref=False)

    # --------------------------------------------------------------------------
    # Method 2: on_app_restart
    # --------------------------------------------------------------------------
    def on_app_restart(self):
        if getattr(self.app, 'script_state', '') == 'running':
            self.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before restarting."))
            return False
        # make sure that the Sys Tray icon is hidden before restart otherwise it will
        # be left in the SySTray
        try:
            self.app.trayIcon.hide()
        except Exception:
            pass

        import appTranslation as fcTranslate
        fcTranslate.restart_program(app=self.app)

    # --------------------------------------------------------------------------
    # Method 3: on_layout
    # --------------------------------------------------------------------------
    def on_layout(self, lay=None, connect_signals=True):
        """
        Set the toolbars layout (location).

        :param connect_signals: Useful when used in the App.__init__(); bool
        :param lay:             Type of layout to be set on the toolbar
        :return:                None
        """
        from PyQt6.QtCore import QSettings
        from PyQt6 import QtWidgets
        from PyQt6.QtCore import Qt

        self.defaults.report_usage("on_layout()")
        self.log.debug(" ---> New Layout")

        # QComboBox.activated supplies an index, not a layout name.
        if isinstance(lay, str) and lay:
            current_layout = lay
        else:
            current_layout = self.app.ui.general_pref_form.general_gui_group.layout_combo.get_value()

        self.options['global_layout'] = current_layout
        lay_settings = QSettings("Open Source", "FlatCAM_EVO")
        lay_settings.setValue('layout', current_layout)

        # This will write the setting to the platform specific storage.
        del lay_settings

        # first remove the toolbars:
        self.log.debug(" -> Remove Toolbars")
        try:
            self.app.ui.removeToolBar(self.app.ui.toolbarfile)
            self.app.ui.removeToolBar(self.app.ui.toolbaredit)
            self.app.ui.removeToolBar(self.app.ui.toolbarview)
            self.app.ui.removeToolBar(self.app.ui.toolbarshell)
            self.app.ui.removeToolBar(self.app.ui.toolbarplugins)
            self.app.ui.removeToolBar(self.app.ui.exc_edit_toolbar)
            self.app.ui.removeToolBar(self.app.ui.geo_edit_toolbar)
            self.app.ui.removeToolBar(self.app.ui.grb_edit_toolbar)
            self.app.ui.removeToolBar(self.app.ui.toolbarshell)
        except Exception:
            pass

        self.log.debug(" -> Add New Toolbars")
        if current_layout == 'compact':
            # ## TOOLBAR INSTALLATION # ##
            self.app.ui.toolbarfile = QtWidgets.QToolBar('File Toolbar')
            self.app.ui.toolbarfile.setObjectName('File_TB')
            self.app.ui.toolbarfile.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.app.ui.toolbarfile)

            self.app.ui.toolbaredit = QtWidgets.QToolBar('Edit Toolbar')
            self.app.ui.toolbaredit.setObjectName('Edit_TB')
            self.app.ui.toolbaredit.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.app.ui.toolbaredit)

            self.app.ui.toolbarshell = QtWidgets.QToolBar('Shell Toolbar')
            self.app.ui.toolbarshell.setObjectName('Shell_TB')
            self.app.ui.toolbarshell.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.app.ui.toolbarshell)

            self.app.ui.toolbarplugins = QtWidgets.QToolBar('Plugin Toolbar')
            self.app.ui.toolbarplugins.setObjectName('Plugins_TB')
            self.app.ui.toolbarplugins.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.app.ui.toolbarplugins)

            self.app.ui.geo_edit_toolbar = QtWidgets.QToolBar('Geometry Editor Toolbar')
            self.app.ui.geo_edit_toolbar.setObjectName('GeoEditor_TB')
            self.app.ui.geo_edit_toolbar.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.app.ui.geo_edit_toolbar)

            self.app.ui.toolbarview = QtWidgets.QToolBar('View Toolbar')
            self.app.ui.toolbarview.setObjectName('View_TB')
            self.app.ui.toolbarview.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.app.ui.toolbarview)

            self.app.ui.addToolBarBreak(area=Qt.ToolBarArea.RightToolBarArea)

            self.app.ui.grb_edit_toolbar = QtWidgets.QToolBar('Gerber Editor Toolbar')
            self.app.ui.grb_edit_toolbar.setObjectName('GrbEditor_TB')
            self.app.ui.grb_edit_toolbar.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.app.ui.grb_edit_toolbar)

            self.app.ui.exc_edit_toolbar = QtWidgets.QToolBar('Excellon Editor Toolbar')
            self.app.ui.exc_edit_toolbar.setObjectName('ExcEditor_TB')
            self.app.ui.exc_edit_toolbar.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.app.ui.exc_edit_toolbar)
        else:
            # ## TOOLBAR INSTALLATION # ##
            self.app.ui.toolbarfile = QtWidgets.QToolBar('File Toolbar')
            self.app.ui.toolbarfile.setObjectName('File_TB')
            self.app.ui.toolbarfile.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.toolbarfile)

            self.app.ui.toolbaredit = QtWidgets.QToolBar('Edit Toolbar')
            self.app.ui.toolbaredit.setObjectName('Edit_TB')
            self.app.ui.toolbaredit.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.toolbaredit)

            self.app.ui.toolbarview = QtWidgets.QToolBar('View Toolbar')
            self.app.ui.toolbarview.setObjectName('View_TB')
            self.app.ui.toolbarview.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.toolbarview)

            self.app.ui.toolbarshell = QtWidgets.QToolBar('Shell Toolbar')
            self.app.ui.toolbarshell.setObjectName('Shell_TB')
            self.app.ui.toolbarshell.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.toolbarshell)

            self.app.ui.toolbarplugins = QtWidgets.QToolBar('Plugin Toolbar')
            self.app.ui.toolbarplugins.setObjectName('Plugins_TB')
            self.app.ui.toolbarplugins.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.toolbarplugins)

            self.app.ui.exc_edit_toolbar = QtWidgets.QToolBar('Excellon Editor Toolbar')
            # self.ui.exc_edit_toolbar.setVisible(False)
            self.app.ui.exc_edit_toolbar.setObjectName('ExcEditor_TB')
            self.app.ui.exc_edit_toolbar.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.exc_edit_toolbar)

            self.app.ui.addToolBarBreak()

            self.app.ui.geo_edit_toolbar = QtWidgets.QToolBar('Geometry Editor Toolbar')
            # self.ui.geo_edit_toolbar.setVisible(False)
            self.app.ui.geo_edit_toolbar.setObjectName('GeoEditor_TB')
            self.app.ui.geo_edit_toolbar.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.geo_edit_toolbar)

            self.app.ui.grb_edit_toolbar = QtWidgets.QToolBar('Gerber Editor Toolbar')
            # self.ui.grb_edit_toolbar.setVisible(False)
            self.app.ui.grb_edit_toolbar.setObjectName('GrbEditor_TB')
            self.app.ui.grb_edit_toolbar.setStyleSheet("QToolBar{spacing:0px;border:0px;}")
            self.app.ui.addToolBar(self.app.ui.grb_edit_toolbar)

        # The File Toolbar is off in all the layout presets; the user can show it from the toolbar context menu.
        self.app.ui.toolbarfile.setVisible(False)

        if current_layout == 'minimal':
            self.app.ui.toolbarview.setVisible(False)
            self.app.ui.toolbarshell.setVisible(False)
            self.app.ui.geo_edit_toolbar.setVisible(False)
            self.app.ui.grb_edit_toolbar.setVisible(False)
            self.app.ui.exc_edit_toolbar.setVisible(False)

        # add all the actions to the toolbars
        self.app.ui.populate_toolbars()

        # Rebuilt toolbars must match the menu state; minimal keeps its locked preset.
        self.app.ui.lock_toolbar(lock=True if current_layout == 'minimal' else self.app.ui.lock_action.isChecked())
        self.app.ui.show_text_under_action(show_text=self.app.ui.show_text_action.isChecked())

        try:
            # reconnect all the signals to the toolbar actions
            if connect_signals is True:
                self.app.connect_toolbar_signals()
        except Exception as e:
            self.log.error(
                "App.on_layout() - connect toolbar signals -> %s" % str(e))

        # Editor Toolbars Signals
        try:
            self.app.connect_editors_toolbar_signals()
        except Exception as m_err:
            self.log.error("App.on_layout() - connect editor signals -> %s" % str(m_err))

        self.app.ui.grid_snap_btn.setChecked(True)

        self.app.ui.corner_snap_btn.setVisible(False)
        self.app.ui.snap_magnet.setVisible(False)

        self.app.ui.grid_gap_x_entry.setText(str(self.options.get("global_gridx", self.defaults.get("global_gridx", 1.0))))
        self.app.ui.grid_gap_y_entry.setText(str(self.options.get("global_gridy", self.defaults.get("global_gridy", 1.0))))
        self.app.ui.snap_max_dist_entry.setText(str(self.options.get("global_snap_max", self.defaults.get("global_snap_max", 0.05))))
        self.app.ui.grid_gap_link_cb.setChecked(True)

    # --------------------------------------------------------------------------
    # Method 4: final_save
    # --------------------------------------------------------------------------
    def final_save(self):
        """
        Callback for doing a preferences save to file whenever the application is about to quit.
        If the project has changes, it will ask the user to save the project.

        :return: None
        """
        from PyQt6 import QtGui, QtWidgets

        if getattr(self.app, 'script_state', '') == 'running' and not getattr(self.app, 'headless', False):
            self.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before closing."))
            return False

        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return False

        # project_save_pending stays set until the GUI has the result of a File -> Save; before that, the dirty
        # flag of a failed save is not restored yet
        if self.app.save_in_progress or getattr(self.app, 'project_save_pending', False):
            # the app closes when the save ends (check_project_file_size()); not after a failed save
            self.app.close_after_save = True
            self.inform.emit('[WARNING_NOTCL] %s' % _("Saving the project. FlatCAM closes when the save ends."))
            return

        # with more than 1 project, ask about each project that has unsaved changes
        projects = getattr(self.app, 'projects', None)
        if projects is not None:
            start = projects.active
            # quit_application() also asks a question (the Tools Database) and can refuse the quit
            quitting = False
            try:
                for ctx in projects.dirty_contexts():
                    answer = projects.ask_save(ctx)
                    if answer == 'cancel' or (answer == 'yes' and not projects.save_context(ctx, visuals=False)):
                        return
                quitting = self.quit_application() is True
            finally:
                if not quitting:
                    # the exit stops (a cancel, a failed save, a refused quit or an error). The save
                    # of an inactive project made it active, but the canvas still shows start: make start active
                    projects.return_to(start)
            if quitting:
                # QApplication.quit() only posts the quit event, so the tray icon is hidden before the app ends
                try:
                    self.app.trayIcon.hide()
                except Exception:
                    pass
            return

        if self.app.should_we_save and self.app.collection.get_list():
            msgbox = FCMessageBox(parent=self.app.ui)
            title = _("Save changes")
            txt = _("There are files/objects modified.\n"
                    "Do you want to Save the project?")
            msgbox.setWindowTitle(title)  # taskbar still shows it
            msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
            msgbox.setText('<b>%s</b>' % title)
            msgbox.setInformativeText(txt)
            msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/save_as.png'))

            bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
            bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
            bt_cancel = msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)

            msgbox.setDefaultButton(bt_yes)
            msgbox.exec()
            response = msgbox.clickedButton()

            if response == bt_yes:
                try:
                    self.app.trayIcon.hide()
                except Exception:
                    pass
                self.app.f_handlers.on_file_save_project_as(use_thread=True, quit_action=True)
            elif response == bt_no:
                try:
                    self.app.trayIcon.hide()
                except Exception:
                    pass
                self.quit_application()
            elif response == bt_cancel:
                return
        else:
            try:
                self.app.trayIcon.hide()
            except Exception:
                pass
            self.quit_application()

    # --------------------------------------------------------------------------
    # Method 5: quit_application
    # --------------------------------------------------------------------------
    def _confirm_tools_database(self):
        try:
            database = self.app.tools_db_tab
        except (AttributeError, RuntimeError):
            return True

        if database is None:
            return True

        try:
            if getattr(database, "_close_preapproved", False) is True:
                return True
            confirm_close = getattr(database, 'confirm_close', None)
            if not callable(confirm_close) or not confirm_close():
                return False
            database._close_preapproved = True
        except RuntimeError:
            return True
        except Exception as error:
            self.log.error("AppLifecycle._confirm_tools_database() --> %s" % str(error))
            return False
        return True

    def quit_application(self, silent=False, mode=None):
        """
        Called (as a pyslot or not) when the application is quit.
        Direct implementation - does NOT delegate back to App to avoid circular call.

        :return: bool
        """
        from PyQt6.QtCore import QSettings
        from PyQt6 import QtCore, QtWidgets

        if getattr(self.app, 'script_state', '') == 'running' and not getattr(self.app, 'headless', False):
            self.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before closing."))
            return False

        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return False

        if getattr(self.app, 'headless', False):
            # headless mode: quit_app ends the run; the preferences and the window state of the user stay as they are
            self.app.headless_exit_code = 0
            raise HeadlessExit(0)

        if not self._confirm_tools_database():
            return False

        # Tools DB confirmation can process a geometry Save request in its modal event loop.
        if getattr(getattr(self.app, 'geo_editor', None), '_source_save_pending', False) is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Geometry save is running. Wait until it ends."))
            return False

        # Keep the application layout before hiding the window or deactivating editors changes its toolbars.
        gui_state = getattr(self.app, '_editor_gui_state', None)
        if getattr(self.app, 'call_source', 'app') == 'app' or not isinstance(gui_state, QtCore.QByteArray):
            gui_state = self.app.ui.saveState(0)

        # make sure that any change we made while working in the app is saved to the defaults
        # WARNING !!! Do not hide UI before saving the state of the UI in the defaults file !!!
        # TODO in the future we need to make a difference between settings that need to be persistent all the time
        self.defaults.update(self.options)
        self.app.preferencesUiManager.save_defaults(silent=True)

        if silent is False:
            self.log.debug("AppLifecycle.quit_application() --> App Defaults saved.")

        # hide the UI so the user experiences a faster shutdown
        self.app.ui.hide()

        # stop listening for the files of a second launch
        try:
            if self.app.new_launch is not None:
                self.app.new_launch.close()
        except Exception as e:
            self.log.error("AppLifecycle.quit_application() --> %s" % str(e))

        # close editors before quitting the app, if they are open
        for kind, editor_name in (('geometry', 'Geo Editor'), ('excellon', 'Excellon Editor'),
                                  ('gerber', 'Gerber Editor'), ('cncjob', 'GCode Editor')):
            editor = self.app._editor_for(kind)
            if editor is not None:
                self.app._deactivate_editor(kind)
                # Keep destroyed() connections intact; Qt disconnects signals when the editor is destroyed.
                if silent is False:
                    self.log.debug("App.quit_application() --> %s deactivated." % editor_name)

        # disconnect the mouse events
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_double_click', self.app.on_mouse_double_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('key_press', self.app.ui.keyPressEvent)

        # Release VisPy OpenGL resources early so the GL thread has time to
        # fully exit before QApplication.quit() processes the quit event
        try:
            # a deferred redraw that runs after the close fails: Gloo has no canvas
            stop_deferred_updates()
            self.app.plotcanvas.close()
        except Exception:
            pass

        # save app state to file
        if self.app.ui.saveState(0) != gui_state:
            self.app.ui.restoreState(gui_state, 0)
        stgs = QSettings("Open Source", "FlatCAM_EVO")
        stgs.setValue('saved_gui_state', gui_state)
        stgs.setValue('maximized_gui', self.app.ui.isMaximized())
        if not self.app.ui.isMaximized():
            # Updater and Tcl quits do not pass through MainGUI.closeEvent().
            g_rect = self.app.ui.geometry()
            stgs.setValue('window_geometry', (g_rect.x(), g_rect.y(), g_rect.width(), g_rect.height()))
        stgs.setValue(
            'language',
            self.app.ui.general_pref_form.general_app_group.language_combo.get_value()
        )
        stgs.setValue(
            'notebook_font_size',
            self.app.ui.general_pref_form.general_app_set_group.notebook_font_size_spinner.get_value()
        )
        stgs.setValue(
            'axis_font_size',
            self.app.ui.general_pref_form.general_app_set_group.axis_font_size_spinner.get_value()
        )
        stgs.setValue(
            'textbox_font_size',
            self.app.ui.general_pref_form.general_app_set_group.textbox_font_size_spinner.get_value()
        )
        stgs.setValue(
            'hud_font_size',
            self.app.ui.general_pref_form.general_app_set_group.hud_font_size_spinner.get_value()
        )
        # This will write the setting to the platform specific storage.
        del stgs

        if silent is False:
            self.log.debug("AppLifecycle.quit_application() --> App UI state saved.")

        # terminate workers
        # self.workers.__del__()
        try:
            self.app.pool.terminate()
            self.app.pool.join()
        except (ValueError, AttributeError):
            pass

        self.app.workers.quit()

        # quit app directly - do NOT call back to App
        QtWidgets.QApplication.quit()
        # QApplication.quit() does nothing while the event loop does not run: a --shellfile runs in App.__init__(),
        # before app.exec(), and with only the line above `quit_app` left a hidden application running. The queued
        # quit ends the application as soon as the event loop starts.
        QtCore.QTimer.singleShot(0, QtWidgets.QApplication.quit)
        return True

    # --------------------------------------------------------------------------
    # Method 6: kill_app
    # --------------------------------------------------------------------------
    @staticmethod
    def kill_app():
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.instance().quit()
        # When the main event loop is not started yet in which case the qApp.quit() will do nothing
        # we use the following command
        sys.exit(0)
        # raise SystemExit

    # --------------------------------------------------------------------------
    # Method 7: on_portable_checked
    # --------------------------------------------------------------------------
    def on_portable_checked(self, state):
        """
        Callback called when the checkbox in Preferences GUI is checked.
        It will set the application as portable by creating the preferences and recent files in the
        'config' folder found in the FlatCAM installation folder.

        :param state: boolean, the state of the checkbox when clicked/checked
        :return:
        """
        from PyQt6.QtCore import Qt

        line_no = 0
        data = None

        if sys.platform != 'win32':
            # this won't work in Linux or macOS
            return

        app_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if getattr(sys, 'frozen', False) is True:
            app_root = os.path.dirname(app_root)
        current_data_path = os.path.join(app_root, 'config')
        config_file = os.path.join(current_data_path, 'configuration.txt')
        try:
            with open(config_file, 'r') as f:
                try:
                    data = f.readlines()
                except Exception as e:
                    self.log.error('App.__init__() -->%s' % str(e))
                    return
        except FileNotFoundError:
            pass

        if data is None:
            return

        for line in data:
            line = line.strip('\n')
            param = str(line).rpartition('=')
            if param[0] == 'portable':
                break
            line_no += 1

        if state == Qt.CheckState.Checked:
            if line_no >= len(data):
                data.append('portable=True\n')
            else:
                data[line_no] = 'portable=True\n'
            # create the new defaults files
            # create current_defaults.FlatConfig file if there is none
            try:
                with open(current_data_path + '/current_defaults.FlatConfig'):
                    pass
            except IOError:
                self.log.debug('Creating empty current_defaults.FlatConfig')
                with open(current_data_path + '/current_defaults.FlatConfig', 'w') as f:
                    json.dump({}, f)

            # create factory_defaults.FlatConfig file if there is none
            try:
                with open(current_data_path + '/factory_defaults.FlatConfig'):
                    pass
            except IOError:
                self.log.debug('Creating empty factory_defaults.FlatConfig')
                with open(current_data_path + '/factory_defaults.FlatConfig', 'w') as f:
                    json.dump({}, f)

            try:
                with open(current_data_path + '/recent.json'):
                    pass
            except IOError:
                self.log.debug('Creating empty recent.json')
                with open(current_data_path + '/recent.json', 'w') as f:
                    json.dump([], f)

            try:
                with open(current_data_path + '/recent_projects.json'):
                    pass
            except IOError:
                self.log.debug('Creating empty recent_projects.json')
                with open(current_data_path + '/recent_projects.json', 'w') as fp:
                    json.dump([], fp)

            # save the current defaults to the new defaults file
            self.app.preferencesUiManager.save_defaults(silent=True, data_path=current_data_path)

        else:
            if line_no >= len(data):
                data.append('portable=False\n')
            else:
                data[line_no] = 'portable=False\n'

        with open(config_file, 'w') as f:
            f.writelines(data)

    # --------------------------------------------------------------------------
    # Method 8: on_defaults_dict_change
    # --------------------------------------------------------------------------
    def on_defaults_dict_change(self, field):
        """
        Called whenever a key changed in the "self.options" dictionary. It will set the required GUI element in the
        Edit -> Preferences tab window.

        :param field:   the key of the "self.options" dictionary that was changed.
        :return:        None
        """
        self.app.preferencesUiManager.defaults_write_form_field(field=field)

    # --------------------------------------------------------------------------
    # Method 9: on_defaults2options
    # --------------------------------------------------------------------------
    def on_defaults2options(self):
        """
        Set the project options of a new project from the application defaults
        and show them in the Preferences form.

        :return:    None
        """

        self.options.reset_from_defaults()
        self.app.preferencesUiManager.defaults_write_form()

    # --------------------------------------------------------------------------
    # Method 10: setup_obj_classes
    # --------------------------------------------------------------------------
    def setup_obj_classes(self):
        """
        Sets up application specifics on the FlatCAMObj class. This way the object.app attribute will point to the App
        class.

        :return: None
        """
        # FlatCAMObj.app = self
        # ObjectCollection.app = self
        # Gerber.app = self
        # Excellon.app = self
        # Geometry.app = self
        # CNCjob.app = self
        # FCProcess.app = self
        # FCProcessContainer.app = self
        from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
        OptionsGroupUI.app = self.app

    # --------------------------------------------------------------------------
    # Method 11: version_check
    # --------------------------------------------------------------------------
    def version_check(self, forced=False):
        """
        Checks for the latest version of the program. Alerts the
        user if theirs is outdated. This method is meant to be run
        in a separate thread.

        Manifest parsing now performs the old ``"version" not in data`` guard
        and uses ``data.get("name")`` / ``data.get("message")`` semantics.

        :return: None
        """
        self.log.debug("version_check()")
        from services.updater.provider import checker_for, run_provider_check

        # The checker is rebuilt when the update source preference changed since it was cached.
        checker = checker_for(self.app)
        return run_provider_check(self.app, checker, forced=forced)

    # --------------------------------------------------------------------------
    # Method 12: start_delayed_quit
    # --------------------------------------------------------------------------
    def start_delayed_quit(self, delay, filename, should_quit=None):
        """

        :param delay:           period of checking if project file size is more than zero; in seconds
        :param filename:        the name of the project file to be checked periodically for size more than zero
        :param should_quit:     if the task finished will be followed by an app quit; boolean
        :return:
        """
        from PyQt6.QtCore import QTimer

        if QtCore.QThread.currentThread() is not self.thread():
            # the save task calls this in a worker: a timer of the worker thread fires only when that worker is free
            # again, so a long task queued after the save kept the save in progress (no close, no project switch)
            self.delayed_quit_requested.emit(int(delay), str(filename), should_quit)
            return

        to_quit = should_quit
        self.app.save_timer = QTimer()
        self.app.save_timer.setInterval(delay)
        self.app.save_timer.timeout.connect(lambda: self.check_project_file_size(filename=filename, should_quit=to_quit))
        self.app.save_timer.start()

    # --------------------------------------------------------------------------
    # Method 13: check_project_file_size
    # --------------------------------------------------------------------------
    def check_project_file_size(self, filename, should_quit=None):
        """

        :param filename:        the name of the project file to be checked periodically for size more than zero
        :param should_quit:     will quit the app if True; boolean
        :return:
        """

        try:
            if os.stat(filename).st_size > 0:
                self.app.save_in_progress = False
                self.app.save_timer.stop()
                # a close during the save is used by this save only (the quit can still be cancelled)
                close_requested = getattr(self.app, 'close_after_save', False)
                self.app.close_after_save = False
                if should_quit:
                    self.app.app_quit.emit()
                elif close_requested:
                    # the app was closed during the save: close the main window now, as the user did (this
                    # method can run in a worker thread)
                    QtCore.QMetaObject.invokeMethod(self.app.ui, 'close', QtCore.Qt.ConnectionType.QueuedConnection)
        except Exception:
            traceback.print_exc()

