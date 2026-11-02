
from PyQt6 import QtCore, QtWidgets     # noqa

from dataclasses import dataclass, field
from copy import copy, deepcopy

import numpy as np

import traceback

from shapely import (
    Polygon,
    MultiPolygon,
    total_bounds,
)
from shapely.ops import unary_union

from typing import TYPE_CHECKING, Union

import gettext
import appTranslation as fcTranslate
import builtins

from camlib import (
    grace,
    flatten_shapely_geometry,
    residual_cleanup,
    object_circle_steps,
    clearing_simplification,
    clearing_error,
    project_units,
)
from defaults import AppDefaults
from appParsers.feature_access import fused_solid
from appCommon.SafeEval import safe_eval
from appPlugins.ToolPaint import PaintModel

if TYPE_CHECKING:
    from appObjects.GerberObject import GerberObject
    from appObjects.GeometryObject import GeometryObject

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class _PaintAppSnapshot:
    """Freeze generation options while forwarding live services and cancellation."""
    def __init__(self, app):
        self._app = app
        # Shallow: the generators only read option values, none of them mutates one.
        self.options = dict(app.options)
        self.defaults = dict(getattr(app, 'defaults', None) or AppDefaults.factory_defaults)
        self.units = app.units

    def __getattr__(self, name):
        return getattr(self._app, name)


def gerber_copper_for_paint(obj):
    """
    The copper of a Gerber object as one valid Polygon / MultiPolygon (``fused_solid``): the composed geometry with
    the polarity applied, repaired and fused. Nothing is changed on the object.
    """
    return fused_solid(obj)


@dataclass
class Params:
    tool_ordering: int
    paint_method: int
    rest_machining_choice: bool
    simplification_value: float
    prog_plot: bool
    tools_storage: field(default_factory=dict)
    output_object_name: str | None
    rest_offset: float = 0.0
    paint_method_explicit: bool = False   # the caller forced one method for every tool


class PaintSelectionError(Exception):
    """The polygons to paint could not be selected; the message is meant for the user."""


class _MainThreadCall(QtCore.QObject):
    """Run a callable on the thread this object lives in (the GUI thread) when it is requested from another."""
    requested = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.requested.connect(self._run)

    @staticmethod
    def _run(function):
        function()


def _on_gui_thread():
    app = QtWidgets.QApplication.instance()
    return app is None or QtCore.QThread.currentThread() is app.thread()


def _live_state(name):
    """Property reading (and writing) a selection attribute of the controller, so no stale copy is kept."""
    return property(lambda self: getattr(self.parent_tool, name, None),
                    lambda self, value: setattr(self.parent_tool, name, value))


