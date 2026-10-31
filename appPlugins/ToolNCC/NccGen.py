
from PyQt6 import QtCore, QtWidgets     # noqa

from copy import copy
import traceback
import logging

from shapely import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.geometry.base import BaseGeometry
from typing import TYPE_CHECKING, Literal, Union

import gettext
import appTranslation as fcTranslate
import builtins

from camlib import grace, flatten_shapely_geometry, residual_cleanup, object_circle_steps
from appParsers.feature_access import fused_solid
from appCommon.SafeEval import safe_eval
from appPlugins.ToolNCC import NccModel
from appObjects.ProjectContext import unique_name

if TYPE_CHECKING:
    from appObjects.GerberObject import GerberObject
    from appObjects.GeometryObject import GeometryObject
    from appMain import App

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def ncc_gerber_copper(obj, repair_buffer=None):
    """
    The copper of a Gerber object as one valid Polygon / MultiPolygon (``fused_solid``): the composed geometry with
    the polarity applied, repaired and fused. Nothing is changed on the object.

    :param repair_buffer:   if not None, a small tolerance buffer applied after the fuse (the NCC clip geometry keeps
                            the 1e-9 one it always had when the Gerber was loaded without buffering)
    """
    geo = fused_solid(obj)
    if repair_buffer is not None:
        geo = geo.buffer(repair_buffer)
    return geo


