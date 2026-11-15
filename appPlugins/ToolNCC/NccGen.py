
from PyQt6 import QtCore, QtWidgets     # noqa

from copy import copy
import traceback
import logging

from shapely import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.geometry import box as shapely_box
from shapely.geometry.base import BaseGeometry
from typing import TYPE_CHECKING, Literal, Union

import gettext
import appTranslation as fcTranslate
import builtins

from camlib import grace, residual_cleanup, object_circle_steps, clearing_simplification, \
    project_units, clearing_error, crosshatch_angle
from appParsers.feature_access import fused_solid
from appCommon.SafeEval import safe_eval
from appPlugins.ToolNCC import NccModel
from appPlugins.Common.clearing import regions
from appPlugins.Common.clearing.job import ClearingJob, NCC_GUI, NCC_TCL, stock_is_left
from appPlugins.Common.clearing.params import (
    JobSpec, SourceSnapshot, copy_geometry, distinct_zones, freeze_options, object_zones, tool_spec,
    tool_direction)
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


def click_faces(source):
    """The copper-free faces of a Gerber / Geometry source: the regions that a click can pick."""
    if source.kind == 'gerber':
        copper = ncc_gerber_copper(source)
    else:
        copper = unary_union(source.solid_geometry)
    if copper.is_empty:
        return []
    return regions.empty_faces(shapely_box(*copper.bounds), copper)