class PaintGen:
    def __init__(self, tool):
        self.app = tool.app
        self.ui = tool.ui
        self.obj_name = tool.obj_name

        self.decimals = tool.decimals

        self.paint_obj = tool.paint_obj
        self.paint_objs = []
        self.multi_paint = False

        self.o_name = tool.o_name

        self.overlap = tool.overlap
        self.connect = tool.connect
        self.contour = tool.contour

        self.tooldia_list = tool.tooldia_list
        self.tool_type_item_options = tool.tool_type_item_options
        self.tooldia = tool.tooldia

        self.units = tool.units
        self.select_method = tool.select_method

        self.bound_obj_name = tool.bound_obj_name
        self.bound_obj = tool.bound_obj

        self.parent_tool = tool
        # Created on the GUI thread: requests from the worker thread are queued to it.
        self._gui_call = _MainThreadCall()

    # The controller owns this state; read it live (and write through) instead of copying it at construction.
    paint_tools = _live_state('paint_tools')
    temp_shapes = _live_state('temp_shapes')
    first_click = _live_state('first_click')
    cursor_pos = _live_state('cursor_pos')
    mouse_is_dragging = _live_state('mouse_is_dragging')
    poly_drawn = _live_state('poly_drawn')
    points = _live_state('points')

    def _option(self, key):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

    def _ui_value(self, widget_name, option_key):
        """The value of a form widget, or the application option when there is no form (headless / Tcl)."""
        widget = getattr(self.ui, widget_name, None) if self.ui is not None else None
        return widget.get_value() if widget is not None else self._option(option_key)

    def on_paint_button_click(self):
        """
        Slot for clicking signal
        :return: None
        """

        self.app.defaults.report_usage("on_paint_button_click")
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        self.parent_tool.reset_selection_state()
        self.parent_tool.clear_preview()

        prog_plot = True if self.app.options.get(
            "tools_paint_plotting",
            app_defaults.get("tools_paint_plotting", AppDefaults.factory_defaults.get("tools_paint_plotting"))
        ) == 'progressive' else False
        if prog_plot:
            self.temp_shapes.clear(update=True)

        self.parent_tool.area_to_paint_list = []

        self.select_method = self.ui.select_method_combo.get_value()

        # the Geometry / Gerber objects selected in the Project tab, when asked; Polygon Selection keeps the combo
        self.paint_objs = []
        self.multi_paint = False
        if self.ui.multi_objects_cb.get_value() and self.select_method in (0, 2, 3):
            self.paint_objs = [obj for obj in self.app.collection.get_selected()
                               if obj.kind in ('geometry', 'gerber')]
            if not self.paint_objs:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select Geometry or Gerber objects in the Project tab."))
                return
            self.multi_paint = True
            self.paint_obj = self.paint_objs[0]
            self.obj_name = self.paint_obj.obj_options['name']
        else:
            self.obj_name = self.ui.obj_combo.currentText()

            # Get source object.
            try:
                self.paint_obj = self.app.collection.get_by_name(str(self.obj_name))
            except Exception as e:
                self.app.log.error("ToolPaint.on_paint_button_click() --> %s" % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), self.obj_name))
                return

            if self.paint_obj is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), self.obj_name))
                return
            self.paint_objs = [self.paint_obj]

        self.o_name = '%s_mt_paint' % self.obj_name

        # use the selected tools in the tool table; get diameters
        self.tooldia_list = []
        table_items = self.ui.tools_table.selectedItems()
        sel_rows = {t.row() for t in table_items}
        if len(sel_rows) > 0:
            for row in sel_rows:
                try:
                    self.tooldia = float(self.ui.tools_table.item(row, 1).text())
                except ValueError:
                    # try to convert comma to decimal point. if it's still not working error message and return
                    try:
                        self.tooldia = float(self.ui.tools_table.item(row, 1).text().replace(',', '.'))
                    except ValueError:
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                        continue
                self.tooldia_list.append(self.tooldia)
        else:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
            return

        if self.select_method == 0:  # _("All")
            self._for_each_source(lambda obj: self.paint_poly_all(obj, tooldia=self.tooldia_list, outname=self.o_name))

        elif self.select_method == 1:   # _("Polygon Selection")
            self._paint_poly_single()

        elif self.select_method == 2:   # _("Area Selection")
            self._paint_poly_area()

        elif self.select_method == 3:   # _("Reference Object")
            self._paint_poly_reference()

    def _steps_at(self, radius, obj=None, kind=None):
        """
        The segments of a full circle of this radius, as the preferences "Circles" of the object's kind set them:
        Gerber objects use the Gerber ones, the other objects the Geometry ones.

        :param radius:  the radius of the tool, in the units of the object
        :param obj:     the painted object; the object being painted if None
        :param kind:    'gerber' or 'geometry'; the kind of the object if None
        """
        obj = obj if obj is not None else getattr(self, 'paint_obj', None)
        if kind is None:
            kind = 'gerber' if getattr(obj, 'kind', None) == 'gerber' else 'geometry'
        return object_circle_steps(self.app.options, kind, radius, getattr(obj, 'units', None) or self.units)

    def _for_each_source(self, paint):
        """Run paint(obj) for every source object, each one its own job with its name and circle steps."""
        for obj in getattr(self, 'paint_objs', None) or [self.paint_obj]:
            self.paint_obj = obj
            self.obj_name = obj.obj_options['name']
            self.o_name = ('%s_paint' if getattr(self, 'multi_paint', False) else '%s_mt_paint') % self.obj_name
            paint(obj)

    def paint_area_selection_option(self, areas_to_paint_list: list[Polygon | MultiPolygon]):
        self.app.log.info("PaintGen.paint_area_selection_option() --> Painting the designated areas(s).")

        areas_to_paint_list = unary_union(areas_to_paint_list)
        if isinstance(areas_to_paint_list, MultiPolygon):
            areas_to_paint_list = list(areas_to_paint_list.geoms)
        elif isinstance(areas_to_paint_list, Polygon):
            areas_to_paint_list = [areas_to_paint_list]

        self._for_each_source(lambda obj: self.paint_poly_area_worker(
            painted_object=obj,
            tooldia=self.tooldia_list,
            areas_to_paint=areas_to_paint_list,
            outname=self.o_name
        ))

    def paint_polygon_worker(
            self,
            poly_g,
            tool_diameter,
            paint_method,
            over,
            conn,
            cont,
            prog_plot,
            obj,
    ):

        # the tool path arcs have the radius of the tool
        circle_steps = self._steps_at(tool_diameter / 2, obj)
        geometry_circle_steps = circle_steps
        apertures = None
        index = None
        if paint_method == 3:
            geometry_circle_steps = self._steps_at(tool_diameter / 2, obj, 'geometry')
            apertures = getattr(obj, 'tools', None)
            # The aperture index is built once per job (a PaintGen copy lives for one job), not per polygon.
            cached = getattr(self, '_aperture_index_cache', None)
            if cached is None or cached[0] is not apertures:
                cached = (apertures, PaintModel.aperture_index(apertures))
                self._aperture_index_cache = cached
            index = cached[1]

        def progress(method):
            messages = {
                2: _("Painting polygon with method: lines."),
                1: _("Failed. Painting polygon with method: seed."),
                0: _("Failed. Painting polygon with method: standard.")
            }
            self.app.inform.emit(messages[method])

        result = PaintModel.paint_polygon(
            poly_g, tool_diameter, paint_method, over, conn, cont, prog_plot, circle_steps,
            getattr(self.parent_tool, 'clear_polygon_shrink', None),
            getattr(self.parent_tool, 'clear_polygon_seed', None),
            getattr(self.parent_tool, 'clear_polygon_lines', None),
            apertures=apertures, geometry_circle_steps=geometry_circle_steps,
            fill_lines=getattr(self.parent_tool, 'fill_with_lines', None), progress=progress, index=index)
        if result is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Geometry could not be painted completely'))
        return result

    def paint_handler(
            self,
            painted_object: Union["GeometryObject", "GerberObject"],
            geometry,
            tooldia=None,
            order=None,
            method=None,
            outname=None,
            tools_storage=None,
            plot=True,
            rest=None,
            run_threaded=True,
            rest_offset=None,
            simplification=None,
    ):
        """
        Paints a given geometry.

        :param painted_object:             painted object
        :param geometry:        geometry to Paint; or a callable returning it, which runs inside the job (on the
                                worker thread when threaded) so a heavy selection does not block the GUI
        :param tooldia:         Diameter of the painting tool
        :param order:           if the tools are ordered and how
        :param outname:         Name of the resulting Geometry Object.
        :param method:          choice out of _("Seed"), 'normal', 'lines'
        :param tools_storage:   whether to use the current tools_storage self.paints_tools or a different one.
                                Usage of the different one is related to when this function is called
                                from a TcL command.
        :param plot:            if the geometry is plotted; bool
        :param rest:            if rest machining apply here; bool
        :param run_threaded:
        :param rest_offset:     rest machining offset; None = the form value
        :param simplification:  output tolerance in the object's units; None = the application option
        :return: 'fail' on failure (not threaded)
        """

        # Each queued callback owns its settings, not the shared controller's next job.
        self = copy(self)
        self.app = _PaintAppSnapshot(self.app)
        params = self._create_params(
            order=order,
            method=method,
            rest=rest,
            outname=outname,
            tools_storage=tools_storage,
            rest_offset=rest_offset,
            simplification=simplification,
        )

        sorted_paint_tools = self._parse_tool_diameters(tooldia)
        if sorted_paint_tools == 'fail':
            return 'fail'
        sorted_paint_tools = list(sorted_paint_tools)
        check_valid = bool(self._ui_value('valid_cb', 'tools_paint_check_valid'))

        proc = None
        if run_threaded:
            proc = self.app.proc_container.new('%s...' % _("Working"))
        else:
            self.app.proc_container.view.set_busy('%s...' % _("Working"))
            if _on_gui_thread():
                QtWidgets.QApplication.processEvents()

        def job_thread(app_instance, proc):
            ret = None
            self._failure_reported = False
            try:
                selected = geometry
                if callable(selected):
                    selected = selected()
                    if not selected:
                        self.app.log.warning('No polygon found.')
                        self.app.inform.emit('[WARNING] %s' % _('No polygon found.'))
                        self._failure_reported = True
                        ret = 'fail'
                if ret != 'fail' and check_valid and self._tools_do_not_fit(selected, sorted_paint_tools, params):
                    self._failure_reported = True
                    ret = 'fail'
                if ret == 'fail':
                    pass
                elif params.rest_machining_choice:
                    ret = self.parent_tool.new_object(
                        "geometry",
                        params.output_object_name,
                        lambda o, a: self._generate_rest_paint_worker(
                            o,
                            a,
                            params,
                            sorted_paint_tools,
                            selected,
                            painted_object,
                            plot,
                        ),
                        plot=plot,
                        source=painted_object,
                        autoselected=False,
                    )
                else:
                    ret = self.parent_tool.new_object(
                        "geometry",
                        params.output_object_name,
                        lambda o, a: self._generate_paint_worker(
                            o, a, params, sorted_paint_tools, selected, painted_object, plot),
                        plot=plot,
                        source=painted_object,
                        autoselected=False,
                    )
            except grace:
                app_instance.log.debug("PaintGen.paint_handler.job_thread() -> Graceful exit.")
                ret = 'fail'
            except PaintSelectionError as err:
                self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
                self._failure_reported = True
                ret = 'fail'
            except Exception as err:
                app_instance.log.error(f"PaintGen.paint_handler.job_thread() -> Exception: {str(err)}")
                app_instance.log.debug(traceback.format_exc())
                ret = 'fail'
            finally:
                if proc is not None:
                    proc.done()
                else:
                    app_instance.proc_container.view.set_idle()

            if ret == 'fail' or ret is None:
                # The promised name will never come: do not leave the app waiting for it.
                self.app.collection.remove_promise(params.output_object_name)
                if self.app.abort_flag:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                elif not self._failure_reported:
                    self.app.inform.emit('[ERROR] %s' % _("Failed."))
                return 'fail'

            self.app.inform.emit('[success] %s' % _("Done."))
            return ret

        if run_threaded:
            self.app.collection.promise(params.output_object_name)
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app, proc]})
        else:
            return job_thread(app_instance=self.app, proc=proc)

    def _tools_do_not_fit(self, selected, diameters, params):
        """
        The Check validity test. Paint cuts inside the selected polygons, so a tool is valid where it fits inside
        them (after the offset). Runs in the job, before the generation.
        :return: True if the run has to stop (no tool fits inside any selected polygon)
        """
        offsets = {}
        for dia in diameters:
            if params.rest_machining_choice:
                # rest machining buffers the selection once, by the rest offset
                offsets[dia] = params.rest_offset
                continue
            uid = self._find_tool_uid(dia, params.tools_storage)
            data = params.tools_storage[uid]['data'] if uid is not None else {}
            offsets[dia] = float(data.get('tools_paint_offset', 0.0))
        level, narrow, unused = PaintModel.check_tools_fit(selected, diameters, offsets)
        if level == 'error':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "Check validity: no tool fits inside the polygons to paint."))
            return True
        if narrow:
            self.app.inform.emit('[WARNING_NOTCL] %s' % (_(
                "Check validity: %(count)d polygon(s) are too narrow for all the tools and will not be painted.")
                % {'count': narrow}))
        if unused:
            self.app.inform.emit('[WARNING_NOTCL] %s' % (_(
                "Check validity: these tools fit inside no polygon: %(tools)s.")
                % {'tools': ', '.join(str(dia) for dia in unused)}))
        return False

    def _create_params(self, order, method, rest, outname, tools_storage, rest_offset=None, simplification=None):
        if simplification is None:
            simplification_value = float(self._option("tools_paint_simplification"))
            # Preferences store tolerances in mm; both workers consume geometry units.
            if project_units(self.app) == 'IN':
                simplification_value /= 25.4
        else:
            simplification_value = float(simplification)
        simplification_value = clearing_simplification(self.app, simplification_value)
        rest_choice = rest if rest is not None else self._ui_value('rest_cb', 'tools_paint_rest')
        if not rest_choice:
            rest_offset_value = 0.0
        elif rest_offset is not None:
            rest_offset_value = float(rest_offset)
        else:
            rest_offset_value = float(self._ui_value('rest_offset_entry', 'tools_paint_rest_offset'))
        source_tools = self.paint_tools if tools_storage is None else tools_storage
        return Params(
            tool_ordering=order if order is not None else self._ui_value('order_combo', 'tools_paint_order'),
            paint_method=method if method is not None else self._ui_value('method_combo', 'tools_paint_method'),
            rest_machining_choice=rest_choice,
            simplification_value=simplification_value,
            prog_plot=self._option("tools_paint_plotting") == 'progressive',
            tools_storage=deepcopy(source_tools),
            output_object_name=outname if outname is not None else self.obj_name + "_paint",
            rest_offset=rest_offset_value,
            paint_method_explicit=method is not None,
        )

    def _parse_tool_diameters(self, tooldia):
        sorted_paint_tools = []
        if tooldia is not None:
            try:
                sorted_paint_tools = [float(safe_eval(dia)) for dia in tooldia.split(",") if dia != '']
            except AttributeError:
                if not isinstance(tooldia, list):
                    sorted_paint_tools = [float(tooldia)]
                else:
                    sorted_paint_tools = tooldia
        else:
            table = getattr(self.ui, 'tools_table', None) if self.ui is not None else None
            sel_rows = {t.row() for t in table.selectedItems()} if table is not None else set()
            for row in sel_rows:
                try:
                    self.tooldia = float(self.ui.tools_table.item(row, 1).text())
                except ValueError:
                    try:
                        self.tooldia = float(self.ui.tools_table.item(row, 1).text().replace(',', '.'))
                    except ValueError:
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                        continue
                sorted_paint_tools.append(self.tooldia)
            if not sorted_paint_tools:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
                return 'fail'
        return sorted_paint_tools

    def _find_tool_uid(self, tool_dia, tools_storage):
        return PaintModel.find_tool_uid(tool_dia, tools_storage, self.decimals)

    def _get_tool_params(self, tool_uid, tools_storage):
        settings = PaintModel.tool_params(tool_uid, tools_storage)
        # Every tool stores its own method (form_to_storage); None when the tool has none.
        settings['method'] = tools_storage[tool_uid]['data'].get('tools_paint_method')
        return settings

    @staticmethod
    def _tool_method(settings, params, painted_object):
        """The method of one tool: the caller's forced one, else the tool's own, else the job's. Laser lines: Gerber only."""
        method = params.paint_method
        own = settings.get('method')
        if own is not None and not getattr(params, 'paint_method_explicit', False):
            try:
                method = int(own)
            except (TypeError, ValueError):
                pass
        if method == 3 and painted_object is not None and getattr(painted_object, 'kind', None) != 'gerber':
            method = 0
        return method

    def _buffer_geometry(self, geometry, offset):
        return PaintModel.buffer_geometry(geometry, offset)

    def _process_polygon(self, polygon, tool_dia, tool_params, params, painted_object):
        return self.paint_polygon_worker(
            polygon,
            tool_diameter=tool_dia,
            over=tool_params['over'],
            conn=tool_params['conn'],
            cont=tool_params['cont'],
            paint_method=self._tool_method(tool_params, params, painted_object),
            obj=painted_object,
            prog_plot=params.prog_plot,
        )

    def _simplify_geometry(self, geometry_list, simplification_value):
        return PaintModel.simplify_geometry(geometry_list, simplification_value)

    def _update_progress(self, current, total, old_disp_number):
        disp_number = int(np.interp(current, [0, total], [0, 100]))
        if old_disp_number < disp_number <= 100:
            self.app.proc_container.update_view_text(' %d%%' % disp_number)
            return disp_number
        return old_disp_number

    def _cleanup_progressive_plotting(self):
        if self._option("tools_paint_plotting") == 'progressive' and self.temp_shapes is not None:
            # The canvas belongs to the GUI thread; a worker thread asks it to do the clearing.
            gui_call = getattr(self, '_gui_call', None)
            if gui_call is None or _on_gui_thread():
                self.temp_shapes.clear(update=True)
            else:
                gui_call.requested.emit(lambda shapes=self.temp_shapes: shapes.clear(update=True))

    def _remove_empty_tools(self, tools_storage):
        for uid in list(tools_storage.keys()):
            if not tools_storage[uid]['solid_geometry']:
                tools_storage.pop(uid, None)

    def _set_output_bounds(self, output_obj, solid_geometry):
        try:
            # Bounds don't need another topological union of the completed toolpaths.
            a, b, c, d = total_bounds(solid_geometry) if solid_geometry else (np.nan,) * 4
            output_obj.obj_options['xmin'] = a
            output_obj.obj_options['ymin'] = b
            output_obj.obj_options['xmax'] = c
            output_obj.obj_options['ymax'] = d
        except Exception as ee:
            self.app.log.error("ToolPaint.paint_poly.job_init() bounds error --> %s" % str(ee))
            return False
        return True

    def _validate_output_geometry(self, output_obj):
        has_solid_geo = sum(1 for uid in output_obj.tools if output_obj.tools[uid]['solid_geometry'])
        if has_solid_geo == 0:
            self.app.log.error(
                "There is no Painting Geometry in the file.\n"
                "Usually it means that the tool diameter is too big for the painted geometry.\n"
                "Change the painting parameters and try again."
            )
            return False
        return True

    def _generate_paint_worker(self, output_geo_object, app_obj, params, sorted_paint_tools, geometry, painted_object, plot):
        return self._generate_worker(output_geo_object, app_obj, params, sorted_paint_tools,
                                     geometry, painted_object, rest=False)

    def _generate_rest_paint_worker(self, output_geo_object, app_obj, params, sorted_paint_tools, geometry, painted_object, plot):
        return self._generate_worker(output_geo_object, app_obj, params, sorted_paint_tools,
                                     geometry, painted_object, rest=True)

    def _check_abort(self):
        # Only the GUI thread may pump the event loop (the Abort button is handled there).
        if _on_gui_thread():
            QtWidgets.QApplication.processEvents()
        if self.app.abort_flag:
            raise grace

    def _tool_started(self, diameter):
        self.app.log.debug("Starting geometry processing for tool: %s" % str(diameter))
        self.app.inform.emit('[success] %s %s%s %s' % (
            _('Painting with tool diameter = '), str(diameter), self.units.lower(), _('started')))
        self.app.proc_container.update_view_text(' %d%%' % 0)

    def _report_warnings(self, warnings):
        reported = set()
        for kind, error in warnings:
            if (kind, error) in reported:
                continue
            reported.add((kind, error))
            if kind == 'cleanup':
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    'Final paint finished. Some areas could not be painted with this tool.'))
            elif kind == 'skipped':
                self.app.inform.emit('[WARNING_NOTCL] %s %s' % (
                    _("A polygon could not be painted with tool diameter ="), error))
            elif kind == 'empty':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "There is no geometry to process or the tool diameter is too big."))
            elif kind in ('normal', 'rest'):
                self.app.log.error("Could not Paint the polygons. %s" % error)
                self.app.inform.emit('[ERROR] %s\n%s' % (_(
                    "Could not do Paint. Try a different combination of parameters. "
                    "Or a different method of Paint"), error))
            else:
                # A kind this class does not know yet must not break the job.
                self.app.log.warning("PaintGen: warning %s: %s" % (kind, error))

    def _generate_worker(self, output, app_obj, params, diameters, geometry, painted_object, rest):
        def process_polygon(polygon, diameter, settings):
            return self._process_polygon(polygon, diameter, settings, params, painted_object)

        try:
            common = dict(check_abort=self._check_abort, progress=self._update_progress, tool_started=self._tool_started,
                          find_uid=self._find_tool_uid, get_params=self._get_tool_params,
                          buffer=self._buffer_geometry, simplify=self._simplify_geometry,
                          # a simplified path and the rest stock keep the Clearing Error of the Preferences, or
                          # the error of the circle steps of the tool
                          edge_error=clearing_error(self.app),
                          steps_at=lambda diameter: self._steps_at(diameter / 2, painted_object))
            if rest:
                result = PaintModel.generate_rest_paint(
                    geometry, params.tools_storage, diameters, params.simplification_value, self.decimals,
                    process_polygon, rest_offset=getattr(params, 'rest_offset', 0.0), **common)
            else:
                result = PaintModel.generate_paint(
                    geometry, params.tools_storage, diameters, getattr(params, 'tool_ordering', 0),
                    params.simplification_value, self.decimals, process_polygon, **common)
            if result == 'fail':
                return 'fail'
            self._report_warnings(result['warnings'])
            finished = self._cleanup_result(
                geometry, result['tools'], result.get('output_tools', result['tools']), params, diameters,
                getattr(params, 'rest_offset', 0.0) if rest else None, painted_object)
            self._report_warnings(finished['warnings'])
            tools = finished['tools']
            self._remove_empty_tools(tools)
            if not tools or not finished['paths']:
                self.app.log.error(
                    "There is no Painting Geometry in the file.\n"
                    "Usually it means that the tool diameter is too big for the painted geometry.\n"
                    "Change the painting parameters and try again.")
                self._failure_reported = True
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "There is no geometry to process or the tool diameter is too big."))
                return 'fail'
            # The paths are already independent toolpaths: a union would only cost time and node the lines.
            solid_geometry = flatten_shapely_geometry(finished['paths'])
            self._check_abort()
            for tool in tools.values():
                tool['data']['output_object_name'] = params.output_object_name
            output.tools = finished['output_tools'] if rest else dict(tools)
            for tool in output.tools.values():
                tool['data']['output_object_name'] = params.output_object_name
            output.solid_geometry = solid_geometry
            output.multigeo = True
            output.multitool = True
            output.obj_options['tools_mill_tooldia'] = '0.0' if rest else str(result['last_diameter'])
            if not self._set_output_bounds(output, solid_geometry) or not self._validate_output_geometry(output):
                self._failure_reported = True
                app_obj.inform.emit('[ERROR] %s' % _("Failed."))
                return 'fail'
        except grace:
            return 'fail'
        finally:
            self._cleanup_progressive_plotting()

    def _cleanup_result(self, geometry, tools, output_tools, params, diameters, rest_offset, painted_object=None):
        def process_polygon(polygon, diameter, settings):
            method = self._tool_method(settings, params, painted_object)
            return self.paint_polygon_worker(
                polygon, diameter, 2 if method == 3 else method,
                settings['over'], settings['conn'], True, False, None)

        def cleanup(domain, diameter, previous, generate_paths, boundary_first):
            return residual_cleanup(self.app, domain, diameter, self._steps_at(diameter / 2, painted_object), previous,
                                    generate_paths, physical=True, boundary_first=boundary_first)

        return PaintModel.final_cleanup(
            geometry, tools, output_tools, diameters, self.decimals, process_polygon, cleanup,
            rest_offset=rest_offset, check_abort=self._check_abort, find_uid=self._find_tool_uid,
            get_params=self._get_tool_params, buffer=self._buffer_geometry)

    @staticmethod
    def _source_geometry(obj):
        """
        The geometry a selection works on. A Gerber always gives its fused copper (``gerber_copper_for_paint``), a
        MultiGeo Geometry the geometry of all its tools, other Geometry its solid geometry. Nothing is changed on
        the object. Can be heavy: call it from the job, not from a GUI slot.
        """
        if obj.kind == 'gerber':
            return gerber_copper_for_paint(obj)
        tools = getattr(obj, 'tools', None) or {}
        if obj.kind == 'geometry' and getattr(obj, 'multigeo', False) and len(tools) > 1:
            return [tool['solid_geometry'] for tool in tools.values() if tool.get('solid_geometry')]
        return obj.solid_geometry

    def _announce_start(self, obj, text):
        message = '%s %s' % (_("Paint Plugin."), text)
        # I don't do anything here, like buffering when the Gerber is loaded without buffering????!!!!
        if obj.kind == 'gerber' and self._option("gerber_buffering") == 'no':
            message += ' %s' % _("Buffering geometry...")
        self.app.inform.emit(message)

    def select_all(self, obj):
        """The polygons of the All mode: every polygon of the object."""
        return PaintModel.selection_geometry(
            self._source_geometry(obj), self._check_abort, expand_multi=obj.kind == 'gerber') or []

    def select_in_areas(self, obj, areas):
        """The polygons of the object inside the areas (Area Selection and Reference Object modes)."""
        if obj.kind == 'gerber':
            target_geo = gerber_copper_for_paint(obj)
        else:
            target_geo = unary_union(flatten_shapely_geometry(self._source_geometry(obj)))
        return PaintModel.area_intersection(target_geo, areas, self._check_abort)

    def preview_area(self, objects, mode, offsets, reference=None, zones=None, polygons=None):
        """
        The area that Generate would paint: the selected polygons of every object, shrunk by every offset in use,
        merged. Runs in the worker: no toolpaths, no Qt widgets.

        :param objects:     the source objects
        :param mode:        the selection mode: 0 All, 1 Polygon Selection, 2 Area Selection, 3 Reference Object
        :param offsets:     the offsets of the selected tools (or the rest machining offset)
        :param reference:   the reference object (mode 3)
        :param zones:       the drawn zones (mode 2)
        :param polygons:    the clicked polygons (mode 1)
        :return: the area, or None when nothing is selected
        """
        if mode == 1:
            selected = list(polygons or [])
        else:
            areas = None
            if mode == 2:
                areas = list(zones or [])
            elif mode == 3:
                areas = PaintModel.reference_area(reference.solid_geometry)
            selected = []
            for obj in objects:
                selected.extend(self.select_all(obj) if mode == 0 else self.select_in_areas(obj, areas))
        parts = []
        for offset in offsets:
            self._check_abort()
            parts.extend(PaintModel.buffer_geometry(selected, offset))
        return unary_union(parts) if parts else None

    def _resolve_selection(self, select, run_threaded):
        """
        Threaded: the selection runs inside the job, on the worker thread, so the GUI stays responsive.
        Not threaded: it runs now and the polygons are returned; None when there is nothing to paint.
        """
        if run_threaded:
            return select
        try:
            selected = select()
        except grace:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return None
        except PaintSelectionError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return None
        if not selected:
            self.app.log.warning('No polygon found.')
            self.app.inform.emit('[WARNING] %s' % _('No polygon found.'))
            return None
        return selected

    def paint_poly(
            self,
            obj: Union["GeometryObject", "GerberObject"],
            inside_pt=None,
            poly_list=None,
            tooldia=None,
            order=None,
            method=None,
            outname=None,
            tools_storage=None,
            plot=True,
            run_threaded=True,
            rest=None,
            rest_offset=None,
            simplification=None,
    ):
        """
        Paints a polygon selected by clicking on its interior or by having a point coordinates given

        Note:
            * The margin is taken directly from the form.

        :param run_threaded:
        :param plot:
        :param poly_list:
        :param obj:             painted object
        :param inside_pt:       [x, y]
        :param tooldia:         Diameter of the painting tool
        :param order:           if the tools are ordered and how
        :param outname:         Name of the resulting Geometry Object.
        :param method:          choice out of _("Seed"), 'normal', 'lines'
        :param tools_storage:   whether to use the current tools_storage self.paints_tools or a different one.
                                Usage of the different one is related to when this function is called
                                from a TcL command.
        :param rest:            rest machining; None = the form value
        :param rest_offset:     rest machining offset; None = the form value
        :param simplification:  output tolerance in the object's units; None = the application option
        :return: 'fail' on failure when not threaded
        """

        self._announce_start(obj, _("Normal painting polygon task started."))

        if inside_pt and poly_list is None:
            def select():
                self._check_abort()
                geometry = self._source_geometry(obj)
                if obj.kind == 'gerber':
                    geometry = flatten_shapely_geometry(geometry)
                polygon = PaintModel.select_polygon(geometry, inside_pt)
                return [polygon] if polygon else []
        elif poly_list:
            def select():
                return poly_list
        else:
            return 'fail'

        polygon_list = self._resolve_selection(select, run_threaded)
        if polygon_list is None:
            return 'fail'

        return self.paint_handler(
            obj,
            polygon_list,
            tooldia=tooldia,
            order=order,
            method=method,
            outname=outname,
            tools_storage=tools_storage,
            plot=plot,
            run_threaded=run_threaded,
            rest=rest,
            rest_offset=rest_offset,
            simplification=simplification,
        )

    def paint_poly_all(
            self,
            obj: Union["GeometryObject", "GerberObject"],
            tooldia=None,
            order=None,
            method=None,
            outname=None,
            tools_storage=None,
            plot=True,
            run_threaded=True,
            rest=None,
            rest_offset=None,
            simplification=None,
    ):
        """
        Paints all polygons in this object.

        :param obj:             painted object
        :param tooldia:         a tuple or single element made out of diameters of the tools to be used
        :param order:           if the tools are ordered and how
        :param outname:         name of the resulting object
        :param method:          choice out of _("Seed"), 'normal', 'lines'
        :param tools_storage:   whether to use the current tools_storage self.paints_tools or a different one.
                                Usage of the different one is related to when this function is called from
                                a TcL command.
        :param run_threaded:
        :param plot:
        :param rest:            rest machining; None = the form value
        :param rest_offset:     rest machining offset; None = the form value
        :param simplification:  output tolerance in the object's units; None = the application option
        :return: 'fail' on failure when not threaded
        """

        self._announce_start(obj, _("Paint all polygons task started."))

        def select():
            return self.select_all(obj)

        painted_area = self._resolve_selection(select, run_threaded)
        if painted_area is None:
            return 'fail'

        return self.paint_handler(
            obj,
            painted_area,
            tooldia=tooldia,
            order=order,
            method=method,
            outname=outname,
            tools_storage=tools_storage,
            plot=plot,
            run_threaded=run_threaded,
            rest=rest,
            rest_offset=rest_offset,
            simplification=simplification,
        )

    def _paint_poly_single(self):
        self.parent_tool.paint_obj = self.paint_obj
        self.parent_tool.tooldia_list = self.tooldia_list

        # disengage the grid snapping since it may be hard to click on polygons with grid snapping on
        if self.app.ui.grid_snap_btn.isChecked():
            self.parent_tool.grid_status_memory = True
            self.app.ui.grid_snap_btn.trigger()
        else:
            self.parent_tool.grid_status_memory = False

        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click on a polygon to paint it."))

        self.app.plotcanvas.graph_event_connect(
            'mouse_release',
            self.parent_tool.on_single_poly_mouse_release,
        )
        self.app.plotcanvas.graph_event_connect(
            'key_press',
            self.parent_tool.on_key_press,
        )

        self.app.plotcanvas.graph_event_disconnect(
            'mouse_release',
            self.app.on_mouse_click_release_over_plot,
        )
        self.app.plotcanvas.graph_event_disconnect(
            'mouse_press',
            self.app.on_mouse_click_over_plot,
        )

        # disconnect flags
        self.parent_tool.poly_sel_disconnect_flag = True

    def _paint_poly_area(self):
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the start point of the area."))

        self.app.plotcanvas.graph_event_disconnect(
            'mouse_press',
            self.app.on_mouse_click_over_plot,
        )
        self.app.plotcanvas.graph_event_disconnect(
            'mouse_move',
            self.app.on_mouse_move_over_plot,
        )
        self.app.plotcanvas.graph_event_disconnect(
            'mouse_release',
            self.app.on_mouse_click_release_over_plot,
        )

        self.app.plotcanvas.graph_event_connect(
            'mouse_release',
            self.parent_tool.on_mouse_release,
        )
        self.app.plotcanvas.graph_event_connect(
            'mouse_move',
            self.parent_tool.on_mouse_move,
        )
        self.app.plotcanvas.graph_event_connect(
            'key_press',
            self.parent_tool.on_key_press,
        )

        # disconnect flags
        self.parent_tool.area_sel_disconnect_flag = True
        # disable the "notebook" until the process is finished
        self.app.ui.notebook.setDisabled(True)

    def _paint_poly_reference(self):
        self.bound_obj_name = self.ui.reference_combo.currentText()
        # Get source object.
        try:
            self.bound_obj = self.app.collection.get_by_name(self.bound_obj_name)
        except Exception:
            self.bound_obj = None
        if self.bound_obj is None:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), self.bound_obj_name)
            )
            return 'fail'

        self._for_each_source(lambda obj: self.paint_poly_ref(
            obj=obj,
            sel_obj=self.bound_obj,
            tooldia=self.tooldia_list,
            outname=self.o_name,
        ))

    def paint_poly_ref(
            self,
            obj,
            sel_obj,
            tooldia=None,
            order=None,
            method=None,
            outname=None,
            tools_storage=None,
            plot=True,
            run_threaded=True,
            rest=None,
            rest_offset=None,
            simplification=None,
    ):
        """
        Paints all polygons in this object that are within the areas_to_paint object

        :param obj: painted object
        :param sel_obj: paint only what is inside this object bounds
        :param tooldia: a tuple or single element made out of diameters of the tools to be used
        :param order: if the tools are ordered and how
        :param outname: name of the resulting object
        :param method: choice out of _("Seed"), 'normal', 'lines'
        :param tools_storage: whether to use the current tools_storage self.paints_tools or a different one.
        Usage of the different one is related to when this function is called from a TcL command.
        :param run_threaded:
        :param plot:
        :param rest: rest machining; None = the form value
        :param rest_offset: rest machining offset; None = the form value
        :param simplification: output tolerance in the object's units; None = the application option
        :return: 'fail' on failure when not threaded
        """

        def reference_area():
            try:
                return PaintModel.reference_area(sel_obj.solid_geometry)
            except grace:
                raise
            except Exception as e:
                self.app.log.error("ToolPaint.paint_poly_ref() --> %s" % str(e))
                raise PaintSelectionError(_("No object available.")) from e

        return self.paint_poly_area_worker(
            painted_object=obj,
            areas_to_paint=reference_area,
            tooldia=tooldia,
            order=order,
            method=method,
            outname=outname,
            tools_storage=tools_storage,
            plot=plot,
            run_threaded=run_threaded,
            rest=rest,
            rest_offset=rest_offset,
            simplification=simplification,
        )

    def paint_poly_area_worker(
            self,
            painted_object: Union["GeometryObject", "GerberObject"],
            areas_to_paint: Union[Polygon, MultiPolygon] | list[Polygon | MultiPolygon],
            tooldia=None,
            order=None,
            method=None,
            outname=None,
            tools_storage=None,
            plot=True,
            run_threaded=True,
            rest=None,
            rest_offset=None,
            simplification=None,
    ):
        """
        Paints all polygons in this object that are within the sel_obj object

        :param painted_object: painted object
        :param areas_to_paint: paint only what is inside this object bounds; or a callable returning it
        :param tooldia: a tuple or single element made out of diameters of the tools to be used
        :param order: if the tools are ordered and how
        :param outname: name of the resulting object
        :param method: choice out of _("Seed"), 'normal', 'lines'
        :param tools_storage: whether to use the current tools_storage self.paints_tools or a different one.
        Usage of the different one is related to when this function is called from a TcL command.
        :param run_threaded:
        :param plot:
        :param rest: rest machining; None = the form value
        :param rest_offset: rest machining offset; None = the form value
        :param simplification: output tolerance in the object's units; None = the application option
        :return: 'fail' on failure when not threaded
        """

        self._announce_start(painted_object, _("Painting area task started."))

        def select():
            # this is where heavy lifting is done and creating the geometry to be painted
            areas = areas_to_paint() if callable(areas_to_paint) else areas_to_paint
            return self.select_in_areas(painted_object, areas)

        p_geo_list = self._resolve_selection(select, run_threaded)
        if p_geo_list is None:
            return 'fail'

        return self.paint_handler(
            painted_object,
            p_geo_list,
            tooldia=tooldia,
            order=order,
            method=method,
            outname=outname,
            tools_storage=tools_storage,
            plot=plot,
            run_threaded=run_threaded,
            rest=rest,
            rest_offset=rest_offset,
            simplification=simplification,
        )