class NccGen:
    """Legacy controller adapter and compatibility facade for GUI and Tcl callers.

    Geometry kernels live in NccModel; settings, signals and output-object writes stay here.
    The area-selection state (first click, cursor, drawn points, areas) belongs to the controller (parent_tool).
    """
    def __init__(self, tool):
        self.app = tool.app
        self.parent_tool = tool

        self.ui = tool.ui

        self.decimals = tool.app.decimals
        self.temp_shapes = tool.temp_shapes

        self.circle_steps = tool.circle_steps

        self.obj_name = tool.obj_name
        self.select_method = tool.select_method
        self.bound_obj_name = tool.bound_obj_name
        self.bound_obj = tool.bound_obj

        self.o_name = tool.o_name

        self.ncc_obj = tool.ncc_obj

        self.ncc_dia_list = []
        self.iso_dia_list = []
        self.tooldia = tool.tooldia

        self.solid_geometry = tool.solid_geometry

        # the Check validity box as it was at the click; Area Selection runs later
        self.check_validity = False

    @property
    def ncc_tools(self) -> dict:
        """The tool table of the controller, which rebinds it when the table is rebuilt."""
        return self.parent_tool.ncc_tools

    def _option(self, key, fallback=None):
        """The option of the project, else the application default, else the factory default, else fallback."""
        value = self.app.options.get(key, None)
        if value is None:
            value = self.app.defaults.get(key, None)
        if value is None:
            value = self.app.defaults.factory_defaults.get(key, None)
        return fallback if value is None else value

    def _circle_steps_for(self, kind, radius=None):
        """
        The segments of a full circle, as the preferences "Circles" of the kind set them (Gerber or Geometry).

        :param kind:    the kind of the cleared object
        :param radius:  the radius of the circle; None for the shared steps of the job (the circles of a 1 mm
                        radius), the tool paths take their own from the tool radius
        """
        self.circle_kind = 'gerber' if kind == 'gerber' else 'geometry'
        if radius is None:
            radius = 1.0 if str(self.app.app_units).upper() == 'MM' else 1.0 / 25.4
        return object_circle_steps(self.app.options, self.circle_kind, radius, self.app.app_units)

    def _simplification(self):
        """The simplification tolerance in the units of the geometry (the preference is in millimetres)."""
        value = float(self._option('tools_ncc_simplification', 0.01))
        if self.app.units.upper() == 'IN':
            value /= 25.4
        return value

    def on_ncc_click(self):
        """
        Slot for clicking signal
        :return: None
        """

        self.app.defaults.report_usage("on_ncc_click")

        self.check_validity = bool(self.ui.valid_cb.get_value())
        self.parent_tool.clear_preview()

        prog_plot = True if self.app.options.get(
            "tools_ncc_plotting",
            self.app.defaults.get(
                "tools_ncc_plotting",
                self.app.defaults.factory_defaults.get("tools_ncc_plotting")
            )
        ) == 'progressive' else False
        if prog_plot:
            self.temp_shapes.clear(update=True)

        obj_type = self.ui.type_obj_radio.get_value()
        self.circle_steps = self._circle_steps_for(obj_type)
        self.obj_name = self.ui.obj_combo.currentText()

        # Get source object.
        try:
            self.ncc_obj = self.app.collection.get_by_name(self.obj_name)
        except Exception as e:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"),  str(self.obj_name)))
            return "Could not retrieve object: %s with error: %s" % (self.obj_name, str(e))

        if self.ncc_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(self.obj_name)))
            return

        if not self.read_selected_diameters():
            return

        self.o_name = '%s_ncc' % self.obj_name

        self.select_method = self.ui.select_method_combo.get_value()
        sources = self.source_objects()
        if sources is None:
            return
        names = self.output_names(sources)
        exclusion = self.parent_tool.exclusion_areas()
        if self.select_method == 0:   # Itself
            self.bound_obj_name = self.ui.obj_combo.currentText()
            # Get source object.
            try:
                self.bound_obj = self.app.collection.get_by_name(self.bound_obj_name)
            except Exception as e:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), self.bound_obj_name))
                return "Could not retrieve object: %s with error: %s" % (self.bound_obj_name, str(e))

            # one job per source, queued one after another: each job works on its own copy of the tool table
            for source, name in zip(sources, names):
                self.ncc_handler(cleared_object=source,
                                 clear_tooldia_list=self.ncc_dia_list,
                                 iso_tooldia_list=self.iso_dia_list,
                                 outname=name,
                                 tools_storage=self.ncc_tools,
                                 check_validity=self.check_validity,
                                 exclusion_areas=exclusion)
        elif self.select_method == 1:   # Area Selection
            self.parent_tool.start_area_selection()
        elif self.select_method == 2:   # Reference Object
            self.bound_obj_name = self.ui.reference_combo.currentText()
            # Get source object.
            try:
                self.bound_obj = self.app.collection.get_by_name(self.bound_obj_name)
            except Exception as e:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), self.bound_obj_name))
                return "Could not retrieve object: %s. Error: %s" % (self.bound_obj_name, str(e))

            for source, name in zip(sources, names):
                self.ncc_handler(
                    cleared_object=source,
                    sel_obj=self.bound_obj,
                    clear_tooldia_list=self.ncc_dia_list,
                    iso_tooldia_list=self.iso_dia_list,
                    outname=name,
                    check_validity=self.check_validity,
                    exclusion_areas=exclusion,
                )

    def read_selected_diameters(self):
        """
        Fill ncc_dia_list and iso_dia_list from the rows selected in the Tool Table.
        :return: False after a message if there is nothing to run with
        """
        # use the selected tools in the tool table; get diameters for isolation
        self.iso_dia_list = []
        # use the selected tools in the tool table; get diameters for non-copper clearing
        self.ncc_dia_list = []

        table_items = self.ui.tools_table.selectedItems()
        sel_rows = {t.row() for t in table_items}
        if len(sel_rows) > 0:
            for row in sel_rows:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    self.tooldia = float(self.ui.tools_table.item(row, 1).text().replace(',', '.'))
                except ValueError:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                    continue

                # find out which tools are for isolation and which are for copper clearing
                for uid_k, uid_v in self.ncc_tools.items():
                    if round(uid_v['tooldia'], self.decimals) == round(self.tooldia, self.decimals):
                        if uid_v['data']['tools_ncc_operation'] == "iso":
                            self.iso_dia_list.append(self.tooldia)
                        else:
                            self.ncc_dia_list.append(self.tooldia)
            return True
        self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
        return False

    def source_objects(self):
        """
        The objects to clear: the one of the combo box, or every Gerber / Geometry object selected in the project
        when "All selected objects" is on. Area Selection works with one object only.
        :return: the list of objects, or None after a message
        """
        if not self.ui.multi_cb.get_value():
            return [self.ncc_obj]
        selected = [obj for obj in self.app.collection.get_selected()
                    if getattr(obj, 'kind', None) in ('gerber', 'geometry')]
        if not selected:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "No Gerber or Geometry object is selected. The object of the combo box is used."))
            return [self.ncc_obj]
        if self.select_method == 1 and len(selected) > 1:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "Area Selection works with one object only. Select a single object or turn off All selected objects."))
            return None
        # the area selection and the messages work with the first one
        self.ncc_obj = selected[0]
        self.obj_name = selected[0].obj_options['name']
        self.o_name = '%s_ncc' % self.obj_name
        return selected

    def output_names(self, sources):
        """The name of the output object of each source: <source>_ncc, unique in the project and in this run."""
        if len(sources) == 1:
            return [self.o_name]
        used = set(self.app.collection.get_names())
        names = []
        for source in sources:
            name = unique_name('%s_ncc' % source.obj_options['name'], used)
            used.add(name)
            names.append(name)
        return names

    def compute_clipping_region(
            self,
            cleared_object: Union["GerberObject", "GeometryObject"],
            clipping_object: Union["GerberObject", "GeometryObject"] = None,
            clipping_type: int = 0,
            clipping_areas: Union[Polygon, MultiPolygon] | list[Polygon | MultiPolygon] = None,
    ) -> tuple[BaseGeometry | list[BaseGeometry] | None, str | None]:
        """
        Will return a geometry that dictate the total extent of the area to be copper cleared

        :param cleared_object:      The object to be copper cleared
        :param clipping_object:     The object whose geometry will be used as delimitation for copper clearing - if selected
        :param clipping_type:       Index that choose what kind of reference to be used for copper clearing extent
                                        0 -> _('Itself')
                                        1 -> _('Area Selection')
                                        2 -> _('Reference Object')
        :param clipping_areas:      The geometry that will be used as delimitation for copper clearing - if selected
        :return:                    The geometry that surrounds the area to be cleared and the kind of object from which the
                                    geometry originated (string: "gerber", "geometry" or None)
        """

        # when using an external object geometry as the area constraint for the copper clearing,
        box_kind = clipping_object.kind if clipping_object is not None else None
        env_obj = None

        if clipping_type == 0:     # _('Itself')
            return self._copper_clear_area_for_entire_object(cleared_object), box_kind

        if clipping_type == 1:   # _("Area Selection")
            if clipping_areas is None:
                return None, None
            return self._copper_clear_area_from_selection_areas(clipping_areas), box_kind

        if clipping_type == 2:   # _("Reference Object")
            if clipping_object is None:
                return None, None
            return self._copper_clear_area_from_reference_object(cleared_object, clipping_object, box_kind), box_kind

        return env_obj, box_kind

    def _copper_clear_area_for_entire_object(self, ncc_obj: Union["GerberObject", "GeometryObject"]):
        try:
            return NccModel.entire_object(ncc_obj.solid_geometry)
        except Exception as e:
            self.app.log.error("ToolNcc.calculate_bounding_box() 'itself'  --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No object available."))
            return None

    def _copper_clear_area_from_selection_areas(
            self,
            area_constraint: Union[Polygon, MultiPolygon] | list[Polygon | MultiPolygon],
    ):
        return NccModel.selection_areas(area_constraint)

    def _copper_clear_area_from_reference_object(
            self,
            cleared_object: Union["GerberObject", "GeometryObject"],
            box_obj: Union["GerberObject", "GeometryObject"],
            box_kind: str,
    ):
        if box_kind not in ('geometry', 'gerber'):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The reference object type is not supported."))
            return None
        source_geometry = cleared_object.solid_geometry if box_kind == 'gerber' else None
        return NccModel.reference_area(source_geometry, box_obj.solid_geometry, box_kind)

    def apply_margin_to_clipping_region(
            self,
            clipping_region: Union[BaseGeometry, list[BaseGeometry]],
            box_kind: str,
            clipping_type,
            margin,
    ):
        """Apply the resolved margin to the clipping region."""
        self.app.log.debug("NCC Tool. Preparing non-copper polygons.")
        self.app.inform.emit(_("NCC Tool. Preparing non-copper polygons."))
        if clipping_region is None:
            return 'fail'
        if clipping_type == 2 and box_kind not in ('geometry', 'gerber'):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The reference object type is not supported."))
            return 'fail'
        try:
            result = NccModel.apply_margin(
                clipping_region, box_kind, clipping_type, margin, lambda: NccGen._check_abort(self))
        except grace:
            raise
        except Exception as err:
            self.app.log.error("ToolNcc.apply_margin_to_bounding_box() --> %s" % str(err))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No object available."))
            return 'fail'
        self.app.log.debug("NCC Tool. Finished non-copper polygons.")
        return result

    def compute_area_to_clear(
            self,
            name: str,
            cleared_object: Union["GerberObject", "GeometryObject"],
            geo_obj,
            iso_tooldia,
            has_offset,
            offset,
            margin,
            clipping_region,
            tools_storage,
            clipping_geometry=None,
            exclusion=None,
    ):
        """Legacy area seam. Scheduled jobs pass a plain dictionary for isolation output."""
        self.app.inform.emit(_("NCC Tool. Calculate 'empty' area."))
        if clipping_geometry is not None:
            geometry = clipping_geometry
            isolation = []
            milling_type = None
        elif cleared_object.kind == 'gerber':
            buffering_off = not iso_tooldia and self.app.options.get(
                'gerber_buffering', self.app.defaults.get(
                    'gerber_buffering', self.app.defaults.factory_defaults.get('gerber_buffering'))) == 'no'
            geometry = ncc_gerber_copper(cleared_object, 1e-9 if buffering_off and not iso_tooldia else None)
            isolation = iso_tooldia or []
            milling_type = (geo_obj['milling_type'] if isinstance(geo_obj, dict)
                            else self.ui.milling_type_radio.get_value()) if isolation else None
        elif cleared_object.kind == 'geometry':
            geometry = unary_union(cleared_object.solid_geometry)
            isolation = []
            milling_type = None
        else:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('The selected object is not suitable for copper clearing.'))
            return 'fail', 0
        # Resolve isolation settings in the caller before scheduling; direct legacy calls still read the UI.
        if isinstance(geo_obj, dict) and 'milling_type' in geo_obj:
            milling_type = geo_obj['milling_type']
        if isolation:
            self.solid_geometry = geometry
        area, generated, warning, messages = NccModel.prepare_area(
            geometry, clipping_region, isolation, tools_storage, name, self.decimals,
            has_offset, offset, margin, milling_type, lambda *args: self.generate_envelope(*args),
            self._compute_area_to_clear_handler, lambda: NccGen._process_events_and_check_abort(self), exclusion,
            self.circle_steps)
        # NccModel translates its messages
        for message in messages:
            self.app.inform.emit('[WARNING_NOTCL] %s' % message)
        if isinstance(area, str):
            if area == 'empty':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('The area to clear is empty.'))
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    'Could not get the extent of the area to be non copper cleared.'))
            return 'fail', 0
        if isinstance(geo_obj, dict):
            geo_obj['tools'].update(generated)
        else:
            geo_obj.tools.update(generated)
            tools_storage.update(NccModel.copy_tools(generated))
        self.app.inform.emit(_("NCC Tool. Finished calculation of 'empty' area."))
        return area, warning

    def compute_clear_area(
            self,
            cleared_object: Union["GerberObject", "GeometryObject"],
            clipping_type: int = 0,
            clipping_object: Union["GerberObject", "GeometryObject"] = None,
            clipping_areas: Union[Polygon, MultiPolygon] | list[Polygon | MultiPolygon] = None,
            margin: float = 0.0,
            iso_tooldia: list = None,
            has_offset: bool = False,
            offset: float = 0.0,
            milling_type: str = None,
            tools_storage: dict = None,
            exclusion: Union[Polygon, MultiPolygon] = None,
            name: str = '',
    ):
        """
        The area that would be cleared, without any clearing toolpath: the region (object, selection or reference)
        with the margin, the isolation envelope of the isolation tools and the offset applied, and the exclusion
        removed.

        :return:    (area, isolation tools, broken count): the area is a MultiPolygon, or 'fail' (the reason was
                    reported); the isolation tools are the tool entries that hold the isolation paths.
        """
        region, kind = self.compute_clipping_region(
            cleared_object=cleared_object, clipping_object=clipping_object,
            clipping_type=clipping_type, clipping_areas=clipping_areas)
        if region is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("NCC Tool failed creating bounding box."))
            return 'fail', {}, 0
        boundary = self.apply_margin_to_clipping_region(
            clipping_region=region, box_kind=kind, clipping_type=clipping_type, margin=margin)
        if boundary is None or isinstance(boundary, str):
            return 'fail', {}, 0
        staged = {'tools': {}, 'milling_type': milling_type}
        area, warning = self.compute_area_to_clear(
            name=name, cleared_object=cleared_object, geo_obj=staged, iso_tooldia=iso_tooldia or [],
            has_offset=has_offset, offset=offset, margin=margin, clipping_region=boundary,
            tools_storage=tools_storage if tools_storage is not None else self.ncc_tools, exclusion=exclusion)
        return area, staged['tools'], warning

    def clear_polygon_worker(self, pol, tooldia, ncc_method, ncc_overlap, ncc_connect, ncc_contour, prog_plot,
                             simplify_tol=0.0):

        # Resolve engines lazily: unbound callers may supply only the selected engine.
        paths, messages = NccModel.clear_polygon(
            pol, tooldia, ncc_method, ncc_overlap, ncc_connect, ncc_contour, prog_plot,
            object_circle_steps(self.app.options, getattr(self, 'circle_kind', 'geometry'), tooldia / 2,
                                self.app.app_units),
            lambda *args, **kwargs: self.parent_tool.clear_polygon_shrink(*args, **kwargs),
            lambda *args, **kwargs: self.parent_tool.clear_polygon_seed(*args, **kwargs),
            lambda *args, **kwargs: self.parent_tool.clear_polygon_lines(*args, **kwargs), simplify_tol)
        for message in messages:
            self.app.inform.emit(message)
        if paths is None:
            pt = pol.representative_point()
            coords = (pt.x, pt.y)
            self.app.inform_shell.emit('%s %s' % (_('Polygon could not be cleared. Location:'), str(coords)))
        return paths

    def _cleanup_result(self, tools, generated, domains, name, settings=None, source=None):
        if not any(tools[uid]['data'].get('tools_ncc_final_cleanup', False) for uid in domains):
            return generated, None
        copper = ncc_gerber_copper(source) if source is not None and source.kind == 'gerber' else None
        return NccModel.finish_cleanup(
            tools, generated, domains, name, self.circle_steps,
            lambda domain, dia, steps, previous, generate: residual_cleanup(
                self.app, domain, dia, steps, previous, generate),
            self.clear_polygon_worker, lambda: NccGen._process_events_and_check_abort(self), settings, copper)

    def final_cleanup(self, output, tools_storage, domains, name, settings=None, source=None):
        """Compatibility adapter for callers that already own an output object."""
        tools, residual = NccGen._cleanup_result(self, tools_storage, output.tools, domains, name, settings, source)
        if residual is None:
            return
        output.tools = tools
        for uid, value in tools.items():
            tools_storage[uid] = value
            if 'ncc_cleanup_start' in value:
                tools_storage[uid] = tools_storage.pop(uid)
        output.ncc_cleanup_residual = residual
        self.app.poly_not_cleared = not residual.is_empty
        if not residual.is_empty:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                'Final cleanup finished. Some residual copper could not be cleared with this tool.'))

    def clear_ncc_area_selection_option(self, areas_to_clear_list: list[Polygon | MultiPolygon]):
        self.app.log.info("NCCGen.clear_ncc_area_selection_option() --> Clearing the designated areas(s).")

        areas_to_clear_list = unary_union(areas_to_clear_list)
        if isinstance(areas_to_clear_list, MultiPolygon):
            areas_to_clear_list = list(areas_to_clear_list.geoms)
        elif isinstance(areas_to_clear_list, Polygon):
            areas_to_clear_list = [areas_to_clear_list]

        self.ncc_handler(
            cleared_object=self.ncc_obj,
            clear_tooldia_list=self.ncc_dia_list,
            iso_tooldia_list=self.iso_dia_list,
            areas_to_clear=areas_to_clear_list,
            sel_obj=self.bound_obj,
            outname=self.o_name,
            check_validity=self.check_validity,
            exclusion_areas=self.parent_tool.exclusion_areas(),
        )

    def _publish_result(self, output, tools, name, last_tool, failed, residual, warning, mill_data=None):
        """
        Publish only after generation and cleanup both succeeded. The output object gets its own copies; the tool
        table of the plugin is never written here.

        :param mill_data: dict of tools_mill_* values (Cut Z, V-tip) written into the data of each output tool
        """
        NccGen._check_abort(self)
        if not tools or not any(value.get('solid_geometry') for value in tools.values()):
            self.app.poly_not_cleared = False
            self.app.inform.emit('[ERROR] %s' % _(
                'There is no NCC Geometry in the file.\n'
                'Usually it means that the tool diameter is too big for the painted geometry.\n'
                'Change the painting parameters and try again.'))
            return 'fail'
        tools = NccModel.copy_tools(tools)
        if mill_data:
            for value in tools.values():
                value['data'].update(mill_data)
        output.tools = tools
        output.solid_geometry = [path for value in tools.values()
                                 for path in flatten_shapely_geometry(value['solid_geometry'])]
        output.multigeo = True
        output.obj_options['tools_mill_tooldia'] = str(last_tool)
        if residual is not None:
            output.ncc_cleanup_residual = residual
            failed = not residual.is_empty
            if failed:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    'Final cleanup finished. Some residual copper could not be cleared with this tool.'))
        self.app.poly_not_cleared = failed
        if warning:
            self.app.inform.emit('[WARNING] %s: %s %s.' % (
                _("NCC Tool clear all done but the copper features isolation is broken for"),
                str(warning), _("tools")))
        else:
            self.app.inform.emit('[success] %s' % _("NCC Tool clear all done."))

    def _tool_progress(self, diameter, units):
        self.app.log.debug("Starting geometry processing for tool: %s" % str(diameter))
        self.app.inform.emit('[success] %s = %s%s %s' % (
            _('NCC Tool clearing with tool diameter'), str(diameter), units.lower(), _('started.')))

    def _tools_fit(self, source, clear_diameters, iso_diameters):
        """
        The Check validity test. Runs in the worker before the generation.
        :return: True if the run has to stop
        """
        if getattr(source, 'kind', None) != 'gerber':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "Check validity is skipped: the source is not a Gerber object."))
            return False
        level, gap, too_big, note = NccModel.check_tools_fit(
            getattr(source, 'tools', {}), clear_diameters, iso_diameters, self.decimals)
        if note:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (note, _("Check validity is skipped.")))
            return False
        units = self.app.units.lower()
        if level == 'error':
            self.app.inform.emit('[ERROR_NOTCL] %s' % (
                _("Check validity: the smallest copper gap is %(gap)s%(units)s. "
                  "Tools too big for it: %(tools)s. Use smaller tools or turn off Check validity.") %
                {'gap': gap, 'units': units, 'tools': ', '.join(str(dia) for dia in too_big)}))
            return True
        if level == 'warning':
            self.app.inform.emit('[WARNING_NOTCL] %s' % (
                _("Check validity: the smallest copper gap is %(gap)s%(units)s. "
                  "These tools will not enter all the gaps: %(tools)s.") %
                {'gap': gap, 'units': units, 'tools': ', '.join(str(dia) for dia in too_big)}))
        return False

    def ncc_handler(
            self,
            cleared_object: Union["GerberObject", "GeometryObject"],
            clear_tooldia_list: list[int | float | str],
            iso_tooldia_list: list[int | float | str],
            areas_to_clear: list[Polygon | MultiPolygon] = None,
            sel_obj: Union["GerberObject", "GeometryObject"] = None,
            outname: str = None,
            tool_ordering: int = None,
            tools_storage: dict = None,
            run_threaded: bool = True,
            check_validity: bool = False,
            exclusion_areas: list[Polygon | MultiPolygon] = None,
    ):
        """
        Resolve GUI input, run explicit-data generation, then publish through the tool factory.

        :param check_validity:      stop before the generation if a tool is too big for the smallest copper gap
        :param exclusion_areas:     polygons that are removed from the area to clear
        """
        # Keep circle accuracy and isolation staging local to this request, not the next GUI job.
        self = copy(self)
        # each source has its own accuracy: with "All selected objects" a Gerber and a Geometry can be in one run
        if getattr(cleared_object, 'kind', None) in ('gerber', 'geometry'):
            self.circle_steps = self._circle_steps_for(cleared_object.kind)
        self.app.log.debug("Executing the ncc_handler() ...")
        tolerance = self._simplification()
        rest = self.ui.rest_cb.get_value()
        order = tool_ordering if tool_ordering is not None else self.ui.order_combo.get_value()
        clipping_type = self.ui.select_method_combo.get_value()
        margin = self.ui.rest_margin_entry.get_value() if rest else self.ui.margin_entry.get_value()
        connect = self.ui.rest_connect_cb.get_value() if rest else False
        contour = self.ui.rest_contour_cb.get_value() if rest else True
        has_offset = self.ui.rest_offset_choice_cb.get_value() if rest else False
        offset = self.ui.rest_offset_entry.get_value() if rest else 0.0
        milling_type = self.ui.milling_type_radio.get_value() if iso_tooldia_list else None
        prog_plot = self.app.options.get('tools_ncc_plotting', self.app.defaults.get(
            'tools_ncc_plotting', self.app.defaults.factory_defaults.get('tools_ncc_plotting'))) == 'progressive'
        # the NCC preferences for Cut Z and the V-tip go to the output tools, like in the Isolation plugin
        mill_data = {'tools_mill_cutz': self._option('tools_ncc_cutz'),
                     'tools_mill_vtipdia': self._option('tools_ncc_tipdia'),
                     'tools_mill_vtipangle': self._option('tools_ncc_tipangle')}
        mill_data = {key: value for key, value in mill_data.items() if value is not None}
        units = self.app.units
        storage = tools_storage if tools_storage is not None else self.ncc_tools
        # the worker reads these copies: the table may be rebuilt while it runs
        snapshot = NccModel.copy_tools(storage)
        tool_settings = NccModel.copy_tools(self.ncc_tools)
        diameters = list(clear_tooldia_list)
        if not diameters:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return 'fail'
        if rest or order == 2:
            diameters.sort(reverse=True)
        elif order == 1:
            diameters.sort()
        exclusion = unary_union(exclusion_areas) if exclusion_areas else None
        name = outname if outname is not None else self.obj_name + '_ncc'
        if run_threaded:
            proc = self.app.proc_container.new('%s...' % _("Working"))
        else:
            self.app.proc_container.view.set_busy('%s...' % _("Working"))
            QtWidgets.QApplication.processEvents()

        def generate(output, app_obj):
            NccGen._process_events_and_check_abort(self)
            if check_validity and self._tools_fit(cleared_object, diameters, list(iso_tooldia_list)):
                return 'fail'
            region, kind = self.compute_clipping_region(
                cleared_object=cleared_object, clipping_object=sel_obj,
                clipping_type=clipping_type, clipping_areas=areas_to_clear)
            if region is None:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("NCC Tool failed creating bounding box."))
                return 'fail'
            boundary = self.apply_margin_to_clipping_region(
                clipping_region=region, box_kind=kind, clipping_type=clipping_type, margin=margin)
            if boundary is None or isinstance(boundary, str):
                return 'fail'
            # This is plain staging data, never the registered output object.
            staged = {'tools': {}, 'milling_type': milling_type}
            prepared = {}
            areas = {}
            warning = 0
            for diameter in diameters:
                NccGen._process_events_and_check_abort(self)
                uid = NccModel.tool_uid(tool_settings, diameter, self.decimals)
                if uid is None:
                    continue
                data = tool_settings[uid]['data']
                use_offset = has_offset if rest else data['tools_ncc_offset_choice']
                distance = offset if rest else float(data['tools_ncc_offset_value'])
                key = (use_offset is True, distance if use_offset is True else 0.0)
                if key not in prepared:
                    prepared[key] = self.compute_area_to_clear(
                        name=name, cleared_object=cleared_object, geo_obj=staged,
                        iso_tooldia=iso_tooldia_list, has_offset=use_offset, offset=distance,
                        margin=margin, clipping_region=boundary, tools_storage=snapshot, exclusion=exclusion)
                areas[uid], warning = prepared[key]
                if rest and isinstance(areas[uid], str):
                    return 'fail'
            if areas and all(isinstance(area, str) for area in areas.values()):
                return 'fail'
            generated, domains, residual, failed, last = NccModel.generate_tools(
                'gui_rest' if rest else 'gui_normal', tool_settings, snapshot, diameters, areas,
                name, self.decimals, self.circle_steps, self.clear_polygon_worker,
                lambda: NccGen._process_events_and_check_abort(self),
                self.app.proc_container.update_view_text, connect=connect, contour=contour,
                simplify_tol=tolerance, prog_plot=prog_plot, initial_tools=staged['tools'],
                tool_progress=lambda diameter: self._tool_progress(diameter, units))
            if not rest:
                self._warn_unused_tools(tool_settings, diameters, generated)
            generated, cleanup_residual = self._cleanup_result(
                snapshot, generated, domains, name,
                dict(tools_ncc_connect=connect, tools_ncc_contour=contour) if rest else None,
                cleared_object)
            if prog_plot:
                self.temp_shapes.clear(update=True)
            return self._publish_result(output, generated, name, '0.0' if rest else last,
                                        failed, cleanup_residual, warning, mill_data)

        def job_thread(app_instance):
            try:
                result = self.parent_tool.new_object(
                    'geometry', name, generate, source=cleared_object, autoselected=False)
                if result == 'fail' or result is False:
                    return 'fail'
            except grace:
                app_instance.log.debug("NccGen.ncc_handler.job_thread() -> Graceful exit.")
                return 'fail'
            except Exception as err:
                app_instance.log.debug("NccGen.ncc_handler.job_thread() -> Exception: %s" % str(err))
                self.app.inform.emit('[ERROR] %s' % _("Failed."))
                return 'fail'
            finally:
                if prog_plot:
                    self.temp_shapes.clear(update=True)
                if run_threaded:
                    proc.done()
                else:
                    app_instance.proc_container.view.set_idle()
            self.app.inform.emit('[success] %s' % _("Done."))

        if run_threaded:
            self.app.collection.promise(name)
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})
        else:
            return job_thread(self.app)

    def clear_copper_tcl(
            self,
            ncc_obj,
            sel_obj=None,
            areas_to_clear_list=None,
            ncc_tooldia=None,
            iso_tooldia=None,
            margin=None,
            has_offset=None,
            offset=None,
            select_method=None,
            outname=None,
            overlap=None,
            connect=None,
            contour=None,
            order: Literal[0, 1, 2, "no", "fwd", "rev"] | None=None,
            method=None,
            rest=None,
            tools_storage=None,
            plot=True,
            run_threaded=False,
            final_cleanup=None,
            milling_type=None,
            simplification=None,
            exclusion_areas=None,
    ):
        """
        Tcl adapter. Synchronous calls finish object creation before returning.

        Every parameter that is None takes its tools_ncc_* value from the application options.
        overlap is a fraction (0.4), simplification is in the units of the geometry (the option is in millimetres),
        exclusion_areas is a list of polygons that are removed from the area to clear.
        """
        self = copy(self)
        # the current Gerber / Geometry accuracy, not the one of the last GUI click
        self.circle_steps = self._circle_steps_for(ncc_obj.kind)
        margin = float(margin if margin is not None else self._option('tools_ncc_margin', 1.0))
        overlap = float(overlap if overlap is not None else float(self._option('tools_ncc_overlap', 40)) / 100)
        connect = bool(connect if connect is not None else self._option('tools_ncc_connect', False))
        contour = bool(contour if contour is not None else self._option('tools_ncc_contour', True))
        method = int(method if method is not None else self._option('tools_ncc_method', 0))
        if order is None:
            order = self._option('tools_ncc_order', 0)
        order = {"0": 0, "1": 1, "2": 2, "no": 0, "fwd": 1, "rev": 2}.get(str(order).strip().lower())
        if order is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The order must be no, fwd or rev."))
            return 'fail'
        rest = bool(rest if rest is not None else self._option('tools_ncc_rest', False))
        has_offset = bool(has_offset if has_offset is not None else self._option('tools_ncc_offset_choice', False))
        offset = float(offset if offset is not None else self._option('tools_ncc_offset_value', 0.0))
        select_method = int(select_method if select_method is not None else self._option('tools_ncc_ref', 0))
        final_cleanup = bool(final_cleanup if final_cleanup is not None else
                             self._option('tools_ncc_final_cleanup', False))
        simplify_tol = float(simplification) if simplification is not None else self._simplification()
        storage = tools_storage if tools_storage is not None else self.ncc_tools
        snapshot = NccModel.copy_tools(storage)
        try:
            diameters = [float(safe_eval(dia)) for dia in ncc_tooldia.split(',') if dia != '']
        except AttributeError:
            diameters = list(ncc_tooldia) if isinstance(ncc_tooldia, list) else [float(ncc_tooldia)]
        if not diameters:
            return 'fail'
        if rest or order == 2:
            diameters.sort(reverse=True)
        elif order == 1:
            diameters.sort()
        for value in snapshot.values():
            if NccModel.in_diameters(value['tooldia'], diameters, self.decimals):
                value['data']['tools_ncc_final_cleanup'] = final_cleanup
        reference = None if sel_obj is None or sel_obj == 0 else sel_obj
        if select_method == 0:
            region = NccModel.entire_object(flatten_shapely_geometry(ncc_obj.solid_geometry))
            boundary = NccModel.apply_margin(region, None, 0, margin)
        elif select_method == 1:
            if areas_to_clear_list is None:
                return 'fail'
            boundary = NccModel.apply_margin(
                NccModel.selection_areas(areas_to_clear_list), None, 1, margin,
                lambda: NccGen._process_events_and_check_abort(self))
        elif select_method == 2 and reference is not None and reference.kind in ('geometry', 'gerber'):
            # Same region as the GUI: for a Gerber reference, the source hull inside the reference hull.
            region = NccModel.reference_area(ncc_obj.solid_geometry, reference.solid_geometry, reference.kind)
            boundary = NccModel.apply_margin(
                region, reference.kind, 2, margin, lambda: NccGen._process_events_and_check_abort(self))
        else:
            return 'fail'
        name = outname if outname is not None else self.obj_name + ('_ncc_rm' if rest else '_ncc')
        if milling_type is None and iso_tooldia:
            milling_type = self._option('tools_ncc_milling_type', 'cl')
        if not iso_tooldia:
            milling_type = None
        exclusion = unary_union(exclusion_areas) if exclusion_areas else None
        units = self.app.app_units
        if run_threaded:
            proc = self.app.proc_container.new('%s...' % _("Working"))
        else:
            self.app.proc_container.view.set_busy('%s...' % _("Working"))
            QtWidgets.QApplication.processEvents()

        def generate(output, app_obj):
            assert output.kind == 'geometry', "Initializer expected a GeometryObject, got %s" % type(output)
            NccGen._process_events_and_check_abort(self)
            if ncc_obj.kind == 'gerber':
                geometry = ncc_gerber_copper(ncc_obj)
                isolation = iso_tooldia or []
            elif ncc_obj.kind == 'geometry':
                geometry = unary_union(ncc_obj.solid_geometry)
                isolation = []
            else:
                return 'fail'
            if isolation:
                self.solid_geometry = geometry
            area, initial, warning, messages = NccModel.prepare_area(
                geometry, boundary, isolation, snapshot, name, self.decimals,
                has_offset, offset if has_offset is True else 0.0, margin, milling_type,
                self.generate_envelope, self._compute_area_to_clear_handler,
                lambda: NccGen._process_events_and_check_abort(self), exclusion, self.circle_steps)
            for message in messages:
                self.app.inform.emit('[WARNING_NOTCL] %s' % message)
            if isinstance(area, str):
                self.app.inform.emit('[ERROR_NOTCL] %s' % (
                    _('The area to clear is empty.') if area == 'empty' else
                    _('Could not get the extent of the area to be non copper cleared.')))
                return 'fail'
            areas = {uid: area for uid, value in snapshot.items()
                     if NccModel.in_diameters(value['tooldia'], diameters, self.decimals)}
            generated, domains, residual, failed, last = NccModel.generate_tools(
                'tcl_rest' if rest else 'tcl_normal', snapshot, snapshot, diameters, areas,
                name, self.decimals, self.circle_steps, self.clear_polygon_worker,
                lambda: NccGen._process_events_and_check_abort(self), self.app.proc_container.update_view_text,
                connect=connect, contour=contour, overlap=overlap, method=method, simplify_tol=simplify_tol,
                initial_tools=initial, tool_progress=lambda diameter: self._tool_progress(diameter, units))
            if not rest:
                self._warn_unused_tools(snapshot, diameters, generated)
            generated, cleanup_residual = self._cleanup_result(
                snapshot, generated, domains, name,
                dict(tools_ncc_method=method, tools_ncc_overlap=overlap * 100,
                     tools_ncc_connect=connect, tools_ncc_contour=contour), ncc_obj)
            return self._publish_result(output, generated, name, last, failed, cleanup_residual, warning)

        def job_thread(app_obj):
            try:
                result = app_obj.app_obj.new_object('geometry', name, generate, plot=plot)
                if result == 'fail' or result is False:
                    return 'fail'
            except grace:
                return 'fail'
            except Exception as err:
                self.app.log.error("NccGen.clear_copper_tcl() --> %s" % str(err))
                traceback.print_stack()
                return 'fail'
            finally:
                if run_threaded:
                    proc.done()
                else:
                    app_obj.proc_container.view.set_idle()

        if run_threaded:
            self.app.collection.promise(name)
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})
        else:
            return job_thread(self.app)

    def _warn_unused_tools(self, tool_settings, diameters, generated):
        for diameter in diameters:
            uid = NccModel.tool_uid(tool_settings, diameter, self.decimals)
            if uid is not None and uid not in generated:
                self.app.inform.emit('[WARNING_NOTCL] %s %s: %s %s: %s' % (
                    _("Could not use the tool for copper clear."), _("Tool"), str(uid),
                    _("with diameter"), str(tool_settings[uid]['tooldia'])))

    def _compute_area_to_clear_handler(self, target, boundary=None):
        """Return the complement of target inside boundary, retaining the public Tcl seam."""
        ret_val = NccModel.complement(
            target, boundary, lambda: NccGen._process_events_and_check_abort(self),
            lambda text: self.app.proc_container.update_view_text(text))
        if isinstance(ret_val, str):
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Try to use the Buffering Type = Full in Preferences -> Gerber General. "
                                   "Reload the Gerber file after this change."))
        return ret_val

    def _check_abort(self):
        if self.app.abort_flag:
            raise grace

    def _process_events_and_check_abort(self):
        # processEvents() belongs to the GUI thread; the workers only check the abort flag
        app = QtWidgets.QApplication.instance()
        if app is not None and QtCore.QThread.currentThread() == app.thread():
            QtWidgets.QApplication.processEvents()
        NccGen._check_abort(self)

    @staticmethod
    def poly2rings(poly):
        return NccModel.poly2rings(poly)

    def generate_envelope(
            self,
            offset,
            invert,
            envelope_iso_type=2,
    ):
        return NccModel.generate_envelope(
            self.solid_geometry, offset, invert, envelope_iso_type,
            lambda *args, **kwargs: self.parent_tool.isolation_geometry(*args, **kwargs))
