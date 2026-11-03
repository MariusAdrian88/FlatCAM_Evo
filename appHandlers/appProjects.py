from copy import deepcopy
import os

from PyQt6 import QtCore, QtWidgets

from appHandlers.appHistory import HistoryManager
from appObjects.ProjectContext import ProjectContext, unique_name, same_path, name_from_filename, \
    is_valid_name, PROJECT_EXT
from defaults import project_options, normalize_units

# App Translation
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ProjectManager(QtCore.QObject):
    """
    Holds the projects of the Project tab. Only 1 project is active. The state of the active project is in the
    App attributes, so the existing code that reads app.collection works on the active project.
    """

    # emitted when the list of projects, a name or the active project changes
    projects_changed = QtCore.pyqtSignal()

    def __init__(self, app, collection_factory=None):
        super().__init__()
        self.app = app
        self.collection_factory = collection_factory if collection_factory is not None else self._make_collection

        # 1 tree view for each project; a switch shows the view of the active project
        self.stack = QtWidgets.QStackedWidget()

        first = ProjectContext(name=self.default_name(), collection=app.collection)
        self.contexts = [first]
        self.active = first
        # the project that the canvas shows. It is not always the active project: a switch with visuals=False
        # (used on exit) changes the active project but not the canvas.
        self.displayed = first
        # the project that the last prepare_open() added to the list, or None if it added none
        self.last_created = None
        self.stack.addWidget(first.collection.view)
        self.stack.setCurrentWidget(first.collection.view)
        app.history = self._ensure_history(first)

    def _make_collection(self):
        from appObjects.ObjectCollection import ObjectCollection

        collection = ObjectCollection(app=self.app)
        collection.view.setMinimumWidth(290)
        return collection

    def _ensure_history(self, ctx):
        """Every project has its own Undo / Redo history."""
        if ctx.history is None:
            ctx.history = HistoryManager(self.app, ctx.collection)
        return ctx.history

    # ------------------------------------------------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------------------------------------------------
    def names(self):
        return [ctx.name for ctx in self.contexts]

    def default_name(self):
        used = [ctx.name for ctx in getattr(self, 'contexts', [])]
        return unique_name('%s_1' % _("Project"), used)

    def get_by_name(self, name):
        for ctx in self.contexts:
            if ctx.name == name:
                return ctx
        return None

    def filename_of(self, ctx):
        # the live filename of the active project is in the App
        return self.app.project_filename if ctx is self.active else ctx.filename

    def find_by_filename(self, filename):
        for ctx in self.contexts:
            if same_path(self.filename_of(ctx), filename):
                return ctx
        return None

    def is_editing(self):
        """
        True if an editor is open. A switch then would lose the changes of the editor.
        """
        return getattr(self.app, 'call_source', 'app') != 'app'

    def is_busy(self):
        """
        True if a switch is not allowed now: an editor is open, or a task is queued or runs. A task that ends after
        a switch can add its result to the wrong project.
        """
        app = self.app
        if getattr(app, 'script_state', '') == 'running':
            return True
        if any(not state['finished'] for state in getattr(app, 'script_pending_work', [])):
            return True
        if self.is_editing():
            return True
        # a task counts from the moment it is queued, before its worker starts. A worker that hands its result to
        # the GUI thread (for example the parse of a project, which then starts the restore of the objects) posts
        # that signal before its "task completed" signal, so the next stage is registered before this count is 0.
        workers = getattr(app, 'workers', None)
        if workers is not None and workers.pending_count() > 0:
            return True
        try:
            if app.proc_container.get_procs_count() > 0:
                return True
        except AttributeError:
            pass
        if getattr(app, 'save_in_progress', False) or getattr(app, 'project_save_pending', False):
            return True
        collection = app.collection
        if collection.has_promises() or collection.has_plot_promises():
            return True
        return bool(getattr(getattr(app, 'app_obj', None), 'load_batches', None))

    def inform_busy(self):
        if self.is_editing():
            msg = _("Close the editor first.")
        else:
            msg = _("A task is running. Wait until it ends.")
        self.app.inform.emit('[WARNING_NOTCL] %s' % msg)

    # ------------------------------------------------------------------------------------------------------------
    # Switch
    # ------------------------------------------------------------------------------------------------------------
    def add_context(self, name):
        """
        Add an empty project to the list. It does not become active.
        """
        ctx = ProjectContext(name=unique_name(name, self.names()), collection=self.collection_factory())
        self.contexts.append(ctx)
        self.stack.addWidget(ctx.collection.view)
        self.projects_changed.emit()
        return ctx

    def capture(self):
        """
        Write the live state of the active project from the App back into its context.
        """
        ctx = self.active
        # only the project keys; the application settings stay in app.defaults for all projects
        ctx.options = deepcopy(project_options(self.app.options))
        ctx.filename = self.app.project_filename
        ctx.should_we_save = self.app.should_we_save
        # the canvas shows the view of the displayed project; after a switch with visuals=False that is another
        # project, and its view must not go into this context
        if ctx is self.displayed:
            ctx.view_rect = self._get_view_rect()

    def _load(self, ctx):
        """
        Put the state of the context into the App.
        """
        self.app.collection = ctx.collection
        if ctx.options is not None:
            # copies the project keys from the defaults, then puts the saved project keys over them
            self.app.options.load_project(deepcopy(ctx.options))
        else:
            self.reset_options()
        self.app.project_filename = ctx.filename
        self.app.should_we_save = ctx.should_we_save
        self.app.all_objects_list = ctx.collection.get_list()
        self.stack.setCurrentWidget(ctx.collection.view)
        self.app.history = self._ensure_history(ctx)
        self.app.history.notify()
        self._follow_units()

    def _follow_units(self):
        """The units of the active project are the units of the app: the app value and the status bar follow."""
        units = normalize_units(self.app.options.get('units'))
        if units is None or units == getattr(self.app, 'app_units', None):
            return
        self.app.app_units = units
        label = getattr(getattr(self.app, 'ui', None), 'units_label', None)
        if label is not None:
            label.setText('[%s]' % ('mm' if units == 'MM' else 'in'))

    def switch(self, ctx, visuals=True, check_busy=True):
        """
        Make ctx the active project.

        :param ctx:     the ProjectContext to activate
        :param visuals: if False, the plots and the plugins are not changed (used on exit)
        :param check_busy: if False, the switch does not wait for the running tasks (used by abandon_open)
        :return:        True if ctx is now the active project
        """
        if ctx is self.active:
            return True
        if ctx not in self.contexts:
            return False
        if check_busy and self.is_busy():
            self.inform_busy()
            return False

        self.capture()
        if visuals:
            self.before_switch()
            # release the project that the canvas shows; after a switch with visuals=False it is not the active one
            if self.displayed is not None:
                self.deactivate(self.displayed)

        self.active = ctx
        self._load(ctx)

        if visuals:
            self.activate(ctx)
            self.displayed = ctx

        self.projects_changed.emit()
        self.update_title()
        return True

    def update_title(self):
        self.app.ui.set_ui_title(name=self.active.name)

    # ------------------------------------------------------------------------------------------------------------
    # New, rename, close
    # ------------------------------------------------------------------------------------------------------------
    def new(self, visuals=True):
        """
        Add an empty project and make it active.

        :return:    the new ProjectContext, or None if a task runs
        """
        if self.is_busy():
            self.inform_busy()
            return None

        ctx = self.add_context(self.default_name())
        if not self.switch(ctx, visuals=visuals):
            self._dispose(ctx)
            return None
        return ctx

    def rename(self, ctx, name):
        """
        :return:    True if the name changed. False if the name is not valid or another project uses it.
        """
        name = name.strip() if name else ''
        if not is_valid_name(name):
            return False
        if name in [other.name for other in self.contexts if other is not ctx]:
            return False

        ctx.name = name
        self.projects_changed.emit()
        if ctx is self.active:
            self.update_title()
        return True

    def is_dirty(self, ctx):
        """
        True if the project has unsaved changes. A project with a file is dirty when its flag is set, also when it
        is empty: the deletion of all its objects is a change. A project without a file and without objects has
        nothing to save, the same rule as the exit of the app with 1 project (AppLifecycle.final_save).
        """
        flag = self.app.should_we_save if ctx is self.active else ctx.should_we_save
        if not flag:
            return False
        return bool(ctx.collection.get_list()) or self.filename_of(ctx) is not None

    def dirty_contexts(self):
        return [ctx for ctx in self.contexts if self.is_dirty(ctx)]

    def close(self, ctx, force=False, visuals=True):
        """
        Close and remove a project. There is no dialog.

        :param ctx:     the project to close
        :param force:   if True, a project with unsaved changes is closed and the changes are lost
        :param visuals: passed to switch()
        :return:        True if the project is removed
        """
        if ctx not in self.contexts:
            return False
        if self.is_dirty(ctx) and not force:
            return False
        if self.is_busy():
            self.inform_busy()
            return False

        if ctx is self.active:
            if len(self.contexts) > 1:
                idx = self.contexts.index(ctx)
                target = self.contexts[idx - 1] if idx > 0 else self.contexts[idx + 1]
                if not self.switch(target, visuals=visuals):
                    return False
            elif self.new(visuals=visuals) is None:
                return False

        self._dispose(ctx)
        return True

    def _dispose(self, ctx):
        """
        Remove an inactive project from the list and release its objects.
        """
        if ctx in self.contexts:
            self.contexts.remove(ctx)
        if self.displayed is ctx:
            self.displayed = None

        for obj in ctx.collection.get_list():
            try:
                obj.clear(update=False)
            except Exception as err:
                self.app.log.error("ProjectManager._dispose() --> %s" % str(err))
            try:
                obj.mark_shapes.clear(update=False)
                obj.mark_shapes.parent = None
            except AttributeError:
                pass

        self.stack.removeWidget(ctx.collection.view)
        ctx.collection.view.deleteLater()
        try:
            ctx.collection.deleteLater()
        except AttributeError:
            pass
        ctx.collection = None
        self.projects_changed.emit()

    # ------------------------------------------------------------------------------------------------------------
    # File name rules
    # ------------------------------------------------------------------------------------------------------------
    def save_target(self, ctx=None):
        """
        The file that a Save of the project writes.

        :return:    None if the project has no file (then Save As is needed). The current file if the project name
                    is the file name. Else, '<name>.FlatPrj' in the folder of the current file.
        """
        ctx = ctx if ctx is not None else self.active
        filename = self.filename_of(ctx)
        if not filename:
            return None
        if name_from_filename(filename) == ctx.name:
            return filename
        return os.path.join(os.path.dirname(filename), ctx.name + PROJECT_EXT)

    def adopt_filename(self, filename, keep_file=True):
        """
        The active project now uses this file: its name becomes the file name.
        Called after an open and after a Save As.

        :param keep_file:   if False, only the name changes. The project has no file, so Save asks for a path.
        """
        ctx = self.active
        ctx.filename = filename if keep_file else None
        used = [other.name for other in self.contexts if other is not ctx]
        ctx.name = unique_name(name_from_filename(filename), used)
        self.projects_changed.emit()
        self.update_title()

    # ------------------------------------------------------------------------------------------------------------
    # Steps that change the GUI
    # ------------------------------------------------------------------------------------------------------------
    # these object kinds have no shapes on the canvas
    NO_SHAPES_KINDS = ('script', 'document')

    def reset_options(self):
        """
        A new project copies the project keys from the application defaults. The application settings are the
        same for all projects, so they do not change.
        """
        self.app.on_defaults2options()

    def before_switch(self):
        """
        Remove the canvas helpers of the current project. A switch never runs while an editor is open (is_busy),
        so this method does not close an editor.
        """
        app = self.app
        photocarve = getattr(app, 'photocarve_tool', None)
        if photocarve is not None:
            photocarve.on_project_reset()
        for attr in ('tool_shapes', 'hover_shapes'):
            shapes = getattr(app, attr, None)
            if shapes is not None:
                try:
                    shapes.clear(update=True)
                except Exception as err:
                    app.log.error("ProjectManager.before_switch() %s --> %s" % (attr, str(err)))

        try:
            app.levelling_tool.probing_shapes.clear(update=True)
        except AttributeError:
            pass

        if getattr(app, 'exc_areas', None) is not None:
            app.exc_areas.clear_shapes()

        app.delete_selection_shape()

    def _plot_options(self):
        # Only rendering inputs: usage statistics and other application bookkeeping must not discard plots.
        return {key: value for key, value in self.app.options.items()
                if key.startswith(('gerber_', 'excellon_', 'geometry_', 'cncjob_')) or
                key in ('units', 'units_precision', 'global_tolerance')}

    def deactivate(self, ctx):
        """
        Hide or release the plot shapes of a project. The geometry and the Properties UI stay.
        """
        # the Properties tab can show the UI of an object of this project; replace it first
        self.app.setup_default_properties_tab()

        caching = self.app.options.get('global_project_caching', False)
        # Settings can change while a project is inactive. The existing full-plot signature checks the shapes;
        # this snapshot also rejects them when the application or object appearance settings have changed.
        ctx.plot_options = deepcopy(self._plot_options()) if caching else None
        for obj in ctx.collection.get_list():
            if obj.kind in self.NO_SHAPES_KINDS:
                continue
            try:
                if caching:
                    obj.shapes.visible = False
                    obj._project_plot_options = deepcopy(dict(obj.obj_options))
                else:
                    obj.clear(update=False)
            except Exception as err:
                self.app.log.error("ProjectManager.deactivate() --> %s" % str(err))
            # the Gerber marks and the CNCJob annotations are in their own collections; plotcanvas.redraw() does not
            # draw them again, so they are cleared with an update
            if obj.kind == 'gerber':
                # the mark checkboxes stay in the Properties UI; uncheck them so that they match the canvas
                try:
                    for row in range(obj.ui.apertures_table.rowCount()):
                        mark_cb = obj.ui.apertures_table.cellWidget(row, 5)
                        if mark_cb is not None:
                            mark_cb.set_value(False)
                    obj.clear_mark_all()
                except AttributeError:
                    pass
            try:
                obj.mark_shapes.clear(update=True)
            except AttributeError:
                pass
            try:
                obj.annotation.clear(update=True)
            except AttributeError:
                pass
            # forces a full plot when the project is active again
            if not caching:
                obj._full_plot_sig = None

        self.app.plotcanvas.redraw()

    def activate(self, ctx):
        """
        Bind the plugins to the new collection and plot. The Properties UI of the objects is not built again:
        set_ui() changes the data of a CNCJob and connects the object signals again.
        """
        app = self.app
        app.setup_default_properties_tab()

        # show the project keys of this project in the Preferences form
        app.preferencesUiManager.defaults_write_form()

        # Reinstalling every plugin dominates warm switches, even when no geometry is plotted. Keep their
        # widgets with caching enabled; their normal run() methods reset the forms when opened again.
        if app.options.get('global_project_caching', False) and self.displayed is not None and \
                getattr(app, 'app_plugins', None) is not None:
            for plugin in app.app_plugins:
                plugin.set_project_collection(self.displayed.collection, ctx.collection)
            widget = app.ui.plugin_scroll_area.takeWidget()
            if widget is not None:
                widget.hide()
            index = app.ui.notebook.indexOf(app.ui.plugin_tab)
            if index >= 0:
                app.ui.notebook.removeTab(index)
        else:
            app.init_tools(init_tcl=False)

        if not app.options.get('global_project_caching', False):
            # Turning caching off releases previously retained projects on the next visual switch, including
            # hidden objects of the target project that plot_all() deliberately skips.
            for other in self.contexts:
                if getattr(other, 'plot_options', None) is not None:
                    self.deactivate(other)

        if ctx.collection.get_list():
            if app.options.get('global_project_caching', False):
                options_match = getattr(ctx, 'plot_options', None) == self._plot_options()
                for obj in ctx.collection.get_list():
                    if obj.kind in self.NO_SHAPES_KINDS:
                        continue
                    if not options_match or getattr(obj, '_project_plot_options', None) != dict(obj.obj_options):
                        obj._full_plot_sig = None
                app.plot_manager.plot_all(fit_view=False, reuse_shapes=True)
            else:
                app.plot_all(fit_view=False)
        self._set_view_rect(ctx.view_rect)

    def _get_view_rect(self):
        try:
            from vispy.geometry import Rect
            return Rect(self.app.plotcanvas.view.camera.rect)
        except Exception:
            return None

    def _set_view_rect(self, rect):
        if rect is None:
            return
        try:
            self.app.plotcanvas.view.camera.rect = rect
        except Exception as err:
            self.app.log.error("ProjectManager._set_view_rect() --> %s" % str(err))

    # ------------------------------------------------------------------------------------------------------------
    # Dialogs. Each dialog is 1 method so a test can replace it.
    # ------------------------------------------------------------------------------------------------------------
    def ask_yes_no(self, title, text):
        from PyQt6 import QtGui
        from appGUI.GUIElements import FCMessageBox

        msgbox = FCMessageBox(parent=self.app.ui)
        msgbox.setWindowTitle(title)
        msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText(text)
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Question)
        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        msgbox.setDefaultButton(bt_yes)
        msgbox.exec()
        return msgbox.clickedButton() == bt_yes

    def ask_save(self, ctx):
        """
        :return:    'yes', 'no' or 'cancel'
        """
        from PyQt6 import QtGui
        from appGUI.GUIElements import FCMessageBox

        title = _("Save changes")
        msgbox = FCMessageBox(parent=self.app.ui)
        msgbox.setWindowTitle(title)
        msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText('%s: %s\n%s' % (_("The project has changes"), ctx.name,
                                                  _("Do you want to Save the project?")))
        msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/save_as.png'))
        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msgbox.setDefaultButton(bt_yes)
        msgbox.exec()
        response = msgbox.clickedButton()
        if response == bt_yes:
            return 'yes'
        if response == bt_no:
            return 'no'
        return 'cancel'

    def ask_save_path(self, ctx):
        """
        :return:    the chosen path, or '' if the user cancels
        """
        from appGUI.GUIElements import FCFileSaveDialog

        filename, _f = FCFileSaveDialog.get_saved_filename(
            caption=_("Save Project As ..."),
            directory='%s/%s' % (str(self.app.get_last_save_folder()), ctx.name),
            ext_filter="FlatCAM Project .FlatPrj (*.FlatPrj);; All Files (*.*)")
        return str(filename)

    def return_to(self, ctx):
        """
        Make ctx the active project again after an exit that stops. The save of an inactive project during the exit
        makes it active with switch(visuals=False), so the canvas and the plugins still show ctx. This method puts
        the live state back to ctx, the same as switch(ctx, visuals=False). It does not check is_busy(): the exit
        stops in all cases, and the live collection must be the one that the canvas shows.
        """
        if ctx is self.active or ctx not in self.contexts:
            return
        self.capture()
        self.active = ctx
        self._load(ctx)
        self.projects_changed.emit()
        self.update_title()

    # ------------------------------------------------------------------------------------------------------------
    # Open, close request, save
    # ------------------------------------------------------------------------------------------------------------
    def prepare_open(self, filename):
        """
        Called before a project file is loaded from the GUI. It selects the project that receives the file.

        :return:    True if the load must continue. False if the load must stop.
        """
        self.last_created = None
        if self.is_busy():
            self.inform_busy()
            return False

        existing = self.find_by_filename(filename)
        if existing is not None:
            reload_file = self.ask_yes_no(
                _("Project already open"),
                '%s: %s\n%s' % (_("This project is already open"), existing.name,
                                _("Do you want to overwrite it with the version from the file?")))
            if not self.switch(existing):
                return False
            return reload_file

        # an empty project that was never saved receives the file; no new entry is needed
        active = self.active
        if not active.collection.get_list() and not self.app.project_filename and not self.app.should_we_save:
            return True

        self.last_created = self.new()
        return self.last_created is not None

    def abandon_open(self, created, previous):
        """
        A load failed: remove the project that prepare_open() added and make the previous project active again.
        The switch does not wait for the running tasks: the task of the failed load can still count as running.

        :return:    True if the previous project is active and the added project is removed
        """
        if created is None or created not in self.contexts or previous not in self.contexts or created is previous:
            return False
        if created is self.active and not self.switch(previous, check_busy=False):
            return False
        self._dispose(created)
        return True

    def request_close(self, ctx):
        """
        Close a project from the GUI. If it has unsaved changes, ask the user first.

        :return:    True if the project is removed
        """
        if self.is_busy():
            self.inform_busy()
            return False
        if self.is_dirty(ctx):
            answer = self.ask_save(ctx)
            if answer == 'cancel':
                return False
            if answer == 'yes' and not self.save_context(ctx):
                return False
        return self.close(ctx, force=True)

    def request_close_all(self):
        """
        Close all the projects from the GUI. Each project with unsaved changes asks the user first. A Cancel or a
        failed save stops the close of the remaining projects. The close of the last project adds 1 empty project.

        :return:    True if all the projects are removed
        """
        if self.is_busy():
            self.inform_busy()
            return False
        for ctx in list(self.contexts):
            if not self.request_close(ctx):
                return False
        return True

    def save_context(self, ctx, visuals=True):
        """
        Save a project now, in this thread. If the project is not active, it becomes active first.

        :return:    True if the file is written
        """
        # switch() returns at once for the active project and skips its busy check; a pending save must end first,
        # else 2 saves overlap
        if self.is_busy():
            self.inform_busy()
            return False
        if not self.switch(ctx, visuals=visuals):
            return False

        path = self.save_target(ctx)
        if path is None:
            path = self.ask_save_path(ctx)
            if not path:
                return False
        elif os.path.exists(path) and not same_path(path, self.app.project_filename):
            if not self.ask_yes_no(_("Overwrite"),
                                   '%s: %s\n%s' % (_("The file already exists"), path,
                                                   _("Do you want to overwrite it?"))):
                return False

        if not self.app.f_handlers.save_project_checked(path, silent=True):
            # the save failed: the project keeps its file name and its unsaved changes
            self.app.save_in_progress = False
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The project was not saved"), path))
            return False
        # save_project() starts a timer that resets this flag later; the save is already done here
        self.app.save_in_progress = False
        timer = getattr(self.app, 'save_timer', None)
        if timer is not None:
            timer.stop()

        self.app.project_filename = path
        self.adopt_filename(path)
        self.app.should_we_save = False
        return True
