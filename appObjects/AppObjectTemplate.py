# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File modified by: Marius Stanciu                         #
# ##########################################################

from appHandlers.appHistory import history_of
from PyQt6 import QtCore, QtGui, QtWidgets

from appGUI.ObjectUI import ObjectUI
from appCommon.Common import LoudDict
from camlib import app_option, Geometry, affine_translate_matrix, affine_scale_matrix
from appHandlers.appObjectOps import history_guard
from appObjects.ProjectContext import unique_name
from appGUI.VisPyVisuals import ShapeCollection

from shapely.ops import unary_union
from shapely import Polygon, MultiPolygon, Point, LineString

from copy import deepcopy, copy
import sys
import uuid
import math
import sys
import numpy as np

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


# Interrupts plotting process if FlatCAMObj has been deleted
class ObjectDeleted(Exception):
    pass


class ValidationError(Exception):
    def __init__(self, message, errors):
        super().__init__(message)

        self.errors = errors


class OffsetUiMixin:
    """
    Fills the Offset entry of the common ObjectUI section with the center of the object bounds. For the object classes
    that have `bounds()` and that show the common section (Gerber, Excellon, Geometry).
    """

    def set_offset_values(self, bounds=None):
        xmin, ymin, xmax, ymax = self.bounds() if bounds is None else bounds
        center_coords = (
            self.app.dec_format((xmin + abs((xmax - xmin) / 2)), self.decimals),
            self.app.dec_format((ymin + abs((ymax - ymin) / 2)), self.decimals)
        )
        self.ui.offsetvector_entry.set_value(str(center_coords))


class PropertiesPanelMixin:
    """
    The Properties tree of the selected-object tab. For the object classes whose ObjectUI has the `info_frame` and
    `treeWidget` widgets (Gerber, Excellon, Geometry, CNCJob); it needs `add_properties_items` from FlatCAMObj.
    """

    def on_properties(self, state):
        if state:
            self.ui.info_frame.show()
        else:
            self.ui.info_frame.hide()
            return

        self.ui.treeWidget.clear()
        self.add_properties_items(obj=self, treeWidget=self.ui.treeWidget)
        self.ui.treeWidget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                         QtWidgets.QSizePolicy.Policy.MinimumExpanding)

        # make sure that the FCTree widget columns are resized to content
        self.ui.treeWidget.resize_sig.emit()

    def on_properties_expanded(self):
        for col in range(self.treeWidget.columnCount()):
            self.ui.treeWidget.resizeColumnToContents(col)