def picked_faces(faces, points):
    """The distinct faces under the points."""
    picked = []
    for point in points or ():
        face = regions.face_at(faces, point)
        if face is not None and not any(face.equals(known) for known in picked):
            picked.append(face)
    return picked


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
        if project_units(self.app) == 'IN':
            value /= 25.4
        return clearing_simplification(self.app, value)

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
        elif self.select_method == 3:   # Polygon Selection
            self.parent_tool.start_click_selection(click_faces(self.ncc_obj))
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
        if self.select_method in (1, 3) and len(selected) > 1:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The selection works with one object only. Select a single object or turn off All selected objects."))
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
            result = regions.apply_margin(
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
                             simplify_tol=0.0, angle=0.0, direction='none'):

        # Resolve engines lazily: unbound callers may supply only the selected engine.
        paths, messages = NccModel.clear_polygon(
            pol, tooldia, ncc_method, ncc_overlap, ncc_connect, ncc_contour, prog_plot,
            object_circle_steps(self.app.options, getattr(self, 'circle_kind', 'geometry'), tooldia / 2,
                                self.app.app_units),
            lambda *args, **kwargs: self.parent_tool.clear_polygon_shrink(*args, **kwargs),
            lambda *args, **kwargs: self.parent_tool.clear_polygon_seed(*args, **kwargs),
            lambda *args, **kwargs: self.parent_tool.clear_polygon_lines(*args, **kwargs), simplify_tol,
            lambda *args, **kwargs: self.parent_tool.clear_polygon_crosshatch(*args, **kwargs), angle, direction)
        for message in messages:
            self.app.inform.emit(message)
        if paths is None:
            pt = pol.representative_point()
            coords = (pt.x, pt.y)
            self.app.inform_shell.emit('%s %s' % (_('Polygon could not be cleared. Location:'), str(coords)))
        return paths

    def _cleanup_result(self, tools, generated, domains, name, source=None):
        if not any(tools[uid]['data'].get('tools_ncc_final_cleanup', False) for uid in domains):
            return generated, None, None
        copper = ncc_gerber_copper(source) if source is not None and source.kind == 'gerber' else None
        return NccModel.finish_cleanup(
            tools, generated, domains, name,
            lambda domain, dia, previous, generate, boundary_first, direction='none': residual_cleanup(
                self.app, domain, dia, self.circle_steps, previous, generate, physical=True,
                boundary_first=boundary_first, direction=direction),
            self.clear_polygon_worker, lambda: NccGen._process_events_and_check_abort(self), copper)

    def final_cleanup(self, output, tools_storage, domains, name, source=None):
        """Compatibility adapter for callers that already own an output object."""
        tools, residual, cleanup_diameter = NccGen._cleanup_result(self, tools_storage, output.tools, domains, name, source)
        if residual is None:
            return
        output.tools = tools
        for uid, value in tools.items():
            tools_storage[uid] = value
            if 'ncc_cleanup_start' in value:
                tools_storage[uid] = tools_storage.pop(uid)
        output.ncc_cleanup_residual = residual
        self.app.poly_not_cleared = not residual.is_empty
        if stock_is_left(residual, cleanup_diameter, False):
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

    def clear_ncc_click_option(self, points):
        """Polygon Selection: clear the faces under the clicked points."""
        self.ncc_handler(
            cleared_object=self.ncc_obj,
            clear_tooldia_list=self.ncc_dia_list,
            iso_tooldia_list=self.iso_dia_list,
            click_points=points,
            outname=self.o_name,
            check_validity=self.check_validity,
            exclusion_areas=self.parent_tool.exclusion_areas(),
        )

    def _publish_result(self, output, tools, name, last_tool, failed, residual, warning, mill_data=None,
                        last_diameter=None, cleanup_diameter=None):
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
        for value in tools.values():
            # the G-code keeps the cut direction of the clearing: no reversal of a path
            if tool_direction(value['data'], 'tools_ncc_') != 'none':
                value['data']['tools_mill_keep_direction'] = True
        output.tools = tools
        output.obj_options['tools_mill_tooldia'] = str(last_tool)
        cleaned = residual is not None
        if cleaned:
            output.ncc_cleanup_residual = residual
            if stock_is_left(residual, cleanup_diameter, False):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    'Final cleanup finished. Some residual copper could not be cleared with this tool.'))
        # the rest stock was measured by the rule of both tools (failed); the cleanup residual replaces it
        if ClearingJob.finish_output(self, output, self.app, stock=residual if cleaned else None,
                                     last_diameter=last_diameter, failed=failed and not cleaned,
                                     warn=not cleaned) == 'fail':
            self.app.poly_not_cleared = False
            return 'fail'
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

    def _tool_specs(self, tool_settings, diameters):
        specs = []
        for diameter in diameters:
            uid = NccModel.tool_uid(tool_settings, diameter, self.decimals)
            if uid is not None:
                specs.append(tool_spec(uid, diameter, tool_settings[uid]['data'], 'tools_ncc_', 'offset_value'))
        return specs

    def _mill_data(self):
        """The NCC preferences for Cut Z and the V-tip go to the output tools, like in the Isolation plugin."""
        mill_data = {'tools_mill_cutz': self._option('tools_ncc_cutz'),
                     'tools_mill_vtipdia': self._option('tools_ncc_tipdia'),
                     'tools_mill_vtipangle': self._option('tools_ncc_tipangle')}
        return {key: value for key, value in mill_data.items() if value is not None}

    def _ncc_spec(self, options, **fields):
        """The JobSpec of an NCC run: the fields that both resolvers share are filled here."""
        units = self.app.units
        app_units = getattr(self.app, 'app_units', units)
        return JobSpec(
            options=options, circle_steps=self.circle_steps,
            decimals=self.decimals, units=units, app_units=app_units, **fields)

    def resolve_gui_job(
            self,
            cleared_object: Union["GerberObject", "GeometryObject"],
            clear_tooldia_list: list[int | float | str],
            iso_tooldia_list: list[int | float | str],
            areas_to_clear: list[Polygon | MultiPolygon] = None,
            sel_obj: Union["GerberObject", "GeometryObject"] = None,
            outname: str = None,
            tool_ordering: int = None,
            tools_storage: dict = None,
            check_validity: bool = False,
            exclusion_areas: list[Polygon | MultiPolygon] = None,
            click_points: list[tuple] = None,
    ):
        """
        The GUI resolver: read the widgets, the tool table and the options into a JobSpec. The run reads only the
        spec. Call it on a copy of the controller (it sets the circle steps of the source kind).

        :return: the JobSpec; 'fail' when no tool is selected
        """
        # each source has its own accuracy: with "All selected objects" a Gerber and a Geometry can be in one run
        if getattr(cleared_object, 'kind', None) in ('gerber', 'geometry'):
            self.circle_steps = self._circle_steps_for(cleared_object.kind)
        tolerance = self._simplification()
        rest = self.ui.rest_cb.get_value()
        order = tool_ordering if tool_ordering is not None else self.ui.order_combo.get_value()
        clipping_type = 3 if click_points else self.ui.select_method_combo.get_value()
        margin = self.ui.rest_margin_entry.get_value() if rest else self.ui.margin_entry.get_value()
        has_offset = self.ui.rest_offset_choice_cb.get_value() if rest else False
        offset = self.ui.rest_offset_entry.get_value() if rest else 0.0
        milling_type = self.ui.milling_type_radio.get_value() if iso_tooldia_list else None
        prog_plot = self.app.options.get('tools_ncc_plotting', self.app.defaults.get(
            'tools_ncc_plotting', self.app.defaults.factory_defaults.get('tools_ncc_plotting'))) == 'progressive'
        mill_data = self._mill_data()
        storage = tools_storage if tools_storage is not None else self.ncc_tools
        # the job reads these copies: the table may be rebuilt while it waits or runs
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
        zones = distinct_zones(exclusion_areas or [])
        exclusion = unary_union(zones) if zones else None
        name = outname if outname is not None else self.obj_name + '_ncc'
        return self._ncc_spec(
            freeze_options(self.app), tools=self._tool_specs(tool_settings, diameters), rest=bool(rest),
            order=order, selection={0: 'all', 1: 'area', 2: 'reference', 3: 'click'}.get(clipping_type, 'all'),
            margin=margin, points=tuple(tuple(point) for point in click_points or ()) or None,
            simplification=tolerance, plot=True, outname=name, areas=copy_geometry(areas_to_clear),
            exclusion=exclusion, exclusion_zones=tuple(zones), source=SourceSnapshot.of(cleared_object),
            origin=cleared_object,
            reference_source=SourceSnapshot.of(sel_obj), storage=snapshot, tool_table=tool_settings,
            diameters=tuple(diameters), iso_diameters=tuple(iso_tooldia_list), milling_type=milling_type,
            has_offset=has_offset, offset=offset, check_validity=check_validity,
            prog_plot=prog_plot, mill_data=mill_data)

    def prepare_job(self, spec):
        """ClearingJob hook: NCC has no step before the output object exists."""
        return True, None

    def generate_output(self, spec, prepared, output, app_obj):
        """ClearingJob hook: fill the output object from the spec (one path for the GUI and for Tcl)."""
        result = self._generate_output(spec, output)
        if isinstance(result, str) and result == 'fail':
            # every failure of the generation reported itself with a message
            self._failure_reported = True
        return result

    def _generate_output(self, spec, output):
        cleared_object = spec.source
        rest = spec.rest
        name = spec.outname
        diameters = list(spec.diameters)
        tool_settings = NccModel.copy_tools(spec.tool_table)
        snapshot = NccModel.copy_tools(spec.storage)
        clipping_type = {'all': 0, 'area': 1, 'reference': 2, 'click': 1}[spec.selection]
        areas = copy_geometry(spec.areas)
        NccGen._process_events_and_check_abort(self)
        if spec.check_validity and self._tools_fit(cleared_object, diameters, list(spec.iso_diameters)):
            return 'fail'
        if spec.selection == 'click':
            # the faces come from the snapshot of the run, not from the live object
            areas = picked_faces(click_faces(cleared_object), spec.points)
            if not areas:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No area under the clicked points."))
                return 'fail'
        region, kind = self.compute_clipping_region(
            cleared_object=cleared_object, clipping_object=spec.reference_source,
            clipping_type=clipping_type, clipping_areas=areas)
        if region is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("NCC Tool failed creating bounding box."))
            return 'fail'
        boundary = self.apply_margin_to_clipping_region(
            clipping_region=region, box_kind=kind, clipping_type=clipping_type, margin=spec.margin)
        if boundary is None or isinstance(boundary, str):
            return 'fail'
        # This is plain staging data, never the registered output object.
        staged = {'tools': {}, 'milling_type': spec.milling_type}
        prepared = {}
        areas = {}
        warning = 0
        for diameter in diameters:
            NccGen._process_events_and_check_abort(self)
            uid = NccModel.tool_uid(tool_settings, diameter, self.decimals)
            if uid is None:
                continue
            data = tool_settings[uid]['data']
            use_offset = spec.has_offset if rest else data['tools_ncc_offset_choice']
            distance = spec.offset if rest else float(data['tools_ncc_offset_value'])
            key = (use_offset is True, distance if use_offset is True else 0.0)
            if key not in prepared:
                prepared[key] = self.compute_area_to_clear(
                    name=name, cleared_object=cleared_object, geo_obj=staged,
                    iso_tooldia=list(spec.iso_diameters), has_offset=use_offset, offset=distance,
                    margin=spec.margin, clipping_region=boundary, tools_storage=snapshot, exclusion=spec.exclusion)
            areas[uid], warning = prepared[key]
            if rest and isinstance(areas[uid], str):
                return 'fail'
        if areas and all(isinstance(area, str) for area in areas.values()):
            return 'fail'
        generated, domains, residual, failed, last = NccModel.generate_tools(
            'rest' if rest else 'normal', tool_settings, snapshot, diameters, areas,
            name, self.decimals, self.circle_steps, self.clear_polygon_worker,
            lambda: NccGen._process_events_and_check_abort(self),
            self.app.proc_container.update_view_text,
            simplify_tol=spec.simplification, prog_plot=spec.prog_plot, initial_tools=staged['tools'],
            clearing_error=clearing_error(self.app),
            tool_progress=lambda diameter: self._tool_progress(diameter, spec.units))
        if not rest:
            self._warn_unused_tools(tool_settings, diameters, generated)
        generated, cleanup_residual, cleanup_diameter = self._cleanup_result(
            snapshot, generated, domains, name, cleared_object)
        if spec.prog_plot:
            self.temp_shapes.clear(update=True)
        return self._publish_result(output, generated, name, '0.0' if rest else last,
                                    failed, cleanup_residual, warning, dict(spec.mill_data), last,
                                    cleanup_diameter)

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
            click_points: list[tuple] = None,
    ):
        """
        Resolve GUI input into a JobSpec, then run it as a ClearingJob.

        :param check_validity:      stop before the generation if a tool is too big for the smallest copper gap
        :param exclusion_areas:     polygons that are removed from the area to clear
        """
        # Keep circle accuracy and isolation staging local to this request, not the next GUI job.
        self = copy(self)
        self.app.log.debug("Executing the ncc_handler() ...")
        spec = self.resolve_gui_job(
            cleared_object, clear_tooldia_list, iso_tooldia_list, areas_to_clear, sel_obj, outname, tool_ordering,
            tools_storage, check_validity, exclusion_areas, click_points)
        if isinstance(spec, str):
            return spec
        return ClearingJob(self, NCC_GUI).run(spec, run_threaded)

    def _tcl_rows(self, diameters, iso_diameters, outname, plot, **values):
        """
        The tool rows of a Tcl job, built from its arguments: every row holds the options as defaults and the given
        values on top. The tool table of the plugin is never read.
        """
        base = dict(freeze_options(self.app))
        base.update(values)
        base.update(name=outname, plot=plot, seg_x=self._option('geometry_seg_x'),
                    seg_y=self._option('geometry_seg_y'))
        rows = {}
        for uid, (dia, operation) in enumerate(
                [(dia, 'clear') for dia in diameters] + [(dia, 'iso') for dia in iso_diameters], 1):
            data = dict(base)
            data.update(tooldia=round(dia, self.decimals), tools_ncc_operation=operation)
            rows[uid] = {'tooldia': round(dia, self.decimals), 'data': data, 'solid_geometry': []}
        return rows

    def resolve_tcl_job(
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
            order=None,
            method=None,
            rest=None,
            tools_storage=None,
            plot=True,
            final_cleanup=None,
            milling_type=None,
            simplification=None,
            exclusion_areas=None,
            angle=None,
            click_points=None,
            clear_milling=None,
    ):
        """
        The Tcl resolver: the arguments, with the tools_ncc_* options as fallback, into a JobSpec. It never reads a
        widget or the tool table of the plugin: the tool rows are built here from the arguments. tools_storage is
        kept for the old callers and ignored.

        :return: the JobSpec; 'fail' when the input is wrong
        """
        # the current Gerber / Geometry accuracy, not the one of the last GUI click
        self.circle_steps = self._circle_steps_for(ncc_obj.kind)
        margin = float(margin if margin is not None else self._option('tools_ncc_margin', 1.0))
        overlap = float(overlap if overlap is not None else float(self._option('tools_ncc_overlap', 40)) / 100)
        connect = bool(connect if connect is not None else self._option('tools_ncc_connect', False))
        contour = bool(contour if contour is not None else self._option('tools_ncc_contour', True))
        method = int(method if method is not None else self._option('tools_ncc_method', 0))
        if method == 4:
            angle = crosshatch_angle(angle if angle is not None
                                     else self._option('tools_ncc_crosshatch_angle', 0.0))
        else:
            angle = None
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
        clear_milling = str(clear_milling if clear_milling is not None
                            else self._option('tools_ncc_clear_milling', 'none')).lower()
        if clear_milling not in ('none', 'climb', 'conv'):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Direction must be none, climb or conv."))
            return 'fail'
        # the strict clearing edge never simplifies, also with an explicit Tcl value
        simplify_tol = clearing_simplification(
            self.app, float(simplification)) if simplification is not None else self._simplification()
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
        reference = None if sel_obj is None or sel_obj == 0 else sel_obj
        if select_method not in (0, 1, 2, 3) or (select_method == 1 and areas_to_clear_list is None) or (
                select_method == 2 and reference is None) or (select_method == 3 and not click_points):
            return 'fail'
        name = outname if outname is not None else self.obj_name + ('_ncc_rm' if rest else '_ncc')
        if milling_type is None and iso_tooldia:
            milling_type = self._option('tools_ncc_milling_type', 'cl')
        if not iso_tooldia:
            milling_type = None
        values = dict(
            tools_ncc_method=method, tools_ncc_overlap=round(overlap * 100, 9), tools_ncc_connect=connect,
            tools_ncc_contour=contour, tools_ncc_offset_choice=has_offset, tools_ncc_offset_value=offset,
            tools_ncc_final_cleanup=final_cleanup, tools_ncc_rest=rest, tools_ncc_margin=margin,
            tools_ncc_ref=select_method, tools_ncc_clear_milling=clear_milling)
        if angle is not None:
            values['tools_ncc_crosshatch_angle'] = angle
        rows = self._tcl_rows(diameters, list(iso_tooldia or ()), name, bool(plot), **values)
        # a script: the zones stored on the cleared object and the ones of its -exclude, for this run only
        zones = distinct_zones(object_zones(ncc_obj) + list(exclusion_areas or []))
        exclusion = unary_union(zones) if zones else None
        return self._ncc_spec(
            freeze_options(self.app), tools=self._tool_specs(rows, diameters), rest=rest, order=order,
            selection={0: 'all', 1: 'area', 2: 'reference', 3: 'click'}[select_method], margin=margin,
            points=tuple(tuple(point) for point in click_points or ()) or None, simplification=simplify_tol, plot=bool(plot), outname=name, areas=copy_geometry(areas_to_clear_list),
            exclusion=exclusion, exclusion_zones=tuple(zones), source=SourceSnapshot.of(ncc_obj), origin=ncc_obj,
            reference_source=SourceSnapshot.of(reference), storage=NccModel.copy_tools(rows), tool_table=rows,
            diameters=tuple(diameters), iso_diameters=tuple(iso_tooldia or ()), milling_type=milling_type,
            has_offset=has_offset, offset=offset, prog_plot=False,
            mill_data=self._mill_data())

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
            angle=None,
            click_points=None,
            clear_milling=None,
    ):
        """
        Tcl adapter. Synchronous calls finish object creation before returning.

        Every parameter that is None takes its tools_ncc_* value from the application options.
        overlap is a fraction (0.4), simplification is in the units of the geometry (the option is in millimetres),
        exclusion_areas is a list of polygons that are removed from the area to clear.
        """
        self = copy(self)
        spec = self.resolve_tcl_job(
            ncc_obj, sel_obj, areas_to_clear_list, ncc_tooldia, iso_tooldia, margin, has_offset, offset,
            select_method, outname, overlap, connect, contour, order, method, rest, tools_storage, plot,
            final_cleanup, milling_type, simplification, exclusion_areas, angle, click_points, clear_milling)
        if isinstance(spec, str):
            return spec
        return ClearingJob(self, NCC_TCL).run(spec, run_threaded)

    def _warn_unused_tools(self, tool_settings, diameters, generated):
        for diameter in diameters:
            uid = NccModel.tool_uid(tool_settings, diameter, self.decimals)
            if uid is not None and uid not in generated:
                self.app.inform.emit('[WARNING_NOTCL] %s %s: %s %s: %s' % (
                    _("Could not use the tool for copper clear."), _("Tool"), str(uid),
                    _("with diameter"), str(tool_settings[uid]['tooldia'])))

    def _compute_area_to_clear_handler(self, target, boundary=None):
        """Return the complement of target inside boundary, retaining the public Tcl seam."""
        ret_val = regions.complement(
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
