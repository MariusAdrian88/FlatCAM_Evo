# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Modified by: Marius Adrian Stanciu (c)              #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui  # noqa

from appPlugins.ToolNCC.NccUI import NccUI
from appGUI.GUIElements import (
    FCComboBox,
    FCCheckBox,
    RadioSet,
    FCDoubleSpinner,
    FCInputDialogSpinnerButton,
)

import logging
import math
from copy import copy, deepcopy

from shapely.ops import unary_union

import gettext
import appTranslation as fcTranslate
import builtins

from appPlugins.ToolNCC.NccGen import NccGen
from appDatabase import load_tools_database, tools_database_error_message
from defaults import AppDefaults
from camlib import Geometry, grace, flatten_shapely_geometry, object_circle_steps
from appCommon.SafeEval import safe_eval
from appCommon.ToolDatabase import matching_tools
from appPlugins.ToolNCC import NccModel
from appPlugins.Common.clearing.controller_base import ClearingToolBase
from appPlugins.Common.clearing.methods import NCC_METHODS

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def valid_tool_dia(value):
    """The tool diameter as a float when it is a finite number above zero, else None."""
    try:
        dia = float(value)
    except (TypeError, ValueError):
        return None
    return dia if math.isfinite(dia) and dia > 0 else None


def merge_db_tool_data(base, db_data):
    """Copy the NCC and milling parameters (and the non-tool ones) of a Tools Database record over base."""
    for key, value in db_data.items():
        if not key.startswith('tools_') or key.startswith(('tools_ncc_', 'tools_mill_')):
            base[key] = deepcopy(value)