class FlatCAMObj(QtCore.QObject):
    """
    Base type of objects handled in FlatCAM. These become interactive
    in the appGUI, can be plotted, and their options can be modified
    by the user in their respective forms.
    """

    # Instance of the application to which these are related.
    # The app should set this value.
    app = None

    # Dispatch metadata, read by AppObject, ObjectCollection and appPlotManager instead of kind chains.
    # status_color: color of the object name in the status bar message (None: no message).
    status_color = None
    # tool_option_prefix: extra application options (with this prefix) copied into obj_options on creation.
    tool_option_prefix = None
    # inherits_mill_options: the tools_mill_* and tools_* options are copied into obj_options on creation.
    inherits_mill_options = False
    # has_plot_colors: the object has user settable fill / outline plot colors.
    has_plot_colors = False
    # plot_default_fill_option / plot_default_line_option: option keys of the colors restored by "Default".
    plot_default_fill_option = None
    plot_default_line_option = None
    # alpha_fill_option: option key whose color alpha is the default alpha level.
    # default_alpha_level: fixed default alpha level when alpha_fill_option is None.
    alpha_fill_option = None
    default_alpha_level = None
    # source_exporter: name of the appIO export method that writes the text of the object (the source_file) in local
    # use: 'export_gerber', 'export_excellon', 'export_dxf'. None: the object has no regenerable source text.
    source_exporter = None
    # emitted by on_options_change with the key of the option that changed
    optionChanged = QtCore.pyqtSignal(str)

    # signal to plot a single object
    plot_single_object = QtCore.pyqtSignal()

    # signal for Properties
    calculations_finished = QtCore.pyqtSignal(float, float, float, float, float, object)

    def _app_option(self, key, fallback=None):
        return app_option(self.app, key, fallback)

    def regenerate_source(self, name=None, *, store=True):
        """
        Rewrites self.source_file after an operation changed the geometry of the object (move, transform, edit,
        origin change, ...). The single place of the ``obj.source_file = f_handlers.export_xxx(...)`` call that
        was repeated at about 55 sites.

        Specification:

        - Which export: ``self.source_exporter`` (class attribute): GerberObject -> ``export_gerber``,
          ExcellonObject -> ``export_excellon``, GeometryObject -> ``export_dxf``; every other kind has None and
          this method does nothing and returns False.
        - Local use only: ``self.app.f_handlers.<exporter>(obj_name=name, filename=None, local_use=self,
          use_thread=False)``. Nothing is written to disk and no file signal is emitted; the export returns
          the text. ``name`` only goes into the ``Filename:`` comment of the header and defaults to
          ``obj_options['name']``.
        - Threading: always ``use_thread=False``. With ``use_thread=True`` the export runs in a worker task and
          a local-use call returns None, so the text would never reach source_file.
        - Units: the text is a self-contained file written in the export preference units (``gerber_exp_*`` /
          ``excellon_exp_*``, it declares them in its header), converted from the object units (the app units;
          the object units for a PCB-document import). This is the format that 'View source', 'Save source' and the
          project file expect, so the object units are not used.
        - Failure: when the export returns 'fail' or None (an exception inside the export, a wrong object type),
          source_file keeps its old value, a warning is logged and False is returned. (The old call sites stored
          'fail' or None as the source text.)

        :param name: the name for the header of the exported text; None: the name of the object
        :param store: False returns generated text without assigning source_file, for queued GUI completion
        :return:     True if source_file was rewritten (the generated text with store=False),
                     False if the kind has no exporter or the export failed
        """
        exporter = self.source_exporter
        if exporter is None:
            return False

        if name is None:
            name = self.obj_options['name']

        result = getattr(self.app.f_handlers, exporter)(obj_name=name, filename=None, local_use=self,
                                                        use_thread=False)
        if result is None or (isinstance(result, str) and result == 'fail'):
            self.app.log.warning("FlatCAMObj.regenerate_source() --> %s failed for %s, the old source_file is kept." %
                                 (exporter, name))
            return False

        if not store:
            return result
        self.source_file = result
        return True

    def __init__(self, name, app):
        """
        Constructor.

        :param name: Name of the object given by the user.
        :param app: The application instance.
        :return: FlatCAMObj
        """

        if isinstance(self, Geometry):
            # PyQt6 forwards the keyword to the next class in the MRO, the camlib base. The object classes run that
            # __init__ first, with their own arguments; the forwarded call returns at once (idempotence guard).
            super().__init__(app=app)
        else:
            # no camlib base follows QObject (ScriptObject, DocumentObject): an 'app' keyword raises TypeError
            super().__init__()

        self.app = app

        # View
        self.ui = None

        # set True by the collection.append() when the object load is complete
        self.load_complete = None

        self.obj_options = LoudDict(name=name)
        self.obj_options.set_change_callback(self.on_options_change)

        self.form_fields = {}

        # store here the default data for Geometry Data
        self.default_data = {}

        self.kind = None  # Override with proper name

        self.shapes = self.app.plotcanvas.new_shape_group()
        self.mark_shapes = self.app.plotcanvas.new_shape_collection(layers=1)

        self.item = None  # Link with project view item

        # signature of the shapes made by the last full plot; see mark_full_plot() and show_plotted_shapes()
        self._full_plot_sig = None

        self.muted_ui = False
        self.deleted = False

        # True while the plot runs in a load batch: the batch redraws the canvas once, when all the objects are plotted
        self.defer_redraw = False

        # True for an object that from_dict() filled: its stored state (bounds, buffered geometry) is trusted
        self.loaded_from_dict = False

        try:
            self._drawing_tolerance = float(self._app_option("global_tolerance")) if \
                self._app_option("global_tolerance") else 0.001
        except ValueError:
            self._drawing_tolerance = 0.001

        self.isHovering = False
        self.notHovering = True

        # Flag to show if a selection shape is drawn
        self.selection_shape_drawn = False

        # self.units = 'IN'
        self.units = self.app.app_units

        # this is the treeWidget from the UI; it is updated when the add_properties_items() method is called
        self.treeWidget = None

        # a stable id for the Undo / Redo history: an object that Undo makes again (after a delete) keeps it
        self.history_uid = uuid.uuid4().hex

        self.plot_single_object.connect(self.single_object_plot)

    def __del__(self):
        pass

    def __str__(self):
        return "<FlatCAMObj({:12s}): {:20s}>".format(self.kind, self.obj_options["name"])

    def from_dict(self, d):
        """
        This supersedes ``from_dict`` in derived classes. Derived classes
        must inherit from FlatCAMObj first, then from derivatives of Geometry.

        ``self.obj_options`` is only updated, not overwritten. This ensures that
        options set by the app do not vanish when reading the objects
        from a project file.

        :param d: Dictionary with attributes to set.
        :return: None
        """

        self.loaded_from_dict = True

        # the history id of a rebuilt object (an old project has none: the object keeps its new id)
        if d.get('history_uid'):
            self.history_uid = d['history_uid']

        for attr in self.ser_attrs:

            if attr in ('options', 'obj_options'):
                self.obj_options.update(d.get('obj_options', d.get('options', {})))
            else:
                try:
                    setattr(self, attr, d[attr])
                except KeyError:
                    self.app.log.debug("FlatCAMObj.from_dict() --> KeyError: %s. "
                                       "Means that we are loading an old project that don't"
                                       "have all attributes in the latest application version." % str(attr))
                    pass
        from appCommon.solderpaste import restore_paste_metadata
        restore_paste_metadata(self, d)

    def change_level(self, level):
        """

        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """

        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def random_color(self):
        """
        Return a random opaque color as an RGBA numpy array for VisPy.
        """
        r_color = np.random.rand(4)
        r_color[3] = 1
        return r_color

    def on_options_change(self, key):
        # Update form on programmatically options change
        self.set_form_item(key)

        # Set object visibility for objects that are edited
        if key == 'plot' and self.app.call_source != 'app':
            self.visible = self.obj_options['plot']

        self.optionChanged.emit(key)

    def plot(self, kind=None):
        """
        Plot this object (Extend this method to implement the actual plotting).
        Call this in descendants before doing the plotting.

        :param kind:    Used by only some of the FlatCAM objects
        :return:        Whether to continue plotting or not depending on the "plot" option. Boolean
        """
        # log.debug(sys._getframe(1).f_code.co_name + " --> FlatCAMObj.plot()")

        if self.deleted:
            return False

        self.clear()
        self._full_plot_sig = None
        return True

    def plot_transformed(self, matrix, kind=None):
        """
        Update the plot after an affine transform of the geometry of this object, without a new triangulation:
        the vertex arrays of the shapes of the last full plot are mapped by the matrix. If that is not possible
        (no full plot, text annotations, a shape without buffers) it plots the object again.
        Like plot(), it runs in the calling thread (the plugins call it from a worker thread).
        The simplification tolerance of the last plot stays as it was (it was applied before the transform).

        :param matrix:  shapely 6-tuple (a, b, d, e, xoff, yoff) that was applied to the geometry
        :param kind:    passed to plot() on fallback
        :return:        True if the shapes were mapped, False if plot() was called
        """
        if self.deleted:
            return False

        done = False
        try:
            annotation = getattr(self, 'annotation', None)
            has_text = annotation is not None and getattr(annotation, '_index', None) is not None
            if not has_text and self._has_full_plot() and hasattr(self.shapes, 'transform'):
                # the shapes keep their keys, so the signature of the full plot stays valid
                done = bool(self.shapes.transform(matrix, update=True))
        except Exception as e:
            self.app.log.debug("FlatCAMObj.plot_transformed() --> %s" % str(e))
            done = False

        if not done:
            self.plot(kind=kind) if kind is not None else self.plot()
        return done

    def _shapes_signature(self):
        try:
            idx = self.shapes._indexes
        except AttributeError:
            return None
        if not idx:
            return None
        return len(idx), idx[0], idx[-1]

    def mark_full_plot(self):
        """
        Call this at the end of a plot() that added all the shapes of the object, with the current options.
        After this, show_plotted_shapes() can show or hide these shapes without a new plot.

        :return: None
        """
        self._full_plot_sig = self._shapes_signature()

    def show_plotted_shapes(self, visible, kind=None):
        """
        Show or hide the shapes of the last full plot. There is no new tessellation of the geometry,
        so this is much faster than plot() for big objects.

        :param visible: True to show the shapes, False to hide them
        :param kind:    Used by only some of the FlatCAM objects
        :return:        True if done. False if the shapes are not the last full plot; then call plot().
        """
        if not self._has_full_plot():
            return False

        self.shapes.visible = visible
        return True

    def bring_to_top(self):
        """
        Draw the shapes of the last full plot over the shapes of the other objects. There is no new tessellation
        of the geometry, so this is much faster than plot() for big objects.

        :return:        True if done. False if the shapes are not the last full plot; then call plot().
        """
        if not self._has_full_plot():
            return False

        self.shapes.move_to_top()
        return True

    def _has_full_plot(self):
        # True if the shapes of the last full plot are still on the canvas
        sig = self._shapes_signature()
        if sig is None or sig != self._full_plot_sig:
            return False

        # a clear of all the canvas removes the shapes but not the keys in the group
        data = self.shapes._collection.data
        return sig[1] in data and sig[2] in data

    def single_object_plot(self):
        def plot_task():
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                self.plot()
            self.app.app_obj.object_changed.emit(self)

        self.app.worker_task.emit({'fcn': plot_task, 'params': []})

    def add_shape(self, **kwargs):
        tol = kwargs['tolerance'] if 'tolerance' in kwargs else self.drawing_tolerance

        if self.deleted:
            raise ObjectDeleted()
        else:
            key = self.shapes.add(tolerance=tol, **kwargs)
        return key

    def add_shapes_batch(self, shapes_data, **kwargs):
        """
        Batch version of add_shape(). Submits all shapes in one pool call.

        :param shapes_data: list of dicts with keys: 'shape', 'color', 'face_color', 'alpha', 'layer', 'tolerance'
        :param kwargs: passed to ShapeCollectionVisual.add_batch()
        :return: list of shape keys
        """
        tol = kwargs.pop('tolerance', self.drawing_tolerance)

        if self.deleted:
            raise ObjectDeleted()

        return self.shapes.add_batch(shapes_data, tolerance=tol, **kwargs)

    def add_mark_shape(self, **kwargs):
        tol = kwargs['tolerance'] if 'tolerance' in kwargs else self.drawing_tolerance

        if self.deleted:
            raise ObjectDeleted()
        else:
            key = self.mark_shapes.add(tolerance=tol, layer=0, **kwargs)
        return key

    @property
    def visible(self):
        """
        This property is used by Editors to turn off plotting for the original object that is edited,
        such that when deleting certain elements there is no background plot in place to confuse things.
        :return:
        :rtype:
        """
        return self.shapes.visible

    @visible.setter
    def visible(self, value, threaded=True):
        self.app.log.debug("FlatCAMObj.visible()")

        # self.shapes.enabled = not self.shapes.enabled

        current_visibility = self.shapes.visible
        self.shapes.visible = current_visibility   # maybe this is slower in VisPy? use enabled property?

        def task(visibility):
            if visibility is True:
                if value is False:
                    self.shapes.visible = False
            else:
                if value is True:
                    self.shapes.visible = True

            # Not all object types has annotations
            try:
                self.annotation.visible = value
            except Exception:
                pass

        if threaded:
            self.app.worker_task.emit({'fcn': task, 'params': [current_visibility]})
        else:
            task(current_visibility)

    def clear(self, update=False):
        self.shapes.clear(update)

        # Not all object types has annotations
        try:
            self.annotation.clear(update)
        except Exception:
            pass

    def set_ui(self, ui):
        self.ui = ui

        self.form_fields = {"name": self.ui.name_entry}

        assert isinstance(self.ui, ObjectUI)
        self.ui.name_entry.returnPressed.connect(self.on_name_activate)

        try:
            # it will raise an exception for those FlatCAM objects that do not build UI with the common elements
            self.ui.offset_button.clicked.connect(self.on_offset_button_click)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.scale_button.clicked.connect(self.on_scale_button_click)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.offsetvector_entry.returnPressed.connect(self.on_offset_button_click)
        except (TypeError, AttributeError):
            pass

        # Creates problems on focusOut
        try:
            self.ui.scale_entry.returnPressed.connect(self.on_scale_button_click)
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.transformations_button.clicked.connect(lambda: self.app.transform_tool.run(toggle=True))
        except (TypeError, AttributeError):
            pass

        # self.ui.skew_button.clicked.connect(self.on_skew_button_click)

    def build_ui(self):
        """
        Sets up the UI/form for this object. Show the UI in the App.

        :return: None
        """

        # during a project load the Properties tab is built for the selected object when the load ends
        if getattr(self, '_load_batch', None) is not None:
            return

        self.muted_ui = True
        self.app.log.debug(sys._getframe(1).f_code.co_name + "--> FlatCAMObj.build_ui()")

        try:
            # HACK: disconnect the scale entry signal since on focus out event will trigger an undesired scale()
            # it seems that the takewidget() does generate a focus out event for the QDoubleSpinbox ...
            # and reconnect after the takeWidget() is done
            # self.ui.scale_entry.returnPressed.disconnect(self.on_scale_button_click)
            self.app.ui.properties_scroll_area.takeWidget()
            # self.ui.scale_entry.returnPressed.connect(self.on_scale_button_click)
        except Exception as e:
            self.app.log.error("FlatCAMObj.build_ui() --> Nothing to remove: %s" % str(e))

        try:
            self.app.ui.properties_scroll_area.setWidget(self.ui)
        except RuntimeError:
            try:
                self.app.ui.properties_scroll_area.setWidget(self.ui)
            except Exception:
                pass
        # self.ui.setMinimumWidth(100)
        # self.ui.setMaximumWidth(self.app.ui.properties_tab.sizeHint().width())

        self.muted_ui = False

    def on_name_activate(self, silent=None, new_name=None):
        old_name = copy(self.obj_options["name"])
        if new_name is None:
            new_name = self.ui.name_entry.get_value()

        if new_name == '':
            # no object without a name (the Project tree does not allow it either)
            new_name = old_name
        elif new_name != old_name:
            # the name of another object gets a number, as for a new object (ObjectCollection.append()): the lookups
            # by name (the Objects menu, Tcl, the plugin combos) find only the first object with a name
            new_name = unique_name(new_name, {obj.obj_options['name'] for obj in self.app.collection.get_list()
                                              if obj is not self})

        if new_name != old_name:
            # update the SHELL auto-completer model data
            try:
                self.app.regFK.remove_keyword(old_name, update=False)
                self.app.regFK.prepend_keyword(new_name)
                self.app.ui.code_editor.set_model_data(self.app.regFK.myKeywords)
            except Exception:
                self.app.log.debug(
                    "on_name_activate() --> Could not remove the old object name from auto-completer model list")

            history_of(self.app).record_rename(self, old_name, new_name)
            self.obj_options["name"] = new_name
            self.default_data["name"] = new_name
            try:
                self.ui.name_entry.set_value(new_name)
            except (AttributeError, RuntimeError):
                pass
            self.app.collection.update_view(self)
            if silent:
                self.app.inform.emit('[success] %s: %s %s: %s' % (
                    _("Name changed from"), str(old_name), _("to"), str(new_name)
                )
                                     )
        else:
            # the entry shows the name that the object keeps
            try:
                self.ui.name_entry.set_value(old_name)
            except (AttributeError, RuntimeError):
                pass

    def on_offset_button_click(self):
        self.app.defaults.report_usage("obj_on_offset_button")

        self.read_form()
        vector_val = self.ui.offsetvector_entry.get_value()

        def worker_task():
            with self.app.proc_container.new(_("Offsetting...")):
                with history_guard(history_of(self.app), _("Offset"), self) as before:
                    result = self.offset(vector_val)
                try:
                    dx, dy = vector_val
                    if result != 'fail' and (dx != 0 or dy != 0):
                        history_of(self.app).record_matrix(
                            _("Offset"), [(self, affine_translate_matrix(dx, dy), before)])
                except (TypeError, ValueError):
                    pass
            self.app.proc_container.update_view_text('')
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                self.plot()
            self.app.app_obj.object_changed.emit(self)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def on_scale_button_click(self):
        self.read_form()
        try:
            factor = float(self.ui.scale_entry.get_value())
        except Exception as e:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Scaling could not be executed."))
            self.app.log.error("FlatCAMObj.on_scale_button_click() -- %s" % str(e))
            return

        if type(factor) != float:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Scaling could not be executed."))

        # if factor is 1.0 do nothing, there is no point in scaling with a factor of 1.0
        if factor == 1.0:
            self.app.inform.emit('[success] %s' % _("Scale done."))
            return

        self.app.log.debug("FlatCAMObj.on_scale_button_click()")

        def worker_task():
            with self.app.proc_container.new(_("Scaling...")):
                # scale() of every object kind scales around (0, 0) when no point is given
                with history_guard(history_of(self.app), _("Scale"), self) as before:
                    result = self.scale(factor)
                if result != 'fail':
                    history_of(self.app).record_matrix(
                        _("Scale"), [(self, affine_scale_matrix(factor, factor, (0, 0)), before)])
                self.app.inform.emit('[success] %s' % _("Scale done."))

            self.app.proc_container.update_view_text('')
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                self.plot()
            self.app.app_obj.object_changed.emit(self)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def on_skew_button_click(self):
        self.app.defaults.report_usage("obj_on_skew_button")
        self.read_form()
        x_angle = self.ui.xangle_entry.get_value()
        y_angle = self.ui.yangle_entry.get_value()

        def worker_task():
            with self.app.proc_container.new(_("Skewing...")):
                # skew() takes its origin inside the object kind: a snapshot does not depend on it
                with history_of(self.app).snapshot(_("Skew"), [self]):
                    self.skew(x_angle, y_angle)
            self.app.proc_container.update_view_text('')
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                self.plot()
            self.app.app_obj.object_changed.emit(self)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def to_form(self):
        """
        Copies options to the UI form.

        :return: None
        """
        self.app.log.debug(sys._getframe(1).f_code.co_name + " --> FlatCAMObj.to_form()")
        for option in self.obj_options:
            try:
                self.set_form_item(option)
            except Exception as err:
                self.app.log.error("Unexpected error: %s" % str(sys.exc_info()), str(err))

    def read_form(self):
        """
        Reads form into ``self.obj_options``.

        :return: None
        :rtype: None
        """
        self.app.log.debug(sys._getframe(1).f_code.co_name + "--> FlatCAMObj.read_form()")
        for option in self.obj_options:
            try:
                self.read_form_item(option)
            except Exception:
                self.app.log.warning("Unexpected error: %s" % str(sys.exc_info()))

    def set_form_item(self, option):
        """
        Copies the specified option to the UI form.

        :param option: Name of the option (Key in ``self.obj_options``).
        :type option: str
        :return: None
        """

        try:
            self.form_fields[option].set_value(self.obj_options[option])
        except KeyError:
            # self.app.log.warn("Tried to set an option or field that does not exist: %s" % option)
            pass
        except (AttributeError, RuntimeError):
            if option != 'name':
                raise

    def read_form_item(self, option):
        """
        Reads the specified option from the UI form into ``self.obj_options``.

        :param option: Name of the option.
        :type option: str
        :return: None
        """
        try:
            self.obj_options[option] = self.form_fields[option].get_value()
        except KeyError:
            pass
            # self.app.log.warning("Failed to read option from field: %s" % option)

    def serialize(self):
        """
        Returns a representation of the object as a dictionary so
        it can be later exported as JSON. Override this method.

        :return: Dictionary representing the object
        :rtype: dict
        """
        return

    def deserialize(self, obj_dict):
        """
        Re-builds an object from its serialized version.

        :param obj_dict: Dictionary representing a FlatCAMObj
        :type obj_dict: dict
        :return: None
        """
        return

    def update_filters(self, last_ext, filter_string):
        """
        Will modify the filter string that is used when saving a file (a list of file extensions) to have the last
        used file extension as the first one in the special string

        :param last_ext:        The file extension that was last used to save a file
        :param filter_string:   A key in self.app.options that holds a string with the filter from QFileDialog
        used when saving a file
        :return:                None
        """

        filters = copy(self._app_option(filter_string))
        filter_list = filters.split(';;')
        filter_list_enum_1 = enumerate(filter_list)

        # search for the last element in the filters which should always be "All Files (*.*)"
        last_elem = ''
        for elem in list(filter_list_enum_1):
            if '(*.*)' in elem[1]:
                last_elem = filter_list.pop(elem[0])

        filter_list_enum = enumerate(filter_list)
        for elem in list(filter_list_enum):
            if '.' + last_ext in elem[1]:
                used_ext = filter_list.pop(elem[0])

                # sort the extensions back
                filter_list.sort(key=lambda x: x.rpartition('.')[2])

                # add as a first element the last used extension
                filter_list.insert(0, used_ext)
                # add back the element that should always be the last (All Files)
                filter_list.append(last_elem)

                self.app.options[filter_string] = ';;'.join(filter_list)
                return

    def add_properties_items(self, obj, treeWidget):
        self.treeWidget = treeWidget
        parent = self.treeWidget.invisibleRootItem()
        apertures = ''
        tools = ''
        drills = ''
        slots = ''
        others = ''

        font = QtGui.QFont()
        font.setBold(True)

        p_color = QtGui.QColor("#000000") if self._app_option('global_theme') in ['default', 'light'] \
            else QtGui.QColor("#FFFFFF")

        # main Items categories
        dims = self.treeWidget.addParent(
            parent, _('Dimensions'), expanded=True, color=p_color, font=font)
        options = self.treeWidget.addParent(parent, _('Options'), color=p_color, font=font)

        if obj.kind.lower() == 'gerber':
            apertures = self.treeWidget.addParent(
                parent, _('Apertures'), expanded=True, color=p_color, font=font)
        else:
            tools = self.treeWidget.addParent(
                parent, _('Tools'), expanded=True, color=p_color, font=font)

        if obj.kind.lower() == 'excellon':
            drills = self.treeWidget.addParent(
                parent, _('Drills'), expanded=True, color=p_color, font=font)
            slots = self.treeWidget.addParent(
                parent, _('Slots'), expanded=True, color=p_color, font=font)

        if obj.kind.lower() == 'cncjob':
            others = self.treeWidget.addParent(
                parent, _('Others'), expanded=True, color=p_color, font=font)

        # separator = self.treeWidget.addParent(parent, '')

        def job_thread(obj_prop):
            self.app.proc_container.new(_("Calculating dimensions ... Please wait."))

            length = 0.0
            width = 0.0
            area = 0.0
            copper_area = 0.0

            geo = obj_prop.solid_geometry
            if geo:
                # calculate physical dimensions
                try:
                    xmin, ymin, xmax, ymax = obj_prop.bounds()

                    length = abs(xmax - xmin)
                    width = abs(ymax - ymin)
                except Exception as ee:
                    self.app.log.error("FlatCAMObj.add_properties_items() -> calculate dimensions --> %s" % str(ee))

                # calculate box area
                if self.app.app_units.lower() == 'mm':
                    area = (length * width) / 100
                else:
                    area = length * width

                if obj_prop.kind.lower() == 'gerber' and geo:
                    # calculate copper area
                    try:
                        for geo_el in geo:
                            copper_area += geo_el.area
                    except TypeError:
                        copper_area += geo.area
                    copper_area /= 100
            else:
                xmin = []
                ymin = []
                xmax = []
                ymax = []

                if obj_prop.kind.lower() == 'cncjob':
                    # CNCJob created from Excellon or Geometry object
                    try:
                        for tool_k in obj_prop.tools:
                            x0, y0, x1, y1 = unary_union(obj_prop.tools[tool_k]['solid_geometry']).bounds
                            xmin.append(x0)
                            ymin.append(y0)
                            xmax.append(x1)
                            ymax.append(y1)
                    except Exception as ee:
                        self.app.log.error("FlatCAMObj.add_properties_items() cncjob --> %s" % str(ee))
                else:
                    try:
                        if obj_prop.tools:
                            for tool_k in obj_prop.tools:
                                t_geo = obj_prop.tools[tool_k]['solid_geometry']
                                try:
                                    x0, y0, x1, y1 = unary_union(t_geo).bounds
                                except Exception:
                                    continue
                                xmin.append(x0)
                                ymin.append(y0)
                                xmax.append(x1)
                                ymax.append(y1)
                    except Exception as ee:
                        self.app.log.error("FlatCAMObj.add_properties_items() not cncjob tools --> %s" % str(ee))

                if xmin and ymin and xmax and ymax:
                    xmin = min(xmin)
                    ymin = min(ymin)
                    xmax = max(xmax)
                    ymax = max(ymax)

                    length = abs(xmax - xmin)
                    width = abs(ymax - ymin)

                    # calculate box area
                    if self.app.app_units.lower() == 'mm':
                        area = (length * width) / 100
                    else:
                        area = length * width

                if obj_prop.kind.lower() == 'gerber' and obj_prop.tools:
                    # calculate copper area

                    # create a complete solid_geometry from the tools
                    geo_tools = []
                    for tool_k in obj_prop.tools:
                        if 'solid_geometry' in obj_prop.tools[tool_k]:
                            for geo_el in obj_prop.tools[tool_k]['solid_geometry']:
                                geo_tools.append(geo_el)

                    for geo_el in geo_tools:
                        copper_area += geo_el.area
                    # in cm2
                    copper_area /= 100

            area_chull = 0.0
            if obj_prop.kind.lower() != 'cncjob':
                # calculate and add convex hull area
                if geo:
                    if isinstance(geo, list) and geo[0] is not None:
                        if isinstance(geo, MultiPolygon):
                            env_obj = geo.convex_hull
                        elif (isinstance(geo, MultiPolygon) and len(geo.geoms) == 1) or \
                                (isinstance(geo, list) and len(geo) == 1) and isinstance(geo[0], Polygon):
                            env_obj = unary_union(geo)
                            env_obj = env_obj.convex_hull
                        else:
                            env_obj = unary_union(geo)
                            env_obj = env_obj.convex_hull

                        area_chull = env_obj.area
                    else:
                        area_chull = 0
                else:
                    try:
                        area_chull = None
                        if obj_prop.tools:
                            area_chull_list = []
                            for tool_k in obj_prop.tools:
                                area_el = unary_union(obj_prop.tools[tool_k]['solid_geometry']).convex_hull
                                area_chull_list.append(area_el.area)
                            area_chull = max(area_chull_list)
                    except Exception as er:
                        area_chull = None
                        self.app.log.error("FlatCAMObj.add_properties_items() area chull--> %s" % str(er))

            if self.app.app_units.lower() == 'mm' and area_chull:
                area_chull = area_chull / 100

            if area_chull is None:
                area_chull = 0

            self.calculations_finished.emit(area, length, width, area_chull, copper_area, dims)

        self.app.worker_task.emit({'fcn': job_thread, 'params': [obj]})

        # Options items
        for option in obj.obj_options:
            if option == 'name':
                continue
            self.treeWidget.addChild(options, [str(option), str(obj.obj_options[option])], True)

        # Items that depend on the object type
        if obj.kind.lower() == 'gerber' and obj.tools:
            temp_ap = {}
            for ap in obj.tools:
                temp_ap.clear()
                temp_ap = deepcopy(obj.tools[ap])
                temp_ap.pop('geometry', None)

                solid_nr = 0
                follow_nr_points = 0
                follow_nr_lines = 0
                follow_nr = 0
                clear_nr = 0

                if 'geometry' in obj.tools[ap]:
                    if obj.tools[ap]['geometry']:
                        font.setBold(True)
                        for el in obj.tools[ap]['geometry']:
                            if 'solid' in el:
                                solid_nr += 1
                            if 'follow' in el:
                                if isinstance(el['follow'], Point):
                                    follow_nr_points += 1
                                elif isinstance(el['follow'], LineString):
                                    follow_nr_lines += 1
                                else:
                                    follow_nr += 1
                            if 'clear' in el:
                                clear_nr += 1
                else:
                    font.setBold(False)

                temp_ap['Solid_Geo'] = '%s %s' % (str(solid_nr), _("Polygons"))
                if follow_nr_lines > 0 and follow_nr_points == 0:
                    temp_ap['Follow_Geo'] = '%s %s' % (str(follow_nr_lines), _("LineStrings"))
                elif follow_nr_lines == 0 and follow_nr_points > 0:
                    temp_ap['Follow_Geo'] = '%s %s' % (str(follow_nr_points), _("Points"))
                else:
                    temp_ap['Follow_Geo'] = '%s %s' % \
                                            (str(follow_nr_lines + follow_nr_points + follow_nr), _("Elements"))
                temp_ap['Clear_Geo'] = '%s %s' % (str(clear_nr), _("Polygons"))

                apid = self.treeWidget.addParent(
                    apertures, str(ap), expanded=False, color=p_color, font=font)
                for key in temp_ap:
                    self.treeWidget.addChild(apid, [str(key), str(temp_ap[key])], True)
        elif obj.kind.lower() == 'excellon':
            tot_drill_cnt = 0
            tot_slot_cnt = 0

            for tool, value in obj.tools.items():
                toolid = self.treeWidget.addParent(
                    tools, str(tool), expanded=False, color=p_color, font=font)

                drill_cnt = 0  # variable to store the nr of drills per tool
                slot_cnt = 0  # variable to store the nr of slots per tool

                # Find no of drills for the current tool
                if 'drills' in value and value['drills']:
                    drill_cnt = len(value['drills'])

                tot_drill_cnt += drill_cnt

                # Find no of slots for the current tool
                if 'slots' in value and value['slots']:
                    slot_cnt = len(value['slots'])

                tot_slot_cnt += slot_cnt

                self.treeWidget.addChild(
                    toolid,
                    [
                        _('Diameter'),
                        '%.*f %s' % (self.decimals, value['tooldia'], self.app.app_units.lower())
                    ],
                    True
                )
                self.treeWidget.addChild(toolid, [_('Drills number'), str(drill_cnt)], True)
                self.treeWidget.addChild(toolid, [_('Slots number'), str(slot_cnt)], True)

            self.treeWidget.addChild(drills, [_('Drills total number:'), str(tot_drill_cnt)], True)
            self.treeWidget.addChild(slots, [_('Slots total number:'), str(tot_slot_cnt)], True)
        elif obj.kind.lower() == 'geometry':
            for tool, value in obj.tools.items():
                geo_tool = self.treeWidget.addParent(
                    tools, str(tool), expanded=False, color=p_color, font=font)
                for k, v in value.items():
                    if k == 'solid_geometry':
                        # printed_value = _('Present') if v else _('None')
                        try:
                            printed_value = str(len(v))
                        except (TypeError, AttributeError):
                            printed_value = '1'
                        self.treeWidget.addChild(geo_tool, [str(k), printed_value], True)
                    elif k == 'data':
                        tool_data = self.treeWidget.addParent(
                            geo_tool, str(k).capitalize(), color=p_color, font=font)
                        for data_k, data_v in v.items():
                            self.treeWidget.addChild(tool_data, [str(data_k), str(data_v)], True)
                    else:
                        self.treeWidget.addChild(geo_tool, [str(k), str(v)], True)
        elif obj.kind.lower() == 'cncjob':
            # for CNCJob objects made from Gerber or Geometry
            if obj.obj_options['type'].lower() == 'geometry':
                for tool, value in obj.tools.items():
                    geo_tool = self.treeWidget.addParent(
                        tools, str(tool), expanded=False, color=p_color, font=font)
                    for k, v in value.items():
                        if k == 'solid_geometry':
                            printed_value = _('Present') if v else _('None')
                            self.treeWidget.addChild(geo_tool, [_("Solid Geometry"), printed_value], True)
                        elif k == 'gcode':
                            printed_value = _('Present') if v != '' else _('None')
                            self.treeWidget.addChild(geo_tool, [_("GCode Text"), printed_value], True)
                        elif k == 'gcode_parsed':
                            printed_value = _('Present') if v else _('None')
                            self.treeWidget.addChild(geo_tool, [_("GCode Geometry"), printed_value], True)
                        elif k == 'data':
                            pass
                        else:
                            self.treeWidget.addChild(geo_tool, [str(k), str(v)], True)

                    v = value['data']
                    tool_data = self.treeWidget.addParent(
                        geo_tool, _("Tool Data"), color=p_color, font=font)
                    for data_k, data_v in v.items():
                        self.treeWidget.addChild(tool_data, [str(data_k).capitalize(), str(data_v)], True)

            # for CNCJob objects made from Excellon
            if obj.obj_options['type'].lower() == 'excellon':
                for tool_id, value in obj.tools.items():
                    tool_dia = value['tooldia']
                    exc_tool = self.treeWidget.addParent(
                        tools, str(tool_id), expanded=False, color=p_color, font=font
                    )
                    self.treeWidget.addChild(
                        exc_tool,
                        [
                            _('Diameter'),
                            '%.*f %s' % (self.decimals, tool_dia, self.app.app_units.lower())
                        ],
                        True
                    )
                    for k, v in value.items():
                        if k == 'solid_geometry':
                            printed_value = _('Present') if v else _('None')
                            self.treeWidget.addChild(exc_tool, [_("Solid Geometry"), printed_value], True)
                        elif k == 'nr_drills':
                            self.treeWidget.addChild(exc_tool, [_("Drills number"), str(v)], True)
                        elif k == 'nr_slots':
                            self.treeWidget.addChild(exc_tool, [_("Slots number"), str(v)], True)
                        elif k == 'gcode':
                            printed_value = _('Present') if v != '' else _('None')
                            self.treeWidget.addChild(exc_tool, [_("GCode Text"), printed_value], True)
                        elif k == 'gcode_parsed':
                            printed_value = _('Present') if v else _('None')
                            self.treeWidget.addChild(exc_tool, [_("GCode Geometry"), printed_value], True)
                        else:
                            pass

                    self.treeWidget.addChild(
                        exc_tool,
                        [
                            _("Depth of Cut"),
                            '%.*f %s' % (
                                self.decimals,
                                (obj.z_cut - abs(value['data']['tools_drill_offset'])),
                                self.app.app_units.lower()
                            )
                        ],
                        True
                    )
                    self.treeWidget.addChild(
                        exc_tool,
                        [
                            _("Clearance Height"),
                            '%.*f %s' % (
                                self.decimals,
                                obj.z_move,
                                self.app.app_units.lower()
                            )
                        ],
                        True
                    )
                    self.treeWidget.addChild(
                        exc_tool,
                        [
                            _("Feedrate"),
                            '%.*f %s/min' % (
                                self.decimals,
                                obj.feedrate,
                                self.app.app_units.lower()
                            )
                        ],
                        True
                    )

                    v = value['data']
                    tool_data = self.treeWidget.addParent(
                        exc_tool, _("Tool Data"), color=p_color, font=font)
                    for data_k, data_v in v.items():
                        self.treeWidget.addChild(tool_data, [str(data_k).capitalize(), str(data_v)], True)

            r_time = obj.routing_time
            if r_time > 1:
                units_lbl = 'min'
            else:
                r_time *= 60
                units_lbl = 'sec'
            r_time = math.ceil(float(r_time))
            self.treeWidget.addChild(
                others,
                [
                    '%s:' % _('Routing time'),
                    '%.*f %s' % (self.decimals, r_time, units_lbl)],
                True
            )
            self.treeWidget.addChild(
                others,
                [
                    '%s:' % _('Travelled distance'),
                    '%.*f %s' % (self.decimals, obj.travel_distance, self.app.app_units.lower())
                ],
                True
            )

        # treeWidget.addChild(separator, [''])

    def update_area_chull(self, area, length, width, chull_area, copper_area, location):

        # add dimensions
        self.treeWidget.addChild(
            location,
            ['%s:' % _('Length'), '%.*f %s' % (self.decimals, length, self.app.app_units.lower())],
            True
        )
        self.treeWidget.addChild(
            location,
            ['%s:' % _('Width'), '%.*f %s' % (self.decimals, width, self.app.app_units.lower())],
            True
        )

        # add box area
        if self.app.app_units.lower() == 'mm':
            self.treeWidget.addChild(location, ['%s:' % _('Box Area'), '%.*f %s' % (self.decimals, area, 'cm2')], True)
            self.treeWidget.addChild(
                location,
                ['%s:' % _('Convex_Hull Area'), '%.*f %s' % (self.decimals, chull_area, 'cm2')],
                True
            )

        else:
            self.treeWidget.addChild(location, ['%s:' % _('Box Area'), '%.*f %s' % (self.decimals, area, 'in2')], True)
            self.treeWidget.addChild(
                location,
                ['%s:' % _('Convex_Hull Area'), '%.*f %s' % (self.decimals, chull_area, 'in2')],
                True
            )

        # add copper area
        if self.app.app_units.lower() == 'mm':
            self.treeWidget.addChild(
                location, ['%s:' % _('Copper Area'), '%.*f %s' % (self.decimals, copper_area, 'cm2')], True)
        else:
            self.treeWidget.addChild(
                location, ['%s:' % _('Copper Area'), '%.*f %s' % (self.decimals, copper_area, 'in2')], True)

        for col in range(self.treeWidget.columnCount()):
            self.ui.treeWidget.resizeColumnToContents(col)

    @staticmethod
    def poly2rings(poly):
        return [poly.exterior] + [interior for interior in poly.interiors]

    @property
    def drawing_tolerance(self):
        self.units = self.app.app_units.upper()
        tol = self._drawing_tolerance if self.units == 'MM' or not self.units else self._drawing_tolerance / 25.4
        return tol

    @drawing_tolerance.setter
    def drawing_tolerance(self, value):
        self.units = self.app.app_units.upper()
        self._drawing_tolerance = value if self.units == 'MM' or not self.units else value / 25.4

    def delete(self):
        # Free resources
        del self.ui
        del self.obj_options

        # Set flag
        self.deleted = True
