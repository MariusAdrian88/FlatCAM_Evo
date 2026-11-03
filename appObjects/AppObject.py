# ###########################################################
# FlatCAM: 2D Post-processing for Manufacturing             #
# http://flatcam.org                                        #
# Author: Juan Pablo Caram (c)                              #
# Date: 2/5/2014                                            #
# MIT Licence                                               #
# Modified by Marius Stanciu (2020)                         #
# ###########################################################

from appCommon.Common import GracefulException
from appHandlers.appHistory import history_of
from defaults import convert_length_options, is_project_key
from PyQt6 import QtCore, QtWidgets

from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject

import math
import time
import traceback
import threading
from collections import deque
from copy import deepcopy

# FlatCAM Translation
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


_object_load_context = threading.local()


class ObjectLoadBatch(QtCore.QObject):
    """Count worker, GUI insertion and plot completions independently."""

    batch_event = QtCore.pyqtSignal(str, object)

    # the time that integrate_next() works before it gives the event loop a turn
    INTEGRATE_SLICE = 0.03

    def __init__(self, owner, jobs, finished=None, parent=None, interactive=True, total=None):
        super().__init__(parent if parent is not None else owner)
        self.owner = owner
        self.app = parent.app if parent is not None else owner.app
        self.interactive = interactive
        self.load_context = owner.load_context if interactive else _object_load_context
        self.jobs = jobs
        self.total = jobs
        self.pending = deque()
        self.plots = 0
        self.plotted = False
        self.integrated = 0
        self.last = None
        self.auto_select = False
        self.selection_changed = False
        self.failed = False
        # the user pressed Cancel in the progress dialog: the load is a failed load
        self.cancelled = False
        # the count of the objects to load (None: unknown, the progress dialog shows the count of the files)
        self.object_total = total
        self.completed = False
        self.finished = finished
        connection = QtCore.Qt.ConnectionType.QueuedConnection if interactive else QtCore.Qt.ConnectionType.AutoConnection
        self.batch_event.connect(self.on_event, type=connection)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.integrate_next)
        self.progress = None
        if not interactive:
            # CLI/no-plot restoration still counts queued AppObject insertions,
            # but does not require a GUI presentation or a QObject factory.
            return
        self.selection_model = self.app.collection.view.selectionModel()
        self.selection_model.selectionChanged.connect(self.on_selection_changed)
        self.progress = QtWidgets.QProgressDialog(_("Loading..."), "", 0, total if total else jobs, self.app.ui)
        self.progress.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        if total:
            self.progress.setCancelButtonText(_("Cancel"))
            self.progress.canceled.connect(self.on_cancel)
        else:
            self.progress.setCancelButton(None)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.show()
        # Paint the dialog now; once the workers start parsing they hold the GIL and the first paint lags.
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        owner.load_batches.add(self)

    def on_selection_changed(self, *args):
        self.selection_changed = True

    def on_cancel(self):
        """Stop integrating the objects: the objects that are not yet in the project are dropped."""
        # closing the dialog at the end of the load emits canceled() too
        if self.cancelled or self.completed:
            return
        self.cancelled = True
        self.failed = True
        self.pending.clear()
        self.app.log.warning("Project loading cancelled.")
        self.check_finished()

    def run(self, fcn, params):
        previous = getattr(self.load_context, 'batch', None)
        self.load_context.batch = self
        try:
            result = fcn(*params)
            if result == 'fail' and not self.cancelled:
                self.batch_event.emit('error', 'Batch object creation failed.')
            return result
        except Exception:
            self.batch_event.emit('error', traceback.format_exc())
        finally:
            self.load_context.batch = previous
            self.batch_event.emit('done', None)

    def on_event(self, event, value):
        if event == 'created':
            if not self.cancelled:
                self.pending.append(value)
                self.timer.start()
        elif event == 'done':
            self.jobs -= 1
            if self.progress is not None and self.object_total is None:
                self.progress.setValue(self.total - self.jobs)
            if not self.jobs and self.progress is not None and self.object_total is None:
                # Parsing can finish before queued GUI insertions and plots.
                self.progress.setRange(0, 0)
        elif event == 'plotted':
            self.plots -= 1
            self.plotted = True
        elif event == 'error':
            self.failed = True
            self.app.log.error(value)
        self.check_finished()

    def integrate_next(self):
        """Integrate the pending objects for about INTEGRATE_SLICE seconds, then let the event loop run."""
        deadline = time.perf_counter() + self.INTEGRATE_SLICE
        while self.pending and not self.cancelled:
            args = self.pending.popleft()
            obj, plot, auto_select, callback, params = args
            obj._load_batch = self
            try:
                self.owner.on_object_created(*args)
                self.last = obj
                self.auto_select = self.auto_select or auto_select
            except Exception:
                self.failed = True
                self.app.log.error(traceback.format_exc())
            finally:
                del obj._load_batch
                self.integrated += 1
            if time.perf_counter() >= deadline:
                break

        if self.progress is not None:
            if self.object_total is not None:
                self.progress.setValue(min(self.integrated, self.object_total))
                self.progress.setLabelText('%s %d / %d' % (_("Loading..."), self.integrated, self.object_total))
            else:
                self.progress.setLabelText('%s %d' % (_("Loading..."), self.integrated))
        if not self.pending:
            self.timer.stop()
        self.check_finished()

    def check_finished(self):
        if self.completed or self.jobs or self.pending or self.plots:
            return
        self.completed = True
        self.timer.stop()
        if not self.interactive:
            try:
                if self.finished is not None:
                    self.finished(self.failed)
            finally:
                self.deleteLater()
            return
        self.selection_model.selectionChanged.disconnect(self.on_selection_changed)
        try:
            self.app.all_objects_list = self.app.collection.get_list()
            if self.last is not None and not self.selection_changed and (
                    self.auto_select or self.app.ui.notebook.currentWidget() is self.app.ui.properties_tab):
                self.app.collection.set_all_inactive()
                self.app.collection.set_active(self.last.obj_options['name'])
            self.app.on_notebook_tab_changed()
            if self.plotted:
                # the objects did not redraw the canvas while they were plotted (see GerberObject.plot())
                self.app.plotcanvas.redraw()
                self.app.on_zoom_fit()
        except Exception:
            self.failed = True
            self.app.log.error(traceback.format_exc())
        finally:
            try:
                if self.finished is not None:
                    self.finished(self.failed)
            finally:
                # Project completion registers its source while this batch is
                # still active, so register_recent defers its write as well.
                self.owner.load_batches.discard(self)
                try:
                    if not self.owner.load_batches:
                        self.app.regFK.register_keywords()
                        self.app.flush_recent_items()
                finally:
                    self.progress.close()
                    self.deleteLater()


