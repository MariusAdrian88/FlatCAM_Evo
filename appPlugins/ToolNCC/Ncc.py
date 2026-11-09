# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Modified by: Marius Adrian Stanciu (c)              #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui  # noqa

from appPlugins.ToolNCC.NccUI import NccUI
from appTool import AppTool
from appGUI.GUIElements import (
    VerticalScrollArea,
    FCComboBox,
    FCCheckBox,
    RadioSet,
    FCDoubleSpinner,
    FCInputDialogSpinnerButton,
)

import logging
import math
from copy import copy, deepcopy
import numpy as np

from shapely import (
    Polygon,
)
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


class ToolNcc(Geometry, AppTool):

    optimal_found_sig = QtCore.pyqtSignal(float)
    # (job token, result): the preview job hands the area to clear (or an error text) to the GUI thread
    preview_done_sig = QtCore.pyqtSignal(int, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
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

        self.grid_status_memory = None

        self.obj_name = ""
        self.ncc_obj = None

        self.areas_to_clear_list: list[Polygon] = []

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

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False

        # store here the points for the "Polygon" area selection shape
        self.points = []
        # set this as True when in middle of drawing a "Polygon" area selection shape
        # it is made False by first click to signify that the shape is complete
        self.poly_drawn = False


        # disconnect flags
        self.area_sel_disconnect_flag = False

        # True while the zones drawn on the canvas are exclusion zones and not the area to clear
        self.exclusion_mode = False
        # the exclusion zones, drawn in their own color for as long as the plugin is open
        self.exclusion_list: list[Polygon] = []
        # the zones of the last Area Selection run: the Preview of that mode uses them
        self.last_zones: list[Polygon] = []
        # the shape collections of the preview and of the exclusion zones, made on the first use
        self._preview_shapes = None
        self._exclusion_shapes = None
        self._preview_drawn = False
        # a preview result is drawn only if no clear_preview() happened since its job started
        self._preview_token = 0

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
            "n_final_cleanup":  "tools_ncc_final_cleanup",
            "n_offset":         "tools_ncc_offset_choice",
            "n_offset_value":   "tools_ncc_offset_value",
            "n_milling_type":   "tools_ncc_milling_type",
            "n_check":          "tools_ncc_check_valid",
        }

        self.old_tool_dia = None

        self.gen: NccGen | None = None

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+N', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolNonCopperClear()")

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            # if the Tool Tab is hidden display it, else hide it but only if the objectName is the same
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            # show the Tab
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)

                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                # focus on Tool Tab
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    # if the Tool Tab is not focused, focus on it
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        # focus on Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        # else remove the Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()

        # reset those objects on a new run
        self.ncc_obj = None
        self.bound_obj = None
        self.obj_name = ''
        self.bound_obj_name = ''

        self.build_ui()

        # all the tools are selected by default
        # self.ui.tools_table.selectColumn(0)
        self.ui.tools_table.selectAll()

        self.app.ui.notebook.setTabText(2, _("NCC"))

    def clear_context_menu(self):
        self.ui.tools_table.removeContextMenu()

    def init_context_menu(self):

        # #############################################################################
        # ###################### Setup CONTEXT MENU ###################################
        # #############################################################################
        self.ui.tools_table.setupContextMenu()
        self.ui.tools_table.addContextMenu(
            _("Add"), self.on_tool_add_by_key, icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Add from DB"), self.on_ncc_tool_add_from_db_clicked, icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Delete"), lambda:
            self.on_tool_delete(rows_to_delete=None, all_tools=None),
            icon=QtGui.QIcon(self.app.resource_location + "/delete32.png")
        )

    def connect_signals(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        try:
            self.ui.level.toggled.disconnect(self.on_level_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.level.toggled.connect(self.on_level_changed)

        try:
            self.ui.find_optimal_button.clicked.disconnect(self.on_find_optimal_tooldia)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.find_optimal_button.clicked.connect(self.on_find_optimal_tooldia)

        # Custom Signal
        try:
            self.optimal_found_sig.disconnect(self.on_optimal_found)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.optimal_found_sig.connect(self.on_optimal_found)

        try:
            self.ui.deltool_btn.clicked.disconnect(self.on_tool_delete)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.deltool_btn.clicked.connect(self.on_tool_delete)

        try:
            self.ui.generate_button.clicked.disconnect(self.gen.on_ncc_click)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.generate_button.clicked.connect(self.gen.on_ncc_click)

        try:
            self.ui.op_radio.activated_custom.disconnect(self.on_operation_change)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.op_radio.activated_custom.connect(self.on_operation_change)

        try:
            self.ui.reference_type_combo.currentIndexChanged.disconnect(self.on_reference_combo_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.reference_type_combo.currentIndexChanged.connect(self.on_reference_combo_changed)

        try:
            self.ui.select_method_combo.currentIndexChanged.disconnect(self.ui.on_selection)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.select_method_combo.currentIndexChanged.connect(self.ui.on_selection)

        try:
            self.ui.rest_cb.stateChanged.disconnect(self.ui.on_rest_machining_check)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.rest_cb.stateChanged.connect(self.ui.on_rest_machining_check)

        try:
            self.ui.order_combo.currentIndexChanged.disconnect(self.on_order_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.order_combo.currentIndexChanged.connect(self.on_order_changed)

        try:
            self.ui.method_combo.currentIndexChanged.disconnect(self.ui.on_method_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.method_combo.currentIndexChanged.connect(self.ui.on_method_changed)

        try:
            self.ui.type_obj_radio.activated_custom.disconnect(self.on_type_obj_index_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.type_obj_radio.activated_custom.connect(self.on_type_obj_index_changed)

        try:
            self.ui.apply_param_to_all.clicked.disconnect(self.on_apply_param_to_all_clicked)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.apply_param_to_all.clicked.connect(self.on_apply_param_to_all_clicked)

        # add a new tool Signals
        try:
            self.ui.search_and_add_btn.clicked.disconnect(self.on_tool_add)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.search_and_add_btn.clicked.connect(lambda: self.on_tool_add())

        try:
            self.ui.addtool_from_db_btn.clicked.disconnect(self.on_ncc_tool_add_from_db_clicked)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.addtool_from_db_btn.clicked.connect(self.on_ncc_tool_add_from_db_clicked)

        try:
            self.app.proj_selection_changed.disconnect(self.on_object_selection_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

        try:
            self.ui.reset_button.clicked.disconnect(self.set_tool_ui)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

        for signal, slot in (
                (self.ui.preview_button.clicked, self.on_preview),
                (self.ui.excl_add_button.clicked, self.on_add_exclusion),
                (self.ui.excl_delete_button.clicked, self.on_delete_exclusions),
                (self.ui.obj_combo.currentIndexChanged, self.on_source_changed),
                (self.ui.multi_cb.stateChanged, self.clear_preview),
                (self.preview_done_sig, self.on_preview_done)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError, AttributeError):
                pass
            signal.connect(slot)

        try:
            self.app.cleanup.disconnect(self.set_tool_ui)
        except (TypeError, RuntimeError, AttributeError):
            pass
        # Cleanup on Graceful exit (CTRL+ALT+X combo key)
        self.app.cleanup.connect(self.set_tool_ui)

    def on_optimal_found(self, val):
        self.ui.new_tooldia_entry.set_value(float(val))

    def end_area_selection(self):
        """
        Leave the area selection mode: give the canvas back to the app, enable the panel and forget the drawn zones.
        """
        if self.area_sel_disconnect_flag is True:
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

            try:
                # restore the Grid snapping if it was active before
                if self.grid_status_memory is True:
                    self.app.ui.grid_snap_btn.trigger()
                self.app.tool_shapes.clear(update=True)
            except Exception as e:
                self.app.log.error("ToolNCC.end_area_selection() --> %s" % str(e))

            self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

            self.app.ui.notebook.setDisabled(False)
            self.area_sel_disconnect_flag = False

        self.points = []
        self.poly_drawn = False
        self.first_click = False
        self.cursor_pos = None
        self.areas_to_clear_list.clear()
        self.exclusion_mode = False

        self.delete_moving_selection_shape()
        self.delete_tool_selection_shape()

    def start_area_selection(self, exclusion=False):
        """
        Let the user draw zones on the canvas with the area selection mouse handlers: the area to clear, or the
        exclusion zones. The panel is disabled until the selection ends (right click or Escape).
        """
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the start point of the area."))

        # a new selection starts empty; the mouse handlers of the controller own this state
        self.areas_to_clear_list.clear()
        self.exclusion_mode = exclusion
        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False
        self.points = []
        self.poly_drawn = False

        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

        self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_release)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)
        self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)

        # disconnect flags
        self.area_sel_disconnect_flag = True
        # disable the "notebook UI" until finished
        self.app.ui.notebook.setDisabled(True)

    def shape_type(self):
        """The shape of the zone being drawn: the exclusion shape in exclusion mode, else the area selection one."""
        radio = self.ui.excl_shape_radio if self.exclusion_mode else self.ui.area_shape_radio
        return radio.get_value()

    # ------------------------------------------------------------------------------------------------------------
    # exclusion zones
    # ------------------------------------------------------------------------------------------------------------
    def on_add_exclusion(self):
        if self.area_sel_disconnect_flag is True:
            return
        self.clear_preview()
        self.start_area_selection(exclusion=True)

    def on_delete_exclusions(self):
        self.exclusion_list.clear()
        self.update_exclusions()

    def exclusion_areas(self):
        """The exclusion zones to remove from the area to clear, or None when they are off or there are none."""
        try:
            use = bool(self.ui.excl_cb.get_value())
        except RuntimeError:
            return None
        return list(self.exclusion_list) if use and self.exclusion_list else None

    def update_exclusions(self):
        """Show the count of the zones and draw them in their own color."""
        self.ui.excl_count_value.setText(str(len(self.exclusion_list)))
        self.clear_preview()
        store = self._shape_store('_exclusion_shapes')
        if store is None:
            return
        store.clear(update=False)
        for poly in self.exclusion_list:
            store.add(poly, color='#CC0000FF', face_color='#CC000040', update=False, layer=0, tolerance=None)
        store.redraw()

    def _shape_store(self, attribute):
        """The shape collection kept in the attribute, created on the first use (None without a canvas)."""
        store = getattr(self, attribute)
        if store is None:
            try:
                store = self.app.plotcanvas.new_shape_collection(layers=1)
            except Exception as e:
                self.app.log.error("ToolNCC._shape_store() --> %s" % str(e))
                return None
            setattr(self, attribute, store)
        return store

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
        if clipping_type == 1:
            zones = list(self.last_zones)
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

    def on_source_changed(self, *args):
        """The object to clear changed: its preview and its Area Selection zones do not apply any more."""
        self.last_zones = []
        self.clear_preview()

    def clear_preview(self, *args):
        """Remove the preview from the canvas; a job still running will not draw its result."""
        self._preview_token += 1
        if self._preview_drawn and self._preview_shapes is not None:
            self._preview_shapes.clear(update=True)
        self._preview_drawn = False

    def on_plugin_cleanup(self):
        """The plugin is closing: leave the area selection, take the preview and the exclusion zones off the canvas."""
        self.end_area_selection()
        self.clear_preview()
        self.last_zones = []
        self.exclusion_list.clear()
        if self._exclusion_shapes is not None:
            self._exclusion_shapes.clear(update=True)

    def set_tool_ui(self):
        # a Reset or an app cleanup must not leave the canvas handlers of an area selection connected
        if self.area_sel_disconnect_flag is True:
            self.end_area_selection()
        # a Reset drops the preview and the exclusion zones
        self.clear_preview()
        self.last_zones = []
        self.exclusion_list.clear()
        if self._exclusion_shapes is not None:
            self._exclusion_shapes.clear(update=True)

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

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False

        prog_plot = True if self.app.options.get(
            "tools_ncc_plotting",
            app_defaults.get("tools_ncc_plotting", AppDefaults.factory_defaults.get("tools_ncc_plotting"))) == 'progressive' else False
        if prog_plot:
            self.temp_shapes.clear(update=True)

        self.areas_to_clear_list.clear()

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

    def on_object_selection_changed(self, current):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                name = current.indexes()[0].internalPointer().obj.obj_options['name']
                kind = current.indexes()[0].internalPointer().obj.kind

                if kind in ['gerber', 'geometry']:
                    self.ui.type_obj_radio.set_value(kind)

                self.ui.obj_combo.set_value(name)
            except Exception:
                pass

    def on_toggle_all_rows(self):
        """
        will toggle the selection of all rows in Tools table

        :return:
        """
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        if len(sel_rows) == self.ui.tools_table.rowCount():
            self.ui.tools_table.clearSelection()
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
        else:
            self.ui.tools_table.selectAll()
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
            )

    def on_row_selection_change(self):
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        # update UI only if only one row is selected otherwise having multiple rows selected will deform information
        # for the rows other that the current one (first selected)
        if len(sel_rows) == 1:
            self.update_ui()

    def update_ui(self):
        self.blockSignals(True)

        sel_rows = set()
        table_items = self.ui.tools_table.selectedItems()
        if table_items:
            for it in table_items:
                sel_rows.add(it.row())
            # sel_rows = sorted(set(index.row() for index in self.ui.tools_table.selectedIndexes()))

        if not sel_rows or len(sel_rows) == 0:
            self.ui.generate_button.setDisabled(True)
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
            self.blockSignals(False)
            return
        else:
            self.ui.generate_button.setDisabled(False)

        for current_row in sel_rows:
            # populate the form with the data from the tool associated with the row parameter
            try:
                item = self.ui.tools_table.item(current_row, 3)
                if item is not None:
                    tooluid = int(item.text())
                else:
                    return
            except Exception as e:
                self.app.log.error("Tool missing. Add a tool in the Tool Table. %s" % str(e))
                return

            # update the QLabel that shows for which Tool we have the parameters in the UI form
            if len(sel_rows) == 1:
                cr = current_row + 1
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s %d</font></b>" % (_('Parameters for'), _("Tool"), cr)
                )
                try:
                    # set the form with data from the newly selected tool
                    for tooluid_key, tooluid_value in list(self.ncc_tools.items()):
                        if int(tooluid_key) == tooluid:
                            for key, value in tooluid_value.items():
                                if key == 'data':
                                    self.storage_to_form(tooluid_value['data'])
                except Exception as e:
                    self.app.log.error("ToolNcc ---> update_ui() " + str(e))
            else:
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
                )

        self.blockSignals(False)

    def storage_to_form(self, dict_storage):
        dict_storage.setdefault('tools_ncc_final_cleanup', False)
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

    def form_to_storage(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table, so we can't save the GUI elements values to storage
            return

        self.blockSignals(True)

        widget_changed = self.sender()
        wdg_objname = widget_changed.objectName()
        option_changed = self.name2option[wdg_objname]

        # row = self.ui.tools_table.currentRow()
        rows = sorted(set(index.row() for index in self.ui.tools_table.selectedIndexes()))
        for row in rows:
            if row < 0:
                row = 0
            tooluid_item = int(self.ui.tools_table.item(row, 3).text())

            for tooluid_key, tooluid_val in self.ncc_tools.items():
                if int(tooluid_key) == tooluid_item:
                    new_option_value = self.form_fields[option_changed].get_value()
                    if option_changed in tooluid_val:
                        tooluid_val[option_changed] = new_option_value
                    if option_changed in tooluid_val['data']:
                        tooluid_val['data'][option_changed] = new_option_value

        self.blockSignals(False)

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

    def rebuild_ui(self):
        # read the table tools uid
        current_uid_list = []
        for row in range(self.ui.tools_table.rowCount()):
            uid = int(self.ui.tools_table.item(row, 3).text())
            current_uid_list.append(uid)

        new_tools = {}
        new_uid = 1

        for current_uid in current_uid_list:
            new_tools[new_uid] = deepcopy(self.ncc_tools[current_uid])
            new_uid += 1

        # keep the same dict: the generator and the tool state hold a reference to it
        self.ncc_tools.clear()
        self.ncc_tools.update(new_tools)

        # build_ui() sorts by diameter when the order is Forward or Reverse, which would undo the drag & drop
        if self.ui.order_combo.isEnabled() and self.ui.order_combo.get_value() != 0:
            self.ui.order_combo.set_value(0)

        # the tools table changed therefore we need to rebuild it
        QtCore.QTimer.singleShot(20, self.build_ui)

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

    def on_tool_add_by_key(self):
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

    def on_tool_delete(self, rows_to_delete=None, all_tools=None):
        """
        Will delete a tool in the tool table

        :param rows_to_delete:  which rows to delete; can be a list
        :param all_tools:       delete all tools in the tool table
        :return:
        """
        self.blockSignals(True)

        deleted_tools_list = []

        if all_tools:
            self.ncc_tools.clear()
            self.blockSignals(False)
            self.build_ui()
            self.update_ui()
            return

        if rows_to_delete:
            try:
                for row in rows_to_delete:
                    tooluid_del = int(self.ui.tools_table.item(row, 3).text())
                    deleted_tools_list.append(tooluid_del)
            except TypeError:
                tooluid_del = int(self.ui.tools_table.item(rows_to_delete, 3).text())
                deleted_tools_list.append(tooluid_del)

            for t in deleted_tools_list:
                self.ncc_tools.pop(t, None)

            self.blockSignals(False)
            self.build_ui()
            self.update_ui()
            return

        try:
            for row in sorted({row_sel.row() for row_sel in self.ui.tools_table.selectedItems()}):
                if row < 0:
                    continue
                tooluid_del = int(self.ui.tools_table.item(row, 3).text())
                deleted_tools_list.append(tooluid_del)

            for t in deleted_tools_list:
                self.ncc_tools.pop(t, None)

        except AttributeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Delete failed. Select a tool to delete."))
            self.blockSignals(False)
            return
        except Exception as e:
            self.app.log.error(str(e))

        if not deleted_tools_list:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No tool selected."))
            self.blockSignals(False)
            return

        self.app.inform.emit('[success] %s' % _("Tools deleted from Tool Table."))
        self.blockSignals(False)
        self.build_ui()
        # the selection is gone: refresh the parameters label and the Generate button
        self.update_ui()

    # To be called after clicking on the plot.
    def on_mouse_release(self, event):
        event_pos = event.pos
        # event_is_dragging = event.is_dragging
        right_button = 2

        event_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(event_pos[0], event_pos[1])
        else:
            curr_pos = (event_pos[0], event_pos[1])

        x1, y1 = curr_pos[0], curr_pos[1]

        shape_type = self.shape_type()

        # do clear area only for left mouse clicks
        if event.button == 1:
            if shape_type == "square":
                if self.first_click is False:
                    self.first_click = True
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the end point of the area."))

                    self.cursor_pos = (curr_pos[0], curr_pos[1])
                else:
                    self.app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    self.app.delete_selection_shape()

                    x0, y0 = self.cursor_pos[0], self.cursor_pos[1]

                    pt1 = (x0, y0)
                    pt2 = (x1, y0)
                    pt3 = (x1, y1)
                    pt4 = (x0, y1)

                    new_rectangle = Polygon(
                        [
                            pt1, pt2, pt3, pt4
                        ]
                    )
                    self.areas_to_clear_list.append(new_rectangle)

                    # add a temporary shape on canvas
                    self.draw_tool_selection_shape(old_coords=(x0, y0), coords=(x1, y1))

                    self.first_click = False
                    return
            else:
                self.points.append((x1, y1))

                if len(self.points) > 1:
                    self.poly_drawn = True
                    self.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))

                return ""
        elif event.button == right_button and self.mouse_is_dragging is False:

            shape_type = self.shape_type()

            if shape_type == "square":
                self.first_click = False
            else:
                # if we finish to add a polygon
                if self.poly_drawn is True:
                    try:
                        # try to add the point where we last clicked if it is not already in the self.points
                        last_pt = (x1, y1)
                        if last_pt != self.points[-1]:
                            self.points.append(last_pt)
                    except IndexError:
                        pass

                    # we need to add a Polygon and a Polygon can be made only from at least 3 points
                    if len(self.points) > 2:
                        self.delete_moving_selection_shape()
                        pol = Polygon(self.points)
                        # do not add invalid polygons even if they are drawn by utility geometry
                        if pol.is_valid:
                            self.areas_to_clear_list.append(pol)
                            self.draw_selection_shape_polygon(points=self.points)
                            self.app.inform.emit(
                                _("Zone added. Click to start adding next zone or right click to finish.")
                            )
                        else:
                            self.app.inform.emit(
                                '[WARNING_NOTCL] %s' % _("The polygon crosses itself. The zone was not added.")
                            )

                    self.points = []
                    self.poly_drawn = False
                    return

            # the selection is over: hand the zones over and give the canvas and the panel back
            areas_to_clear = list(self.areas_to_clear_list)
            exclusion_mode = self.exclusion_mode
            self.end_area_selection()

            if not areas_to_clear:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No area selected."))
                return

            if exclusion_mode:
                self.exclusion_list.extend(areas_to_clear)
                self.update_exclusions()
                self.app.inform.emit('[success] %s' % _("Exclusion zones added."))
                return
            # the Preview of this mode draws these zones
            self.last_zones = areas_to_clear

            # the source object may have been deleted while the zones were drawn
            ncc_obj = self.gen.ncc_obj
            try:
                found = self.app.collection.get_by_name(ncc_obj.obj_options['name'])
            except Exception:
                found = None
            if found is None or found is not ncc_obj:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The object to be processed is no longer available."))
                return

            self.gen.clear_ncc_area_selection_option(areas_to_clear)

    # called on mouse move
    def on_mouse_move(self, event):
        shape_type = self.shape_type()

        event_pos = event.pos
        event_is_dragging = event.is_dragging

        curr_pos = self.app.plotcanvas.translate_coords(event_pos)

        # detect mouse dragging motion
        if event_is_dragging is True:
            self.mouse_is_dragging = True
        else:
            self.mouse_is_dragging = False

        # update the cursor position
        if self.app.grid_status():
            # Update cursor
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])
            app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

            self.app.app_cursor.set_data(
                np.asarray([(curr_pos[0], curr_pos[1])]),
                symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                edge_width=self.app.options.get(
                    "global_cursor_width",
                    app_defaults.get("global_cursor_width", AppDefaults.factory_defaults.get("global_cursor_width"))),
                size=self.app.options.get(
                    "global_cursor_size",
                    app_defaults.get("global_cursor_size", AppDefaults.factory_defaults.get("global_cursor_size"))),
            )

        if self.cursor_pos is None:
            self.cursor_pos = (0, 0)

        self.app.dx = curr_pos[0] - float(self.cursor_pos[0])
        self.app.dy = curr_pos[1] - float(self.cursor_pos[1])

        # # update the positions on status bar
        # self.app.ui.position_label.setText("&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
        #                                    "<b>Y</b>: %.4f&nbsp;" % (curr_pos[0], curr_pos[1]))
        # self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
        #                                        "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (self.app.dx, self.app.dy))
        self.app.ui.update_location_labels(
            self.app.dx,
            self.app.dy,
            curr_pos[0],
            curr_pos[1],
        )

        # units = self.app.app_units.lower()
        # self.app.plotcanvas.text_hud.text = \
        #     'Dx:\t{:<.4f} [{:s}]\nDy:\t{:<.4f} [{:s}]\n\nX:  \t{:<.4f} [{:s}]\nY:  \t{:<.4f} [{:s}]'.format(
        #         self.app.dx, units, self.app.dy, units, curr_pos[0], units, curr_pos[1], units)
        self.app.plotcanvas.on_update_text_hud(
            self.app.dx,
            self.app.dy,
            curr_pos[0],
            curr_pos[1],
        )

        # draw the utility geometry
        if shape_type == "square":
            if self.first_click:
                self.app.draw_moving_selection_shape(
                    old_coords=(self.cursor_pos[0], self.cursor_pos[1]),
                    coords=(curr_pos[0], curr_pos[1]),
                )
        else:
            self.draw_moving_selection_shape_poly(
                points=self.points,
                data=(curr_pos[0], curr_pos[1]),
            )

    def on_key_press(self, event):
        # modifiers = QtWidgets.QApplication.keyboardModifiers()

        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if type(event) is int:
            key = event
        # events from the GUI are of type QKeyEvent
        elif type(event) == QtGui.QKeyEvent:
            key = event.key()

        # events from Vispy are of type KeyEvent
        else:
            key = event.key

        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':

            self.end_area_selection()

    def on_ncc_tool_add_from_db_executed(self, tool):
        """
        Here add the tool from DB  in the selected geometry object
        :return:
        """
        tool_from_db = deepcopy(tool)

        if tool['data']['tool_target'] not in [0, 5]:   # [General, NCC]
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
            return 'fail'

        res = self.on_ncc_tool_from_db_inserted(tool=tool_from_db)

        if res == 'fail':
            return 'fail'
        self.app.inform.emit('[success] %s' % _("Tool from DB added in Tool Table."))

        # select last tool added
        toolid = res
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == toolid:
                self.ui.tools_table.selectRow(row)
        self.on_row_selection_change()
        return res

    def on_ncc_tool_from_db_inserted(self, tool):
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

    def on_ncc_tool_add_from_db_clicked(self):
        """
        Called when the user wants to add a new tool from Tools Database. It will create the Tools Database object
        and display the Tools Database tab in the form needed for the Tool adding
        :return: None
        """

        # if the Tools Database is already opened focus on it
        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.tabText(idx) == _("Tools Database"):
                self.app.ui.plot_tab_area.setCurrentWidget(self.app.tools_db_tab)
                break
        ret_val = self.app.on_tools_database(source='ncc')
        if ret_val == 'fail':
            return
        self.app.tools_db_tab.ok_to_add = True
        self.app.tools_db_tab.ui.buttons_frame.hide()
        self.app.tools_db_tab.ui.add_tool_from_db.show()
        self.app.tools_db_tab.ui.cancel_tool_from_db.show()

    def reset_fields(self):
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