class ToolNcc(Geometry, ClearingToolBase):

    key_prefix = 'tools_ncc'
    tools_attr = 'ncc_tools'
    tool_target = 5
    method_map = NCC_METHODS
    usage_name = "ToolNonCopperClear()"

    optimal_found_sig = QtCore.pyqtSignal(float)
    # (job token, result): the preview job hands the area to clear (or an error text) to the GUI thread
    preview_done_sig = QtCore.pyqtSignal(int, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        ClearingToolBase.__init__(self, app)
        # no fixed steps: the circles follow the preferences "Circles" of their kind (NccGen._circle_steps_for())
        Geometry.__init__(self, app=app)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = NccUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        self.init_context_menu()

        # #############################################################################
        # ########################## VARIABLES ########################################
        # #############################################################################
        self.units = ''
        self.ncc_tools = {}
        self.tooluid = 0

        # store here the default data for Geometry Data
        self.default_data = {}

        self.obj_name = ""
        self.ncc_obj = None

        self.bound_obj_name = ""
        self.bound_obj = None

        self.has_offset = None
        self.o_name = None
        self.overlap = None
        self.connect = None
        self.contour = None
        self.rest = None

        # store here the tool diameter that is guaranteed to isolate the object
        self.safe_tooldia = None

        self.ncc_dia_list = []
        self.iso_dia_list = []

        # the faces of the last Polygon Selection: the Preview of that mode uses them
        self.last_click_faces = []

        # store here solid_geometry when there are tool with isolation job
        self.solid_geometry = []

        self.select_method = None
        self.tool_type_item_options = []

        # the steps of the job start from the Gerber circles of a 1 mm radius; NccGen sets them for the cleared object
        self.circle_steps = object_circle_steps(self.app.options, 'gerber', 1.0 if self.app.app_units.upper() == 'MM'
                                                else 1.0 / 25.4, self.app.app_units)

        self.tooldia = None

        self.form_fields = {
            "tools_ncc_operation":      self.ui.op_radio,
            "tools_ncc_overlap":        self.ui.overlap_entry,
            "tools_ncc_margin":         self.ui.margin_entry,
            "tools_ncc_method":         self.ui.method_combo,
            "tools_ncc_crosshatch_angle": self.ui.crosshatch_angle_entry,
            "tools_ncc_connect":        self.ui.connect_cb,
            "tools_ncc_contour":        self.ui.contour_cb,
            "tools_ncc_clear_milling":  self.ui.clear_dir_radio,
            "tools_ncc_final_cleanup":  self.ui.final_cleanup_cb,
            "tools_ncc_offset_choice":  self.ui.offset_choice_cb,
            "tools_ncc_offset_value":   self.ui.offset_entry,
            "tools_ncc_milling_type":   self.ui.milling_type_radio,
            "tools_ncc_check_valid":    self.ui.valid_cb
        }

        self.name2option = {
            "n_operation":      "tools_ncc_operation",
            "n_overlap":        "tools_ncc_overlap",
            "n_margin":         "tools_ncc_margin",
            "n_method":         "tools_ncc_method",
            "n_crosshatch_angle": "tools_ncc_crosshatch_angle",
            "n_connect":        "tools_ncc_connect",
            "n_contour":        "tools_ncc_contour",
            "n_clear_milling":  "tools_ncc_clear_milling",
            "n_final_cleanup":  "tools_ncc_final_cleanup",
            "n_offset":         "tools_ncc_offset_choice",
            "n_offset_value":   "tools_ncc_offset_value",
            "n_milling_type":   "tools_ncc_milling_type",
            "n_check":          "tools_ncc_check_valid",
        }

        self.old_tool_dia = None

        self.gen: NccGen | None = None

    def install(self, icon=None, separator=None, **kwargs):
        ClearingToolBase.install(self, icon, separator, shortcut='Alt+N', **kwargs)

    def tab_title(self):
        return _("NCC")

    def generate_slot(self):
        return self.gen.on_ncc_click

    def type_obj_slot(self):
        return self.on_type_obj_index_changed

    def multi_objects_widget(self):
        return self.ui.multi_cb

    def reset_source(self):
        # reset those objects on a new run
        self.ncc_obj = None
        self.bound_obj = None
        self.obj_name = ''
        self.bound_obj_name = ''

    def extra_signals(self):
        ui = self.ui
        return [
            (ui.find_optimal_button.clicked, self.on_find_optimal_tooldia),
            (self.optimal_found_sig, self.on_optimal_found),
            (ui.op_radio.activated_custom, self.on_operation_change),
            (ui.rest_cb.stateChanged, ui.on_rest_machining_check),
            (ui.rest_connect_cb.clicked, self.on_rest_connect_contour_clicked),
            (ui.rest_contour_cb.clicked, self.on_rest_connect_contour_clicked),
            (ui.order_combo.currentIndexChanged, self.on_order_changed),
        ]

    def on_optimal_found(self, val):
        self.ui.new_tooldia_entry.set_value(float(val))

    # ------------------------------------------------------------------------------------------------------------
    # the canvas selection
    # ------------------------------------------------------------------------------------------------------------
    def start_click_selection(self, faces):
        """
        Polygon Selection: a left click adds / removes the copper-free face under the cursor, a right click or Enter
        runs the clearing of the picked faces, Escape cancels. The panel is disabled until then.
        """
        self.selector.start('click', commit=self.on_faces_picked, faces=faces)

    def on_faces_picked(self, points, faces):
        self.last_click_faces = faces
        self.gen.clear_ncc_click_option(points)

    def on_zones_selected(self, zones):
        """The zones of the Area Selection: the Preview of that mode draws them; the source may be gone."""
        self.last_zones = zones

        # the source object may have been deleted while the zones were drawn
        ncc_obj = self.gen.ncc_obj
        try:
            found = self.app.collection.get_by_name(ncc_obj.obj_options['name'])
        except Exception:
            found = None
        if found is None or found is not ncc_obj:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The object to be processed is no longer available."))
            return

        self.gen.clear_ncc_area_selection_option(zones)

    def on_ncc_tool_add_from_db_executed(self, tool):
        return self.on_tool_add_from_db_executed(tool)

    def on_ncc_tool_add_from_db_clicked(self):
        return self.on_tool_add_from_db_clicked()

    def exclusion_areas(self):
        """The exclusion zones to remove from the area to clear, or None when they are off or there are none."""
        try:
            use = bool(self.ui.excl_cb.get_value())
        except RuntimeError:
            return None
        return list(self.exclusion_list) if use and self.exclusion_list else None

    # ------------------------------------------------------------------------------------------------------------
    # the preview of the area to clear
    # ------------------------------------------------------------------------------------------------------------
    def on_preview(self):
        """Compute the area that would be cleared (in the worker) and draw it on the canvas."""
        gen = self.gen
        self.clear_preview()
        if not gen.read_selected_diameters():
            return
        obj_name = self.ui.obj_combo.currentText()
        try:
            gen.ncc_obj = self.app.collection.get_by_name(obj_name)
        except Exception:
            gen.ncc_obj = None
        if gen.ncc_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(obj_name)))
            return
        gen.obj_name = obj_name

        clipping_type = self.ui.select_method_combo.get_value()
        gen.select_method = clipping_type
        sources = gen.source_objects()
        if sources is None:
            return
        reference = None
        zones = None
        click = clipping_type == 3
        if click:
            clipping_type = 1       # the faces of the last Polygon Selection are zones
        if clipping_type == 1:
            zones = list(self.last_click_faces if click else self.last_zones)
            if not zones:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "No area is drawn yet. Click Generate to draw the zones of the Area Selection, "
                    "then use Preview."))
                return
        elif clipping_type == 2:
            ref_name = self.ui.reference_combo.currentText()
            try:
                reference = self.app.collection.get_by_name(ref_name)
            except Exception:
                reference = None
            if reference is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(ref_name)))
                return

        rest = self.ui.rest_cb.get_value()
        tools = NccModel.copy_tools(self.ncc_tools)
        if rest:
            offsets = {(bool(self.ui.rest_offset_choice_cb.get_value()), float(self.ui.rest_offset_entry.get_value()))}
            margin = self.ui.rest_margin_entry.get_value()
        else:
            # every selected tool may have its own offset: the preview shows the union of the areas
            offsets = set()
            for diameter in gen.ncc_dia_list:
                uid = NccModel.tool_uid(tools, diameter, self.decimals)
                if uid is not None:
                    data = tools[uid]['data']
                    use_offset = bool(data['tools_ncc_offset_choice'])
                    offsets.add((use_offset, float(data['tools_ncc_offset_value']) if use_offset else 0.0))
            margin = self.ui.margin_entry.get_value()
        if not offsets:
            offsets = {(False, 0.0)}

        params = dict(
            sources=[(source, gen._circle_steps_for(getattr(source, 'kind', None))) for source in sources],
            clipping_type=clipping_type, reference=reference, zones=zones, margin=margin,
            iso=list(gen.iso_dia_list), offsets=sorted(offsets), tools=tools, exclusion=self.exclusion_areas(),
            milling_type=self.ui.milling_type_radio.get_value() if gen.iso_dia_list else None,
            token=self._preview_token)
        self.app.inform.emit(_("Computing the area to clear ..."))
        self.app.worker_task.emit({'fcn': self.preview_job, 'params': [params]})

    def preview_job(self, params):
        """Worker: the area to clear for each source and offset, merged. No toolpaths, no Qt widgets."""
        area, error = None, ''
        try:
            with self.app.proc_container.new('%s...' % _("Preview")):
                gen = copy(self.gen)
                exclusion = unary_union(params['exclusion']) if params['exclusion'] else None
                areas = []
                failed = False
                for source, circle_steps in params['sources']:
                    gen.circle_steps = circle_steps
                    for has_offset, offset in params['offsets']:
                        # compute_clear_area changes its tools: every pass gets its own copy of the table
                        result, _iso, _broken = gen.compute_clear_area(
                            source, clipping_type=params['clipping_type'], clipping_object=params['reference'],
                            clipping_areas=params['zones'], margin=params['margin'], iso_tooldia=params['iso'],
                            has_offset=has_offset, offset=offset, milling_type=params['milling_type'],
                            tools_storage=NccModel.copy_tools(params['tools']), exclusion=exclusion)
                        if isinstance(result, str):
                            # the reason was reported by compute_clear_area
                            failed = True
                            break
                        areas.append(unary_union(flatten_shapely_geometry(result)))
                    if failed:
                        break
                if not failed:
                    area = unary_union(areas)
        except grace:
            error = _("Aborted. The preview was not computed.")
        except Exception as e:
            self.app.log.error("ToolNCC.preview_job() --> %s" % str(e))
            error = _("The preview failed.")
        self.preview_done_sig.emit(params['token'], (area, error))

    def on_preview_done(self, token, result):
        """GUI thread: draw the area, unless the preview was cleared while the job was running."""
        if token != self._preview_token:
            return
        area, error = result
        if error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            return
        if area is None:
            return
        polygons = [p for p in flatten_shapely_geometry(area) if not p.is_empty]
        if not polygons:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The area to clear is empty."))
            return
        store = self._shape_store('_preview_shapes')
        if store is None:
            return
        store.clear(update=False)
        for poly in polygons:
            store.add(poly, color='#00AA00FF', face_color='#00AA0055', update=False, layer=0, tolerance=None)
        store.redraw()
        self._preview_drawn = True
        self.app.inform.emit('[success] %s' % _("Preview of the area to clear."))

    def set_tool_ui(self):
        # a Reset or an app cleanup must not leave the canvas handlers of a selection connected; it drops the preview
        # and the exclusion zones
        self.reset_drawn_regions()

        self.units = self.app.app_units.upper()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.old_tool_dia = self.app.options.get(
            "tools_ncc_newdia",
            app_defaults.get("tools_ncc_newdia", AppDefaults.factory_defaults.get("tools_ncc_newdia")))

        self.clear_ui(self.layout)
        self.ui = NccUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        self.clear_context_menu()
        self.init_context_menu()

        self.form_fields = {
            "tools_ncc_operation":      self.ui.op_radio,
            "tools_ncc_overlap":        self.ui.overlap_entry,
            "tools_ncc_margin":         self.ui.margin_entry,
            "tools_ncc_method":         self.ui.method_combo,
            "tools_ncc_crosshatch_angle": self.ui.crosshatch_angle_entry,
            "tools_ncc_connect":        self.ui.connect_cb,
            "tools_ncc_contour":        self.ui.contour_cb,
            "tools_ncc_clear_milling":  self.ui.clear_dir_radio,
            "tools_ncc_final_cleanup":  self.ui.final_cleanup_cb,
            "tools_ncc_offset_choice":  self.ui.offset_choice_cb,
            "tools_ncc_offset_value":   self.ui.offset_entry,
            "tools_ncc_milling_type":   self.ui.milling_type_radio,
            "tools_ncc_check_valid":    self.ui.valid_cb
        }

        # reset the value to prepare for another isolation
        self.safe_tooldia = None

        self.ui.tools_frame.show()

        # use the current selected object and make it visible in the NCC object combobox
        sel_list = self.app.collection.get_selected()
        if len(sel_list) == 1:
            active = self.app.collection.get_active()
            kind = active.kind
            if kind == 'gerber':
                self.ui.type_obj_radio.set_value('gerber')
            else:
                self.ui.type_obj_radio.set_value('geometry')

            # run those once so the obj_type attribute is updated for the FCComboboxes
            # so the last loaded object is displayed
            self.on_type_obj_index_changed(val=kind)
            self.on_reference_combo_changed()

            self.ui.obj_combo.set_value(active.obj_options['name'])
        else:
            kind = 'gerber'
            self.ui.type_obj_radio.set_value('gerber')

            # run those once so the obj_type attribute is updated for the FCComboboxes
            # so the last loaded object is displayed
            self.on_type_obj_index_changed(val=kind)
            self.on_reference_combo_changed()

        self.ui.op_radio.set_value(self.app.options.get(
            "tools_ncc_operation",
            app_defaults.get("tools_ncc_operation", AppDefaults.factory_defaults.get("tools_ncc_operation"))))
        self.ui.order_combo.set_value(self.app.options.get(
            "tools_ncc_order",
            app_defaults.get("tools_ncc_order", AppDefaults.factory_defaults.get("tools_ncc_order"))))
        self.ui.overlap_entry.set_value(self.app.options.get(
            "tools_ncc_overlap",
            app_defaults.get("tools_ncc_overlap", AppDefaults.factory_defaults.get("tools_ncc_overlap"))))
        self.ui.margin_entry.set_value(self.app.options.get(
            "tools_ncc_margin",
            app_defaults.get("tools_ncc_margin", AppDefaults.factory_defaults.get("tools_ncc_margin"))))
        self.ui.method_combo.set_value(self.app.options.get(
            "tools_ncc_method",
            app_defaults.get("tools_ncc_method", AppDefaults.factory_defaults.get("tools_ncc_method"))))
        self.ui.crosshatch_angle_entry.set_value(self.app.options.get(
            "tools_ncc_crosshatch_angle",
            app_defaults.get("tools_ncc_crosshatch_angle",
                             AppDefaults.factory_defaults.get("tools_ncc_crosshatch_angle", 0.0))))
        self.ui.on_method_changed()
        self.ui.connect_cb.set_value(self.app.options.get(
            "tools_ncc_connect",
            app_defaults.get("tools_ncc_connect", AppDefaults.factory_defaults.get("tools_ncc_connect"))))
        self.ui.contour_cb.set_value(self.app.options.get(
            "tools_ncc_contour",
            app_defaults.get("tools_ncc_contour", AppDefaults.factory_defaults.get("tools_ncc_contour"))))
        self.ui.clear_dir_radio.set_value(self.app.options.get(
            "tools_ncc_clear_milling", app_defaults.get("tools_ncc_clear_milling", "none")))
        self.ui.final_cleanup_cb.set_value(self.app.options.get(
            "tools_ncc_final_cleanup", app_defaults.get("tools_ncc_final_cleanup", False)))
        self.ui.offset_choice_cb.set_value(self.app.options.get(
            "tools_ncc_offset_choice",
            app_defaults.get("tools_ncc_offset_choice", AppDefaults.factory_defaults.get("tools_ncc_offset_choice"))))
        self.ui.offset_entry.set_value(self.app.options.get(
            "tools_ncc_offset_value",
            app_defaults.get("tools_ncc_offset_value", AppDefaults.factory_defaults.get("tools_ncc_offset_value"))))

        self.ui.rest_cb.set_value(self.app.options.get(
            "tools_ncc_rest",
            app_defaults.get("tools_ncc_rest", AppDefaults.factory_defaults.get("tools_ncc_rest"))))
        self.ui.on_rest_machining_check(state=self.app.options.get(
            "tools_ncc_rest",
            app_defaults.get("tools_ncc_rest", AppDefaults.factory_defaults.get("tools_ncc_rest"))))

        self.ui.rest_margin_entry.set_value(self.app.options.get(
            "tools_ncc_margin",
            app_defaults.get("tools_ncc_margin", AppDefaults.factory_defaults.get("tools_ncc_margin"))))
        self.ui.rest_connect_cb.set_value(self.app.options.get(
            "tools_ncc_connect",
            app_defaults.get("tools_ncc_connect", AppDefaults.factory_defaults.get("tools_ncc_connect"))))
        self.ui.rest_contour_cb.set_value(self.app.options.get(
            "tools_ncc_contour",
            app_defaults.get("tools_ncc_contour", AppDefaults.factory_defaults.get("tools_ncc_contour"))))
        self.ui.rest_offset_choice_cb.set_value(self.app.options.get(
            "tools_ncc_offset_choice",
            app_defaults.get("tools_ncc_offset_choice", AppDefaults.factory_defaults.get("tools_ncc_offset_choice"))))
        self.ui.rest_offset_entry.set_value(self.app.options.get(
            "tools_ncc_offset_value",
            app_defaults.get("tools_ncc_offset_value", AppDefaults.factory_defaults.get("tools_ncc_offset_value"))))

        self.ui.select_method_combo.set_value(self.app.options.get(
            "tools_ncc_ref",
            app_defaults.get("tools_ncc_ref", AppDefaults.factory_defaults.get("tools_ncc_ref"))))
        self.ui.area_shape_radio.set_value(self.app.options.get(
            "tools_ncc_area_shape",
            app_defaults.get("tools_ncc_area_shape", AppDefaults.factory_defaults.get("tools_ncc_area_shape"))))
        self.ui.excl_shape_radio.set_value(self.ui.area_shape_radio.get_value())
        # the preference tools_ncc_multi_objects is False until the preferences group has it
        self.ui.multi_cb.set_value(bool(self.app.options.get(
            "tools_ncc_multi_objects", app_defaults.get("tools_ncc_multi_objects", False))))
        self.ui.valid_cb.set_value(self.app.options.get(
            "tools_ncc_check_valid",
            app_defaults.get("tools_ncc_check_valid", AppDefaults.factory_defaults.get("tools_ncc_check_valid"))))

        self.ui.milling_type_radio.set_value(self.app.options.get(
            "tools_ncc_milling_type",
            app_defaults.get("tools_ncc_milling_type", AppDefaults.factory_defaults.get("tools_ncc_milling_type"))))

        self.ui.new_tooldia_entry.set_value(self.app.options.get(
            "tools_ncc_newdia",
            app_defaults.get("tools_ncc_newdia", AppDefaults.factory_defaults.get("tools_ncc_newdia"))))

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level", app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        # init the working variables
        self.default_data.clear()
        self.default_data['tools_ncc_final_cleanup'] = self.ui.final_cleanup_cb.get_value()
        self.default_data['tools_ncc_clear_milling'] = self.ui.clear_dir_radio.get_value()
        kind = 'geometry'
        for option in self.app.options:
            if option.find(kind + "_") == 0:
                oname = option[len(kind) + 1:]
                self.default_data[oname] = self.app.options.get(
                    option, app_defaults.get(option, AppDefaults.factory_defaults.get(option)))

            if option.find('tools_') == 0:
                self.default_data[option] = self.app.options.get(
                    option, app_defaults.get(option, AppDefaults.factory_defaults.get(option)))

        tools_ncc_tools = self.app.options.get(
            "tools_ncc_tools", app_defaults.get("tools_ncc_tools", AppDefaults.factory_defaults.get("tools_ncc_tools")))
        try:
            dias = [float(tools_ncc_tools)]
        except (ValueError, TypeError):
            try:
                dias = [float(safe_eval(dia)) for dia in tools_ncc_tools.split(",") if dia != '']
            except AttributeError:
                dias = tools_ncc_tools
        except Exception:
            dias = []

        self.tooluid = 0

        # the shape combo boxes of the table are filled from this
        self.tool_type_item_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]

        self.ncc_tools.clear()
        self.add_default_tools(dias)

        self.obj_name = ""
        self.ncc_obj = None
        self.bound_obj_name = ""
        self.bound_obj = None

        self.units = self.app.app_units.upper()

        prog_plot = True if self.app.options.get(
            "tools_ncc_plotting",
            app_defaults.get("tools_ncc_plotting", AppDefaults.factory_defaults.get("tools_ncc_plotting"))) == 'progressive' else False
        if prog_plot:
            self.temp_shapes.clear(update=True)

        self.ui.tools_table.drag_drop_sig.connect(self.rebuild_ui)

        # the operation was set before the signals were connected, so the enabled state of the fields needs a refresh
        self.ui.parameters_ui(val=self.ui.op_radio.get_value())

        self.gen = NccGen(self)
        self.connect_signals()

    def add_default_tools(self, dias):
        """
        Fill the Tool Table with the default tools: one read of the Tools Database, one rebuild of the table.

        :param dias:    the tools diameters
        :return:
        """
        records = {}
        try:
            records = load_tools_database(self.app.tools_database_path(), self.app.options, target=5,
                                          units=getattr(self.app, 'app_units', None))
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % tools_database_error_message(error))

        for dia in dias:
            tool_dia = valid_tool_dia(dia)
            if tool_dia is None:
                continue
            truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)
            new_tdia = truncated_tooldia
            new_data = deepcopy(self.default_data)

            matches = matching_tools(records, target=5, diameter=truncated_tooldia) if records else {}
            if len(matches) == 1:
                db_tool = next(iter(matches.values()))
                new_tdia = self.app.dec_format(db_tool['tooldia'], self.decimals)
                merge_db_tool_data(new_data, db_tool['data'])

            if new_tdia in [self.app.dec_format(v['tooldia'], self.decimals) for v in self.ncc_tools.values()]:
                continue
            uid = max([int(k) for k in self.ncc_tools], default=0) + 1
            self.ncc_tools[uid] = {'tooldia': new_tdia, 'data': new_data, 'solid_geometry': []}

        self.build_ui()
        if self.ui.tools_table.rowCount():
            self.ui.tools_table.selectRow(self.ui.tools_table.rowCount() - 1)
            self.update_ui()

    def on_level_changed(self, checked):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)

            # Add Tool section
            self.ui.add_tool_frame.hide()

            # Tool parameters section
            if self.ncc_tools:
                for tool in self.ncc_tools:
                    tool_data = self.ncc_tools[tool]['data']

                    tool_data['tools_ncc_operation'] = "clear"
                    tool_data['tools_ncc_milling_type'] = "cl"

                    tool_data['tools_ncc_offset_choice'] = False
                    tool_data['tools_ncc_offset_value'] = 0.0
                    tool_data['tools_ncc_rest'] = False

            self.ui.op_label.hide()
            self.ui.op_radio.hide()
            self.ui.milling_type_label.hide()
            self.ui.milling_type_radio.hide()
            self.ui.offset_choice_cb.hide()
            self.ui.offset_entry.hide()

            # the control is hidden, so rest machining must not stay on without a visible way to turn it off
            self.ui.rest_cb.set_value(False)
            self.ui.rest_cb.hide()

            # All param section
            self.ui.apply_param_to_all.hide()

            # Context Menu section
            self.ui.tools_table.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            # Add Tool section
            self.ui.add_tool_frame.show()

            # Tool parameters section
            if self.ncc_tools:
                for tool in self.ncc_tools:
                    tool_data = self.ncc_tools[tool]['data']

                    # only fill what is missing; the per-tool values the user edited stay
                    for key in ("tools_ncc_operation", "tools_ncc_milling_type", "tools_ncc_offset_choice",
                                "tools_ncc_offset_value", "tools_ncc_rest"):
                        if key not in tool_data:
                            tool_data[key] = self.app.options.get(
                                key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

            self.ui.op_label.show()
            self.ui.op_radio.show()
            self.ui.milling_type_label.show()
            self.ui.milling_type_radio.show()
            self.ui.offset_choice_cb.show()
            self.ui.offset_entry.show()

            self.ui.rest_cb.show()

            # All param section
            self.ui.apply_param_to_all.show()

            # Context Menu section
            self.ui.tools_table.setupContextMenu()

    def on_type_obj_index_changed(self, val):
        obj_type = 0 if val == 'gerber' else 2
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.obj_combo.setCurrentIndex(0)
        self.ui.obj_combo.obj_type = {
            "gerber": "Gerber", "geometry": "Geometry"
        }[self.ui.type_obj_radio.get_value()]

    def on_operation_change(self, val):
        self.ui.parameters_ui(val=val)

        current_row = self.ui.tools_table.currentRow()
        try:
            current_uid = int(self.ui.tools_table.item(current_row, 3).text())
            self.ncc_tools[current_uid]['data']['tools_ncc_operation'] = val
            # TODO got a crash here, a KeyError exception; need to see it again and find out the why
        except AttributeError:
            return

    def storage_to_form(self, dict_storage):
        dict_storage.setdefault('tools_ncc_final_cleanup', False)
        dict_storage.setdefault('tools_ncc_clear_milling', 'none')
        dict_storage.setdefault('tools_ncc_crosshatch_angle', 0.0)
        for form_key in self.form_fields:
            for storage_key in dict_storage:
                if form_key == storage_key:
                    try:
                        self.form_fields[form_key].set_value(dict_storage[form_key])
                    except Exception as e:
                        self.app.log.error("ToolNcc.storage_to_form() --> %s" % str(e))
                        pass
        self.ui.on_method_changed()

    def on_rest_connect_contour_clicked(self):
        """The rest Connect and Contour widgets apply to all rows: a rest run reads Connect and Contour per row."""
        widget = self.sender()
        key = 'tools_ncc_connect' if widget is self.ui.rest_connect_cb else 'tools_ncc_contour'
        value = bool(widget.get_value())
        for tool in self.ncc_tools.values():
            tool['data'][key] = value
        if len(set(index.row() for index in self.ui.tools_table.selectedIndexes())) == 1:
            self.update_ui()

    def on_apply_param_to_all_clicked(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table, so we can't save the GUI elements values to storage
            self.app.log.debug("ToolNcc.on_apply_param_to_all_clicked() --> no tool in Tools Table, aborting.")
            return

        self.blockSignals(True)

        row = self.ui.tools_table.currentRow()
        if row < 0:
            row = 0

        tooluid_item = int(self.ui.tools_table.item(row, 3).text())
        temp_tool_data = {}

        for tooluid_key, tooluid_val in self.ncc_tools.items():
            if int(tooluid_key) == tooluid_item:
                # this will hold the 'data' key of the self.tools[tool] dictionary that corresponds to
                # the current row in the tool table
                temp_tool_data = tooluid_val['data']
                break

        for tooluid_key, tooluid_val in self.ncc_tools.items():
            tooluid_val['data'] = deepcopy(temp_tool_data)

        # store all the data associated with the row parameter to the self.tools storage
        # tooldia_item = float(self.ui.tools_table.item(row, 1).text())
        # type_item = self.ui.tools_table.cellWidget(row, 2).currentText()
        # operation_type_item = self.ui.tools_table.cellWidget(row, 4).currentText()
        #
        # nccoffset_item = self.ui.offset_choice_cb.get_value()
        # nccoffset_value_item = float(self.ui.offset_entry.get_value())

        # this new dict will hold the actual useful data, another dict that is the value of key 'data'
        # temp_tools = {}
        # temp_dia = {}
        # temp_data = {}
        #
        # for tooluid_key, tooluid_value in self.ncc_tools.items():
        #     for key, value in tooluid_value.items():
        #         if key == 'data':
        #             # update the 'data' section
        #             for data_key in tooluid_value[key].keys():
        #                 for form_key, form_value in self.form_fields.items():
        #                     if form_key == data_key:
        #                         temp_data[data_key] = form_value.get_value()
        #                 # make sure we make a copy of the keys not in the form (we may use 'data' keys that are
        #                 # updated from self.app.options
        #                 if data_key not in self.form_fields:
        #                     temp_data[data_key] = value[data_key]
        #             temp_dia[key] = deepcopy(temp_data)
        #             temp_data.clear()
        #
        #         elif key == 'solid_geometry':
        #             temp_dia[key] = deepcopy(self.tools[tooluid_key]['solid_geometry'])
        #         else:
        #             temp_dia[key] = deepcopy(value)
        #
        #         temp_tools[tooluid_key] = deepcopy(temp_dia)
        #
        # self.ncc_tools.clear()
        # self.ncc_tools = deepcopy(temp_tools)
        # temp_tools.clear()

        self.app.inform.emit('[success] %s' % _("Current Tool parameters were applied to all tools."))

        self.blockSignals(False)

    def build_ui(self):
        self.ui_disconnect()

        # updated units
        self.units = self.app.app_units.upper()

        # the rows are keyed by the tool uid: two tools can have the same diameter
        sorted_tools = list(self.ncc_tools.items())

        order = self.ui.order_combo.get_value()
        if order == 1:  # "Forward"
            sorted_tools.sort(key=lambda item: round(float(item[1]['tooldia']), self.decimals), reverse=False)
        elif order == 2:    # "Reverse"
            sorted_tools.sort(key=lambda item: round(float(item[1]['tooldia']), self.decimals), reverse=True)
        else:
            pass

        n = len(sorted_tools)
        self.ui.tools_table.setRowCount(n)
        tool_id = 0

        selected_enabled_flag = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled  # noqa
        enabled_flag = QtCore.Qt.ItemFlag.ItemIsEnabled
        selected_enabled_editable_flag = (
                QtCore.Qt.ItemFlag.ItemIsEditable
                | QtCore.Qt.ItemFlag.ItemIsSelectable   # noqa
                | QtCore.Qt.ItemFlag.ItemIsEnabled
        )

        for tooluid_key, tooluid_value in sorted_tools:
            tool_id += 1

            # ------------------------ Tool ID ----------------------------------------------------------------
            id_ = QtWidgets.QTableWidgetItem('%d' % int(tool_id))
            id_.setFlags(selected_enabled_flag)
            row_no = tool_id - 1
            self.ui.tools_table.setItem(row_no, 0, id_)  # Tool name/id

            # ------------------------ Tool Diameter ----------------------------------------------------------
            # Make sure that the drill diameter when in MM is with no more than self.decimals decimals
            dia = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals, tooluid_value['tooldia']))
            dia.setFlags(enabled_flag)
            self.ui.tools_table.setItem(row_no, 1, dia)  # Diameter

            # ------------------------ Tool Shape -------------------------------------------------------------
            tool_type_item = FCComboBox()
            tool_type_item.addItems(self.tool_type_item_options)
            idx = int(tooluid_value['data'].get('tools_mill_tool_shape', 0))
            tool_type_item.setCurrentIndex(idx)
            self.ui.tools_table.setCellWidget(row_no, 2, tool_type_item)

            # ------------------------ Tool UID - NOT Visible -------------------------------------------------
            tool_uid_item = QtWidgets.QTableWidgetItem(str(int(tooluid_key)))
            # ## REMEMBER: THIS COLUMN IS HIDDEN IN OBJECTUI.PY # ##
            self.ui.tools_table.setItem(row_no, 3, tool_uid_item)  # Tool unique ID

        # make the diameter column editable
        for row in range(tool_id):
            self.ui.tools_table.item(row, 1).setFlags(selected_enabled_editable_flag)

        self.ui.tools_table.resizeColumnsToContents()
        self.ui.tools_table.resizeRowsToContents()

        vertical_header = self.ui.tools_table.verticalHeader()
        vertical_header.hide()
        self.ui.tools_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.tools_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.ui.tools_table.setMinimumHeight(self.ui.tools_table.getHeight())
        self.ui.tools_table.setMaximumHeight(self.ui.tools_table.getHeight())

        self.ui_connect()

        # set the text on tool_data_label after loading the object
        sel_rows = set()
        sel_items = self.ui.tools_table.selectedItems()
        for it in sel_items:
            sel_rows.add(it.row())
        if len(sel_rows) > 1:
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
            )

    def ui_connect(self):
        self.ui.tools_table.itemChanged.connect(self.on_tool_edit)

        # rows selected
        self.ui.tools_table.selectionModel().selectionChanged.connect(self.on_row_selection_change)
        self.ui.tools_table.horizontalHeader().sectionClicked.connect(self.on_toggle_all_rows)

        for row in range(self.ui.tools_table.rowCount()):
            try:
                self.ui.tools_table.cellWidget(row, 2).currentIndexChanged.connect(self.on_tooltable_cellwidget_change)
            except AttributeError:
                pass

        for opt in self.form_fields:
            current_widget = self.form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                current_widget.stateChanged.connect(self.form_to_storage)
            if isinstance(current_widget, RadioSet):
                current_widget.activated_custom.connect(self.form_to_storage)
            elif isinstance(current_widget, FCDoubleSpinner):
                current_widget.returnPressed.connect(self.form_to_storage)
            elif isinstance(current_widget, FCComboBox):
                current_widget.currentIndexChanged.connect(self.form_to_storage)

        self.ui.rest_cb.stateChanged.connect(self.ui.on_rest_machining_check)
        self.ui.order_combo.currentIndexChanged.connect(self.on_order_changed)

    def ui_disconnect(self):

        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.ui.tools_table.itemChanged.disconnect()
        except (TypeError, AttributeError):
            pass

        for row in range(self.ui.tools_table.rowCount()):

            try:
                self.ui.tools_table.cellWidget(row, 2).currentIndexChanged.disconnect()
            except (TypeError, AttributeError):
                pass

        for opt in self.form_fields:
            current_widget = self.form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                try:
                    current_widget.stateChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            if isinstance(current_widget, RadioSet):
                try:
                    current_widget.activated_custom.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget, FCDoubleSpinner):
                try:
                    current_widget.returnPressed.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget, FCComboBox):
                try:
                    current_widget.currentIndexChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass

        try:
            self.ui.rest_cb.stateChanged.disconnect(self.ui.on_rest_machining_check)
        except (TypeError, ValueError):
            pass
        try:
            self.ui.order_combo.currentIndexChanged.disconnect(self.on_order_changed)
        except (TypeError, ValueError):
            pass

        # rows selected
        try:
            self.ui.tools_table.selectionModel().selectionChanged.disconnect(self.on_row_selection_change)
        except (TypeError, RuntimeError, AttributeError):
            pass
        try:
            self.ui.tools_table.horizontalHeader().sectionClicked.disconnect()
        except (TypeError, AttributeError):
            pass

    def on_reference_combo_changed(self):
        obj_type = self.ui.reference_type_combo.currentIndex()
        self.ui.reference_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.reference_combo.setCurrentIndex(0)
        self.ui.reference_combo.obj_type = {0: "Gerber", 1: "Excellon", 2: "Geometry"}[obj_type]

    def on_order_changed(self, order):
        if order != 0:  # "Default"
            self.build_ui()

    def on_tooltable_cellwidget_change(self):
        cw = self.sender()
        assert isinstance(cw, QtWidgets.QComboBox),\
            "Expected a QtWidgets.QComboBox, got %s" % isinstance(cw, QtWidgets.QComboBox)

        # the shape combo box is always in the column 2
        cw_row = next((row for row in range(self.ui.tools_table.rowCount())
                       if self.ui.tools_table.cellWidget(row, 2) is cw), -1)
        cw_col = 2
        if cw_row < 0:
            return

        current_uid = int(self.ui.tools_table.item(cw_row, 3).text())

        # if the sender is in the column with index 2 then we update the tool_type key
        if cw_col == 2:
            tt = cw.currentText()
            typ = 'Iso' if tt == 'V' else 'Rough'

            self.ncc_tools[current_uid].update({
                'type': typ,
                'tool_type': tt,
            })
            # build_ui() shows the shape from here
            self.ncc_tools[current_uid]['data']['tools_mill_tool_shape'] = cw.currentIndex()

    def on_find_optimal_tooldia(self):
        self.find_safe_tooldia_worker()

    def find_safe_tooldia_worker(self):
        self.safe_tooldia = None
        self.app.inform.emit(_("Checking tools for validity."))
        self.units = self.app.app_units.upper()

        obj_name = self.ui.obj_combo.currentText()

        # Get source object.
        try:
            fcobj = self.app.collection.get_by_name(obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
            return

        if fcobj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(obj_name)))
            return

        if getattr(fcobj, 'kind', None) == 'geometry':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The optimal tool diameter can be found only for a Gerber object."))
            return

        def job_thread(app_obj):
            with self.app.proc_container.new(_("Checking ...")):
                try:
                    app_obj.proc_container.update_view_text(' %d%%' % 0)

                    def check_abort():
                        if self.app.abort_flag:
                            # graceful abort requested by the user
                            raise grace

                    # the gap is kept below the real one, like the Check validity test does
                    message, min_dist = NccModel.smallest_gerber_gap(fcobj.tools, self.decimals, check_abort)
                    if message:
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % message)
                        return 'fail'
                    app_obj.proc_container.update_view_text(' %d%%' % 100)

                    min_dist_truncated = self.app.dec_format(float(min_dist), self.decimals)
                    if min_dist_truncated <= 0:
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Could not find a safe tool diameter."))
                        return 'fail'
                    self.safe_tooldia = min_dist_truncated

                    self.optimal_found_sig.emit(min_dist_truncated)

                    l_units = self.units.lower()
                    app_obj.inform.emit(
                        f'[success] {_("Optimal tool diameter found")}: {str(min_dist_truncated)} {l_units}')
                except grace:
                    app_obj.inform.emit('[WARNING_NOTCL] %s' % _("Aborted. The optimal tool diameter was not computed."))
                    return 'fail'
                except Exception as ee:
                    app_obj.log.error(str(ee))
                    app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Could not find a safe tool diameter."))
                    return 'fail'

        self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})

    def on_tool_add(self, custom_dia=None):
        self.blockSignals(True)

        filename = self.app.tools_database_path()

        new_tools_dict = deepcopy(self.default_data)
        updated_tooldia = None

        # construct a list of all 'tooluid' in the self.iso_tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.ncc_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        tooluid = int(max_uid + 1)

        tool_dias = []
        for k, v in self.ncc_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(self.app.dec_format(v[tool_v], self.decimals))

        # determine the new tool diameter
        if custom_dia is None:
            tool_dia = self.ui.new_tooldia_entry.get_value()
        else:
            tool_dia = custom_dia

        tool_dia = valid_tool_dia(tool_dia)
        if tool_dia is None:
            self.build_ui()
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("The tool diameter must be a number greater than zero.")
            )
            self.blockSignals(False)
            return

        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)

        # if new tool diameter already in the Tool List then abort
        if truncated_tooldia in tool_dias:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table."))
            )
            self.blockSignals(False)
            return

        try:
            tools_db_dict = load_tools_database(filename, self.app.options, target=5,
                                               diameter=truncated_tooldia,
                                               units=getattr(self.app, 'app_units', None))
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % tools_database_error_message(error))
            self.blockSignals(False)
            self.on_tool_default_add(dia=tool_dia)
            return

        tool_found = len(tools_db_dict)

        # look in database tools
        for db_tool, db_tool_val in tools_db_dict.items():
            db_tooldia = db_tool_val['tooldia']
            updated_tooldia = db_tooldia if db_tooldia != truncated_tooldia else None
            merge_db_tool_data(new_tools_dict, db_tool_val['data'])

        # test we found a suitable tool in Tools Database or if multiple ones
        if tool_found == 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("Tool not in Tools Database. Adding a default tool.")
            )
            self.on_tool_default_add(dia=tool_dia)
            self.blockSignals(False)
            return

        if tool_found > 1:
            msg = _("Cancelled.\nMultiple tools for one tool diameter found in Tools Database.")
            self.app.inform.emit(f'[WARNING_NOTCL] {msg}')
            self.blockSignals(False)
            return

        # if new tool diameter found in Tools Database already in the Tool List then abort
        if updated_tooldia is not None and updated_tooldia in tool_dias:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
            self.blockSignals(False)
            return

        new_tdia = deepcopy(updated_tooldia) if updated_tooldia is not None else deepcopy(truncated_tooldia)
        self.ncc_tools.update({
            tooluid: {
                'tooldia':          new_tdia,
                'data':             deepcopy(new_tools_dict),
                'solid_geometry':   []
            }
        })
        self.blockSignals(False)
        self.build_ui()

        # select the tool just added
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == tooluid:
                self.ui.tools_table.selectRow(row)
                break

        # update the UI form
        self.update_ui()

        self.app.inform.emit(
            '[success] %s' % _("New tool added to Tool Table from Tools Database.")
        )

    def on_tool_default_add(self, dia=None, muted=None):
        self.blockSignals(True)
        self.units = self.app.app_units.upper()

        if dia is not None:
            tool_dia = dia
        else:
            tool_dia = self.ui.new_tooldia_entry.get_value()

        tool_dia = valid_tool_dia(tool_dia)
        if tool_dia is None:
            self.build_ui()
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("The tool diameter must be a number greater than zero.")
            )
            self.blockSignals(False)
            return

        # construct a list of all 'tooluid' in the self.tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.ncc_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        self.tooluid = int(max_uid + 1)

        tool_dias = []
        for k, v in self.ncc_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(float('%.*f' % (self.decimals, (v[tool_v]))))

        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)
        if truncated_tooldia in tool_dias:
            if muted is None:
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table."))
                )
            # self.ui.tools_table.itemChanged.connect(self.on_tool_edit)
            self.blockSignals(False)
            return

        self.ncc_tools.update({
            int(self.tooluid): {
                'tooldia':          truncated_tooldia,
                'data':             deepcopy(self.default_data),
                'solid_geometry':   []
            }
        })

        self.blockSignals(False)
        self.build_ui()

        # select the tool just added
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == self.tooluid:
                self.ui.tools_table.selectRow(row)
                break

        # update the UI form
        self.update_ui()

        if muted is None:
            self.app.inform.emit('[success] %s' % _("Default tool added to Tool Table."))

    def on_add_tool_by_key(self):
        # tool_add_popup = FCInputDialog(title='%s...' % _("New Tool"),
        #                                text='%s:' % _('Enter a Tool Diameter'),
        #                                min=0.0001, max=10000.0000, decimals=self.decimals)
        btn_icon = QtGui.QIcon(self.app.resource_location + '/open_excellon32.png')

        tool_add_popup = FCInputDialogSpinnerButton(
            title='%s...' % _("New Tool"),
            text='%s:' % _('Enter a Tool Diameter'),
            min=0.0001, max=10000.0000, decimals=self.decimals,
            button_icon=btn_icon,
            callback=self.on_find_optimal_tooldia,
            parent=self.app.ui,
        )
        tool_add_popup.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/letter_t_32.png'))

        def find_optimal(valor):
            tool_add_popup.set_value(float(valor))

        self.optimal_found_sig.connect(find_optimal)

        val, ok = tool_add_popup.get_results()
        if ok:
            if valid_tool_dia(val) is None:
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s' % _("The tool diameter must be a number greater than zero.")
                )
                self.optimal_found_sig.disconnect(find_optimal)
                return
            self.on_tool_add(custom_dia=float(val))
        else:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s...' % _("Adding Tool cancelled")
            )
        self.optimal_found_sig.disconnect(find_optimal)

    def on_tool_edit(self, item):
        self.blockSignals(True)

        edited_row = item.row()
        editeduid = int(self.ui.tools_table.item(edited_row, 3).text())

        try:
            new_tool_dia = float(self.ui.tools_table.item(edited_row, 1).text())
        except ValueError:
            # try to convert comma to decimal point. if it's still not working error message and return
            try:
                new_tool_dia = float(self.ui.tools_table.item(edited_row, 1).text().replace(',', '.'))
            except ValueError:
                self.app.inform.emit(
                    '[ERROR_NOTCL]  %s' % _("Wrong value format entered, use a number.")
                )
                self.blockSignals(False)
                return

        if valid_tool_dia(new_tool_dia) is None:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("The tool diameter must be a number greater than zero.")
            )
            self.blockSignals(False)
            self.build_ui()
            return

        # the diameters of all the other tools, rounded like the new one
        tool_dias = [
            float('%.*f' % (self.decimals, v['tooldia'])) for uid, v in self.ncc_tools.items() if uid != editeduid
        ]
        new_tool_dia = float('%.*f' % (self.decimals, new_tool_dia))

        # identify the tool that was edited and get it's tooluid
        if new_tool_dia not in tool_dias:
            self.ncc_tools[editeduid]['tooldia'] = new_tool_dia
            self.app.inform.emit(
                '[success] %s' % _("Tool from Tool Table was edited.")
            )
            self.blockSignals(False)
            self.build_ui()
            return

        # identify the old tool_dia and restore the text in tool table
        for k, v in self.ncc_tools.items():
            if k == editeduid:
                old_tool_dia = v['tooldia']
                restore_dia_item = self.ui.tools_table.item(edited_row, 1)
                restore_dia_item.setText(str(old_tool_dia))
                break

        self.app.inform.emit(
            '[WARNING_NOTCL] %s' % _("Cancelled. New diameter value is already in the Tool Table.")
        )
        self.blockSignals(False)
        self.build_ui()

    def on_tool_from_db_inserted(self, tool):
        """
        Called from the Tools DB object through an App method when adding a tool from Tools Database
        :param tool: a dict with the tool data
        :return: the uid of the new tool, or 'fail'
        """

        self.units = self.app.app_units.upper()

        tooldia = valid_tool_dia(tool['tooldia'])
        if tooldia is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The tool diameter must be a number greater than zero."))
            return 'fail'

        self.ui_disconnect()

        # construct a list of all 'tooluid' in the self.tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.ncc_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        tooluid = max_uid + 1

        tool_dias = []
        for k, v in self.ncc_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(self.app.dec_format(v[tool_v], self.decimals))

        truncated_tooldia = self.app.dec_format(tooldia, self.decimals)
        if truncated_tooldia in tool_dias:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
            self.ui_connect()
            return 'fail'

        # a Tools Database record may lack parameters of the plugin: start from the same defaults as a default tool
        new_data = deepcopy(self.default_data)
        new_data.update(deepcopy(tool['data']))
        new_data['name'] = '_ncc'
        self.ncc_tools.update({
            tooluid: {
                'tooldia':          truncated_tooldia,
                'data':             new_data,
                'solid_geometry':   []
            }
        })

        self.app.inform.emit('[success] %s' % _("New tool added to Tool Table."))

        self.ui_connect()
        self.build_ui()

        # select the tool just added
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == tooluid:
                self.ui.tools_table.selectRow(row)
                break

        return tooluid

    def reset_fields(self):
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