class AppObject(QtCore.QObject):

    # Emitted by app_obj.new_object() and passes the new object as argument, plot flag.
    # on_object_created() adds the object to the collection, plots on appropriate flag
    # and emits app_obj.new_object_available.
    object_created = QtCore.pyqtSignal(object, bool, bool, object, list)

    # Emitted when a object has been changed (like scaled, mirrored)
    object_changed = QtCore.pyqtSignal(object)

    # Emitted after a transform of the object; the second argument says whether the Properties Offset entry is filled.
    # A worker thread emits it queued, so the Qt widget is written on the GUI thread.
    object_transform_done = QtCore.pyqtSignal(object, bool)

    # Emitted after object has been plotted.
    # Calls 'on_zoom_fit' method to fit object in scene view in main thread to prevent drawing glitches.
    object_plotted = QtCore.pyqtSignal(object)

    plots_updated = QtCore.pyqtSignal()

    def __init__(self, app):
        super(AppObject, self).__init__()
        self.app = app
        self.inform = app.inform
        self.load_context = _object_load_context
        self.load_batches = set()

        # signals that are emitted when object state changes
        self.object_created.connect(self.on_object_created)
        self.object_changed.connect(self.on_object_changed)
        self.object_transform_done.connect(self.on_object_transform_done)
        self.object_plotted.connect(self.on_object_plotted)
        self.plots_updated.connect(self.app.on_plots_updated)

    def _app_option(self, key, fallback=None):
        defaults = getattr(self.app, "defaults", None)
        if defaults is None:
            return self.app.options.get(key, fallback)

        factory_defaults = getattr(defaults, "factory_defaults", {})
        return self.app.options.get(key, defaults.get(key, factory_defaults.get(key, fallback)))

    def new_object(self, kind, name, initialize, plot=True, autoselected=True, callback=None,
                   callback_params=None, record_history=True):
        """
        Creates the object, see _new_object(). When it fails or the user aborts, the promise of the name is removed
        from the collection, so the app does not stay busy waiting for an object that will never come.

        :return:                Either the object or the string 'fail'
        """
        try:
            result = self._new_object(kind, name, initialize, plot, autoselected, callback, callback_params,
                                      record_history)
        except GracefulException:
            self.app.collection.remove_promise(name)
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Aborted. The object was not created."))
            return "fail"
        except BaseException:
            self.app.collection.remove_promise(name)
            raise
        if isinstance(result, str) and result == "fail":
            self.app.collection.remove_promise(name)
        return result

    def _new_object(self, kind, name, initialize, plot=True, autoselected=True, callback=None,
                   callback_params=None, record_history=True):
        """
        Creates a new specialized FlatCAMObj and attaches it to the application,
        this is, updates the GUI accordingly, any other records and plots it.
        This method is thread-safe.

        Notes:
        If the name is in use, the self.collection will modify it when appending it to the collection.
        There is no need to handle
              name conflicts here.

        :param kind:            The kind of object to create. One of 'gerber', 'excellon', 'cncjob' and 'geometry'
        :type kind:             str
        :param name:            Name for the object
        :type name:             str
        :param initialize:      Function to run after creation of the object but before it is attached to the
                                application
                                The function is called with 2 parameters: the new object and the App instance.
        :type initialize:       function
        :param plot:            If to plot the resulting object
        :param autoselected:    if the resulting object is autoselected in the Project tab and therefore in the
                                self.collection
        :param callback:        a method that is launched after the object is created
        :type callback:         function

        :param callback_params: a list of parameters for the parameter: callback
        :type callback_params:  list
        :param record_history:  record a Create step in the Undo / Redo history (False: project open, the rebuild
                                of an object by Undo / Redo, Tcl commands)

        :return:                Either the object or the string 'fail'
        :rtype:                 object
        """

        if callback_params is None:
            callback_params = [None]
        self.app.log.debug("AppObject.new_object()")
        batch = getattr(self.load_context, 'batch', None)
        if batch is not None and batch.cancelled:
            return "fail"
        obj_plot = plot
        obj_autoselected = autoselected

        t0 = time.time()  # Debug

        # ## Create object
        classdict = {
            "gerber":       GerberObject,
            "excellon":     ExcellonObject,
            "cncjob":       CNCJobObject,
            "geometry":     GeometryObject,
            "script":       ScriptObject,
            "document":     DocumentObject
        }

        self.app.log.debug("Calling object constructor...")

        # Object creation/instantiation
        obj = classdict[kind](name, app=self.app)

        # ############################################################################################################
        # adding object PROPERTIES
        # ############################################################################################################
        obj.units = self._app_option("units")
        obj.isHovering = False
        obj.notHovering = True

        # IMPORTANT
        # The key names in defaults and options dictionaries are not random:
        # they have to have in name first the type of the object (geometry, excellon, cncjob and gerber) or how it's
        # called here, the 'kind' followed by an underline. Above the App default values from self.defaults are
        # copied to self.obj_options. After that, below, depending on the type of
        # object that is created, it will strip the name of the object and the underline (if the original key was
        # let's say "excellon_toolchange", it will strip the excellon_) and to the obj.obj_options the key will become
        # "toolchange"

        # ############################################################################################################
        # this section copies the application defaults related to the object to the object OPTIONS
        # ############################################################################################################
        # The options are read once and stored without the change callback: the object has no UI yet.
        # An object that is made from a project entry sets "provided_options" on its initialize function: the entry
        # overwrites those keys, so only the missing ones are filled.
        provided = getattr(initialize, 'provided_options', None) or ()
        names = list(self.app.options)
        sources = {}    # key of the object option -> key of the application option

        for option in names:
            if option.find(kind + "_") == 0 and option[len(kind) + 1:] not in provided:
                sources[option[len(kind) + 1:]] = option

        # add some of the FlatCAM Tools related properties
        # it is done like this to preserve some kind of order in the keys
        tool_option_prefix = obj.tool_option_prefix
        if tool_option_prefix:
            for option in names:
                if option.find(tool_option_prefix) == 0 and option not in provided:
                    sources[option] = option

        # the milling options should be inherited by all manufacturing objects
        if obj.inherits_mill_options:
            for option in names:
                if option.find('tools_') == 0 and option not in provided:
                    sources[option] = option
        fill = {key: self._app_option(option) for key, option in sources.items()}

        # the application keys are in the units of the defaults, the object is in the units of the project
        app_keys = {key: value for key, value in fill.items() if not is_project_key(sources[key])}
        defaults = getattr(self.app, "defaults", None)
        if defaults is not None:
            fill.update(convert_length_options(app_keys, defaults.get('units'), self._app_option("units")))
        dict.update(obj.obj_options, fill)
        # ############################################################################################################
        # ############################################################################################################

        # Initialize as per user request
        # User must take care to implement initialize
        # in a thread-safe way as is likely that we
        # have been invoked in a separate thread.
        t1 = time.time()
        self.app.log.debug("%f seconds before initialize()." % (t1 - t0))

        try:
            return_value = initialize(obj, self.app)
        except GracefulException:
            raise
        except Exception as e:
            msg = '[ERROR_NOTCL] %s' % _("An internal error has occurred. See shell.\n")
            msg += _("Object ({kind}) failed because: {error} \n\n").format(kind=kind, error=str(e))
            msg += traceback.format_exc()
            self.app.inform.emit(msg)
            return "fail"

        t2 = time.time()
        msg = "%s %s. %f seconds executing initialize()." % (_("New object with name:"), name, (t2 - t1))
        self.app.log.debug(msg)
        self.app.inform_shell.emit(msg)

        if return_value == 'fail' or return_value is False:
            self.app.log.debug("Object (%s) parsing and/or geometry creation failed." % kind)
            return "fail"

        # ############################################################################################################
        # Check units and convert if necessary
        # This condition CAN be true because initialize() can change obj.units
        # ############################################################################################################
        converted = self._app_option("units").upper() != obj.units.upper()
        if converted:
            self.app.inform.emit('%s: %s' % (_("Converting units to "), self._app_option("units")))
            obj.convert_units(self._app_option("units"))
            t3 = time.time()
            self.app.log.debug("%f seconds converting units." % (t3 - t2))

        # ############################################################################################################
        # Create the bounding box for the object and then add the results to the obj.obj_options
        # But not for Scripts or for Documents
        # ############################################################################################################
        if kind != 'document' and kind != 'script':
            try:
                # a project file stores the bounds of the object: no pass over all its geometry
                stored = None
                if not converted and getattr(obj, "loaded_from_dict", False):
                    stored = self._stored_bounds(obj)
                xmin, ymin, xmax, ymax = stored if stored is not None else obj.bounds()
                obj.obj_options['xmin'] = xmin
                obj.obj_options['ymin'] = ymin
                obj.obj_options['xmax'] = xmax
                obj.obj_options['ymax'] = ymax
            except Exception as e:
                self.app.log.error("AppObject.new_object() -> The object has no bounds properties. %s" % str(e))
                return "fail"

        self.app.log.debug("Moving new object back to main thread.")

        # ############################################################################################################
        # Move the object to the main thread and let the app know that it is available.
        # ############################################################################################################
        obj.moveToThread(self.app.main_thread)

        # the suspension is read here because the history can be suspended only while new_object runs
        history = history_of(self.app)
        obj._record_history = bool(record_history and history is not None and history.is_recording())

        batch = getattr(self.load_context, 'batch', None)
        if batch is not None:
            if return_value == 'drill_gx2':
                callback = self.app.edit_class.convert_any2excellon
                callback_params = [name]
            batch.batch_event.emit('created', (obj, obj_plot, obj_autoselected, callback, callback_params))
            return "defective" if return_value == "defective" else obj

        if return_value == 'drill_gx2':
            self.object_created.emit(obj, obj_plot, obj_autoselected,
                                     self.app.edit_class.convert_any2excellon,
                                     [name])
            self.app.log.warning("Gerber X2 drill file detected. Converted to Excellon object.")
            self.app.inform.emit('[WARNING] %s' % _("Gerber X2 drill file detected. Converted to Excellon object."))
        else:
            if callback_params is None:
                callback_params = []
            self.object_created.emit(obj, obj_plot, obj_autoselected, callback, callback_params)

        if return_value == "defective":
            return "defective"

        return obj

    @staticmethod
    def _stored_bounds(obj):
        """The bounds that a project file stores in the object options; None if they are missing or not valid."""
        try:
            xmin, ymin, xmax, ymax = [float(obj.obj_options[key]) for key in ('xmin', 'ymin', 'xmax', 'ymax')]
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)) or xmin > xmax or ymin > ymax:
            return None
        return xmin, ymin, xmax, ymax

    def on_object_created(self, obj, plot, auto_select, callback, callback_params):
        """
        Event callback for object creation.
        It will add the new object to the collection. After that it will plot the object in a threaded way

        :param obj:             The newly created FlatCAM object.
        :param plot:            if the newly create object to be plotted
        :param auto_select:     if the newly created object to be autoselected after creation
        :param callback:        a method that is launched after the object is created
        :param callback_params: a list of parameters for the parameter: callback
        :type callback_params:  list
        :return:                None
        """

        t0 = time.time()  # DEBUG
        self.app.log.debug("on_object_created()")

        # #############################################################################################################
        # ###############################  Add the new object to the Collection  ######################################
        # #############################################################################################################
        guard = getattr(obj, '_publish_guard', None)
        if guard is not None:
            del obj._publish_guard
            if not guard():
                self.app.log.debug('Discarding an obsolete generated object before publication.')
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Obsolete generated object discarded."))
                return

        # The Collection might change the name if there is a collision
        self.app.collection.append(obj)
        after_publish = getattr(obj, '_after_publish', None)
        if after_publish is not None:
            del obj._after_publish
            after_publish(obj)

        if getattr(obj, '_record_history', False):
            history_of(self.app).record_create('%s: %s' % (_("New"), obj.obj_options['name']), [obj])

        creation_context = getattr(obj, '_plugin_creation_context', None)
        auto_switch = False
        if creation_context is not None:
            del obj._plugin_creation_context
            source, auto_hide, auto_switch = creation_context
            if auto_hide and source is not None and source is not obj:
                source.obj_options['plot'] = False
                source.visible = False
            if auto_switch:
                auto_select = True

        # after adding the object to the collection always update the list of objects that are in the collection
        batch = getattr(obj, '_load_batch', None)
        if batch is None or not batch.interactive:
            self.app.all_objects_list = self.app.collection.get_list()

        # self.app.inform.emit('[selected] %s created & selected: %s' %
        #                  (str(obj.kind).capitalize(), str(obj.obj_options['name'])))

        # #############################################################################################################
        # ######################  Set colors for the message in the Status Bar  #######################################
        # #############################################################################################################
        status_color = obj.status_color
        if status_color is not None:
            self.app.inform.emit('[selected] {kind} {tx}: <span style="color:{color};">{name}</span>'.format(
                kind=obj.kind.capitalize(),
                color=status_color,
                name=str(obj.obj_options['name']), tx=_("created/selected"))
            )

        # ############################################################################################################
        # Set the colors for the objects that have geometry
        # ############################################################################################################
        if obj.has_plot_colors:
            try:
                if obj.kind == 'gerber' and "gerber_color_list" not in self.app.options:
                    self.app.options["gerber_color_list"] = deepcopy(self._app_option("gerber_color_list", []))
                gerber_color_list = self._app_option("gerber_color_list", [])

                if obj.kind == 'excellon':
                    if self._app_option("excellon_color"):
                        obj.fill_color = self._app_option("excellon_color")[0]
                        obj.outline_color = self._app_option("excellon_color")[1]
                    else:
                        obj.fill_color = self._app_option("excellon_plot_fill")
                        obj.outline_color = self._app_option("excellon_plot_line")

                if obj.kind == 'gerber':
                    if self._app_option("gerber_store_color_list") is True:
                        group = self.app.collection.group_items["gerber"]
                        index = group.child_count() - 1

                        # when loading a Gerber object always create a color tuple (line color, fill_color, layer_name)
                        # and add it to the self.app.options["gerber_color_list"] from where it will be picked and used
                        try:
                            colors = gerber_color_list[index]
                        except IndexError:
                            obj.outline_color = self._app_option("gerber_plot_line")
                            obj.fill_color = self._app_option("gerber_plot_fill")
                            obj.alpha_level = str(hex(int(obj.fill_color[7:9], 16))[2:])
                            colors = (obj.outline_color, obj.fill_color, '%s_%d' % (_("Layer"), int(index)))
                            gerber_color_list.append(colors)

                        new_line_color = colors[0]
                        new_fill = colors[1]
                        new_alpha = str(hex(int(colors[1][7:9], 16))[2:])
                        obj.outline_color = new_line_color
                        obj.fill_color = new_fill
                        obj.alpha_level = new_alpha
                    else:
                        obj.outline_color = self._app_option("gerber_plot_line")
                        obj.fill_color = self._app_option("gerber_plot_fill")
                        obj.alpha_level = str(hex(int(self._app_option('gerber_plot_fill')[7:9], 16))[2:])
            except Exception as e:
                self.app.log.error("AppObject.new_object() -> setting colors error. %s" % str(e))

        if batch is not None:
            pass
        elif auto_select or self.app.ui.notebook.currentWidget() is self.app.ui.properties_tab:
            # select the just opened object but deselect the previous ones
            self.app.collection.set_all_inactive()
            self.app.collection.set_active(obj.obj_options["name"])
        elif plot is True:
            # Single-file import (not batch): deselect all, no new selection
            self.app.collection.set_all_inactive()
        else:
            # else: batch mode (plot=False, auto_select=False) — skip deselection entirely.
            # During batch import, repeatedly deselecting all objects is O(n²) and causes UI freeze.
            # The final plot_all (dispatched with priority='low') will handle the UI state.
            pass

        # here it is done the object plotting
        def plotting_task(t_obj):
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                if t_obj.kind == 'cncjob':
                    t_obj.plot(kind=self._app_option("cncjob_plot_kind"))
                elif t_obj.kind == 'gerber':
                    t_obj.plot(color=t_obj.outline_color, face_color=t_obj.fill_color)
                else:
                    t_obj.plot()

                t1 = time.time()  # DEBUG
                msg = "%f seconds adding object and plotting." % (t1 - t0)
                self.app.log.debug(msg)
                if batch is None:
                    self.object_plotted.emit(t_obj)

                # a Gerber from a project file was already buffered when it was saved
                if t_obj.kind == 'gerber' and self._app_option("gerber_buffering") != 'full' and \
                        self._app_option("gerber_delayed_buffering") and not getattr(t_obj, "loaded_from_dict", False):
                    t_obj.do_buffer_signal.emit()

        # Send to worker
        # self.worker.add_task(worker_task, [self])
        if plot is True:
            if batch is None:
                self.app.worker_task.emit({'fcn': plotting_task, 'params': [obj]})
            else:
                batch.plots += 1

                def batch_plotting_task(t_obj):
                    t_obj.defer_redraw = True
                    try:
                        plotting_task(t_obj)
                    except Exception:
                        batch.batch_event.emit('error', traceback.format_exc())
                    finally:
                        t_obj.defer_redraw = False
                        batch.batch_event.emit('plotted', None)

                self.app.worker_task.emit({'fcn': batch_plotting_task, 'params': [obj]})

        if callback is not None:
            # callback(*callback_params)
            self.app.worker_task.emit({'fcn': callback, 'params': callback_params})

        if creation_context is not None and auto_switch:
            self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

    def on_object_changed(self, obj):
        """
        Called whenever the geometry of the object was changed in some way.
        This requires the update of it's bounding values so it can be the selected on canvas.
        Update the bounding box data from obj.obj_options

        :param obj: the object that was changed
        :return: None
        """

        try:
            xmin, ymin, xmax, ymax = obj.bounds()
        except TypeError:
            return
        obj.obj_options['xmin'] = xmin
        obj.obj_options['ymin'] = ymin
        obj.obj_options['xmax'] = xmax
        obj.obj_options['ymax'] = ymax

        self.app.log.debug("Object changed, updating the bounding box data on self.obj_options")
        # delete the old selection shape
        self.app.delete_selection_shape()
        self.app.should_we_save = True

    def on_object_transform_done(self, obj, set_offset):
        """Fill the Properties Offset entry after a transform; called on the GUI thread by the queued signal."""
        if not set_offset:
            return
        # the transform already emitted object_changed, which stored the fresh bounds in obj_options; reuse them
        # instead of running obj.bounds() a second time
        bounds = None
        try:
            bounds = (float(obj.obj_options['xmin']), float(obj.obj_options['ymin']),
                      float(obj.obj_options['xmax']), float(obj.obj_options['ymax']))
            if not all(math.isfinite(value) for value in bounds):
                bounds = None
        except (AttributeError, KeyError, TypeError, ValueError):
            bounds = None
        try:
            obj.set_offset_values(bounds=bounds)
        except AttributeError:
            pass
        except Exception as e:
            self.app.log.debug("AppObject.on_object_transform_done() --> %s" % str(e))

    def on_object_plotted(self):
        """
        Callback called whenever the plotted object needs to be fit into the viewport (canvas)

        :return: None
        """
        self.app.on_zoom_fit()

    def new_excellon_object(self, new_name=None):
        """
        Creates a new, blank Excellon object.

        :param new_name:    new name for the new Geometry object
        :type new_name:     str
        :return:            None
        """

        outname = 'new_exc' if new_name is None else new_name

        def obj_init(new_obj, app_obj):
            new_obj.tools = {}
            new_obj.source_file = ''
            new_obj.solid_geometry = []

        if self.new_object('excellon', outname, obj_init, plot=False) == 'fail':
            return 'fail'

    def new_geometry_object(self, new_name=None):
        """
        Creates a new, blank and single-tool Geometry object.

        :param new_name:    new name for the new Geometry object
        :type new_name:     str
        :return:            None
        """

        outname = 'new_geo' if new_name is None else new_name

        def initialize(new_obj, app):
            new_obj.multitool = True
            new_obj.multigeo = True

            # store here the default data for Geometry Data
            default_data = {}
            for opt_key, opt_val in app.options.items():
                if opt_key.find('geometry' + "_") == 0:
                    oname = opt_key[len('geometry') + 1:]
                    default_data[oname] = self._app_option(opt_key)
                if opt_key.find('tools_') == 0:
                    default_data[opt_key] = self._app_option(opt_key)

            new_obj.tools = {
                1: {
                    'tooldia':          float(self._app_option("tools_mill_tooldia")),
                    'offset':           'Path',
                    'offset_value':     0.0,
                    'type':             'Rough',
                    'tool_type':        'C1',
                    'data':             deepcopy(default_data),
                    'solid_geometry':   []
                }
            }

            new_obj.tools[1]['data']['name'] = outname
            new_obj.solid_geometry = []
            new_obj.source_file = ''

        if self.new_object('geometry', outname, initialize, plot=False) == 'fail':
            return 'fail'

    def new_gerber_object(self, new_name=None):
        """
        Creates a new, blank Gerber object.

        :param new_name:    new name for the new Geometry object
        :type new_name:     str
        :return:            None
        """

        outname = 'new_grb' if new_name is None else new_name

        def initialize(new_obj, app):
            new_obj.multitool = False
            new_obj.source_file = ''
            new_obj.multigeo = False
            new_obj.follow = False
            new_obj.tools = {}
            new_obj.solid_geometry = []
            new_obj.follow_geometry = []

            try:
                new_obj.obj_options['xmin'] = 0
                new_obj.obj_options['ymin'] = 0
                new_obj.obj_options['xmax'] = 0
                new_obj.obj_options['ymax'] = 0
            except KeyError:
                pass

        if self.new_object('gerber', outname, initialize, plot=False) == 'fail':
            return 'fail'

    def new_script_object(self, new_name=None):
        """
        Creates a new, blank TCL Script object.

        :param new_name:    new name for the new Geometry object
        :type new_name:     str
        :return:            None
        """

        outname = 'new_script' if new_name is None else new_name

        # commands_list = "# AddCircle, AddPolygon, AddPolyline, AddRectangle, AlignDrill, " \
        #                 "AlignDrillGrid, Bbox, Bounds, ClearShell, CopperClear,\n" \
        #                 "# Cncjob, Cutout, Delete, Drillcncjob, ExportDXF, ExportExcellon, ExportGcode,\n" \
        #                 "# ExportGerber, ExportSVG, Exteriors, Follow, GeoCutout, GeoUnion, GetNames,\n" \
        #                 "# GetSys, ImportSvg, Interiors, Isolate, JoinExcellon, JoinGeometry, " \
        #                 "ListSys, MillDrills,\n" \
        #                 "# MillSlots, Mirror, New, NewExcellon, NewGeometry, NewGerber, Nregions, " \
        #                 "Offset, OpenExcellon, OpenGCode, OpenGerber, OpenProject,\n" \
        #                 "# Options, Paint, Panelize, PlotAl, PlotObjects, SaveProject, " \
        #                 "SaveSys, Scale, SetActive, SetSys, SetOrigin, Skew, SubtractPoly,\n" \
        #                 "# SubtractRectangle, Version, WriteGCode\n"

        new_source_file = '# %s\n' % _('CREATE A NEW TCL SCRIPT') + \
                          '# %s:\n' % _('TCL Tutorial is here') + \
                          '# https://www.tcl.tk/man/tcl8.5/tutorial/tcltutorial.html\n' + '\n\n' + \
                          '# %s:\n' % _("Commands list")
        new_source_file += '# %s\n\n' % _("Type >help< followed by Run Code for a list of Tcl Commands "
                                          "(displayed in Tcl Shell).")

        def initialize(new_obj, app):
            new_obj.source_file = deepcopy(new_source_file)

        self.new_object('script', outname, initialize, plot=False)

    def new_document_object(self, new_name=None):
        """
        Creates a new, blank Document object.

        :param new_name:    new name for the new Geometry object
        :type new_name:     str
        :return:            None
        """

        outname = 'new_document' if new_name is None else new_name

        def initialize(new_obj, app):
            new_obj.source_file = ""

        self.new_object('document', outname, initialize, plot=False)
