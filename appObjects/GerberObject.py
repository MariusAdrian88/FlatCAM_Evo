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

from PyQt6 import QtWidgets, QtCore
from appGUI.GUIElements import FCCheckBox
from appGUI.ObjectUI import GerberObjectUI
from appParsers.ParseGerber import Gerber, drop_placeholder_record_keys
from appObjects.AppObjectTemplate import FlatCAMObj, OffsetUiMixin, PropertiesPanelMixin, ObjectDeleted, ValidationError

from camlib import flatten_shapely_geometry, isolation_leftover_paths, clip_paths_to_area, area_outline_paths, \
    boundary_passes_message, milling_orient, pass_covers_area, gerber_quad_segs

from shapely import MultiLineString, LinearRing, MultiPolygon, Polygon, LineString, Point
from shapely.ops import unary_union
from appParsers.DXF.writer import build_dxf

import numpy as np
from copy import deepcopy

from appHandlers.appHistory import history_of
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class GerberObject(OffsetUiMixin, PropertiesPanelMixin, FlatCAMObj, Gerber):
    """
    Represents Gerber code.
    """
    # Dispatch metadata, read by AppObject, ObjectCollection and appPlotManager instead of kind chains.
    # status_color: color of the object name in the status bar message (None: no message).
    status_color = 'green'
    source_exporter = 'export_gerber'
    # tool_option_prefix: extra application options (with this prefix) copied into obj_options on creation.
    tool_option_prefix = 'tools_iso_'
    # inherits_mill_options: the tools_mill_* and tools_* options are copied into obj_options on creation.
    inherits_mill_options = True
    # has_plot_colors: the object has user settable fill / outline plot colors.
    has_plot_colors = True
    # plot_default_fill_option / plot_default_line_option: option keys of the colors restored by "Default".
    plot_default_fill_option = 'gerber_plot_fill'
    plot_default_line_option = 'gerber_plot_line'
    # alpha_fill_option: option key whose color alpha is the default alpha level.
    # default_alpha_level: fixed default alpha level when alpha_fill_option is None.
    alpha_fill_option = 'gerber_plot_fill'
    default_alpha_level = None
    replotApertures = QtCore.pyqtSignal()

    do_buffer_signal = QtCore.pyqtSignal()

    ui_type = GerberObjectUI

    def __init__(self, name, app):
        self.app = app
        self.decimals = self.app.decimals

        # the fixed number of the preference, for the code that needs one; the parser follows the preferences
        # (Adaptive / Fixed) circle by circle
        self.circle_steps = int(self._app_option("gerber_circle_steps"))

        Gerber.__init__(self, steps_per_circle=None, app=app)
        FlatCAMObj.__init__(self, name, app=app)

        self.kind = "gerber"

        # The 'name' is already in self.obj_options from FlatCAMObj
        # Automatically updates the UI
        self.obj_options.update({
            "plot": True,
            "multicolored": False,
            "solid": False,
            "noncoppermargin": 0.0,
            "noncopperrounded": False,
            "bboxmargin": 0.0,
            "bboxrounded": False,
            "aperture_display": False,
            "follow": False,
            "milling_type": 'cl',
        })

        # type of isolation: 0 = exteriors, 1 = interiors, 2 = complete isolation (both interiors and exteriors)
        self.iso_type = 2

        self.multigeo = False

        self.follow = False

        self.apertures_row = 0

        # store the source file here
        self.source_file = ""

        # list of rows with apertures plotted
        self.marked_rows = []

        # dict to store the polygons selected for isolation; key is the shape added to be plotted and value is the poly
        self.poly_dict = {}

        # store the status of grid snapping
        self.grid_status_memory = None

        self.units_found = self.app.app_units

        self.fill_color = self._app_option('gerber_plot_fill')
        self.outline_color = self._app_option('gerber_plot_line')
        self.alpha_level = 'bf'

        # keep track if the UI is built, so we don't have to build it every time
        self.ui_build = False

        # aperture marking storage
        self.mark_shapes_storage = {}

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from predecessors.
        self.ser_attrs = ['obj_options', 'kind', 'fill_color', 'outline_color', 'alpha_level'] + self.ser_attrs

    def copy_from(self, src):
        """
        Make this (new, empty) Gerber a copy of ``src``. Used by both copy paths of AppObjectOps
        (``on_copy_command`` and ``on_copy_object2``). Everything is deep-copied, nothing is shared with ``src``.

        Copied: solid_geometry, follow_geometry, tools (the apertures with their geometry), aperture_macros,
        source_file, export_as_regions (the apertures of a region-exported object do not describe its geometry,
        the copy must keep exporting regions), multigeo, int_digits / frac_digits (the number format, serialized
        with the project) and units (``new_object`` converts them to the app units if they differ).

        Not copied, on purpose: obj_options (the new object takes the current app defaults, as every new
        object) and the UI / plot state (colors, marked apertures, selection).

        The 2 old paths differed: ``on_copy_command`` lost aperture_macros, ``on_copy_object2`` lost
        follow_geometry and source_file; both lost multigeo and the number format. The union of both is copied.
        The caller keeps its own checks (``on_copy_object2`` fails on an object without apertures).

        :param src: the GerberObject to copy
        :return:    None
        """
        self.solid_geometry = deepcopy(src.solid_geometry)
        self.follow_geometry = deepcopy(getattr(src, 'follow_geometry', self.follow_geometry))
        self.tools = deepcopy(src.tools)
        self.aperture_macros = deepcopy(getattr(src, 'aperture_macros', self.aperture_macros))
        self.source_file = deepcopy(getattr(src, 'source_file', self.source_file))
        self.export_as_regions = bool(getattr(src, 'export_as_regions', False))
        self.multigeo = getattr(src, 'multigeo', self.multigeo)
        self.int_digits = getattr(src, 'int_digits', self.int_digits)
        self.frac_digits = getattr(src, 'frac_digits', self.frac_digits)
        self.units = getattr(src, 'units', self.units)

    def set_ui(self, ui):
        """
        Maps options with GUI inputs.
        Connects GUI events to methods.

        :param ui: GUI object.
        :type ui: GerberObjectUI
        :return: None
        """
        FlatCAMObj.set_ui(self, ui)
        self.app.log.debug("GerberObject.set_ui()")

        self.units = self.app.app_units.upper()

        self.replotApertures.connect(self.on_mark_cb_click_table)

        self.form_fields.update({
            "plot": self.ui.plot_cb,
            "multicolored": self.ui.multicolored_cb,
            "solid": self.ui.solid_cb,
            "noncoppermargin": self.ui.noncopper_margin_entry,
            "noncopperrounded": self.ui.noncopper_rounded_cb,
            "bboxmargin": self.ui.bbmargin_entry,
            "bboxrounded": self.ui.bbrounded_cb,
            "aperture_display": self.ui.aperture_table_visibility_cb,
            "follow": self.ui.follow_cb
        })

        # Fill form fields only on object create
        self.to_form()

        assert isinstance(self.ui, GerberObjectUI), \
            "Expected a GerberObjectUI, got %s" % type(self.ui)

        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.plot_cb.stateChanged.connect(self.on_plot_cb_click)
        self.ui.solid_cb.stateChanged.connect(self.on_solid_cb_click)
        self.ui.multicolored_cb.stateChanged.connect(self.on_multicolored_cb_click)

        # Editor
        self.ui.editor_button.clicked.connect(lambda: self.app.on_editing_start())

        # Properties
        self.ui.info_button.toggled.connect(self.on_properties)
        self.calculations_finished.connect(self.update_area_chull)
        self.ui.treeWidget.itemExpanded.connect(self.on_properties_expanded)
        self.ui.treeWidget.itemCollapsed.connect(self.on_properties_expanded)

        # Tools
        self.ui.iso_button.clicked.connect(lambda: self.app.isolation_tool.run(toggle=True))
        self.ui.generate_cutout_button.clicked.connect(lambda: self.app.cutout_tool.run(toggle=True))
        self.ui.generate_film_button.clicked.connect(lambda: self.app.film_tool.run(toggle=True))
        self.ui.generate_ncc_button.clicked.connect(lambda: self.app.ncclear_tool.run(toggle=True))
        self.ui.generate_follow_button.clicked.connect(lambda: self.app.follow_tool.run(toggle=True))

        # Utilities
        self.ui.generate_bb_button.clicked.connect(self.on_generatebb_button_click)
        self.ui.generate_noncopper_button.clicked.connect(self.on_generate_non_copper_button_click)
        self.ui.util_button.clicked.connect(lambda st: self.ui.util_frame.show() if st else self.ui.util_frame.hide())

        self.ui.aperture_table_visibility_cb.stateChanged.connect(self.on_aperture_table_visibility_change)
        self.ui.follow_cb.stateChanged.connect(self.on_follow_cb_click)

        self.do_buffer_signal.connect(self.on_generate_buffer)

        # Show/Hide Advanced Options
        app_mode = self._app_option("global_app_level")
        self.change_level(app_mode)

        if self._app_option("gerber_buffering") == 'no':
            self.ui.create_buffer_button.show()
            try:
                self.ui.create_buffer_button.clicked.disconnect(self.on_generate_buffer)
            except TypeError:
                pass
            self.ui.create_buffer_button.clicked.connect(self.on_generate_buffer)
        else:
            self.ui.create_buffer_button.hide()

        # set initial state of the aperture table and associated widgets
        self.on_aperture_table_visibility_change()

        self.units_found = self.app.app_units

        self.set_offset_values()

    def on_level_changed(self, checked):
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: green;
                                                }
                                                """)

            self.ui.tools_table_label.hide()
            self.ui.tt_frame.hide()
            self.ui.aperture_table_visibility_cb.set_value(False)
            self.ui.aperture_table_visibility_cb.hide()

            self.ui.follow_cb.hide()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: red;
                                                }
                                                """)

            self.ui.tools_table_label.show()
            self.ui.tt_frame.show()
            self.ui.aperture_table_visibility_cb.show()

            self.ui.follow_cb.show()

    def build_ui(self):
        FlatCAMObj.build_ui(self)

        if self.ui.aperture_table_visibility_cb.get_value() and self.ui_build is False:
            self.ui_build = True

            try:
                # if connected, disconnect the signal from the slot on item_changed as it creates issues
                self.ui.apertures_table.itemChanged.disconnect()
            except (TypeError, AttributeError):
                pass

            self.apertures_row = 0

            sort = []
            for k in list(self.tools.keys()):
                sort.append(int(k))
            sorted_apertures = sorted(sort)

            n = len(sorted_apertures)
            self.ui.apertures_table.setRowCount(n)

            for ap_code in sorted_apertures:
                # ap_code = str(ap_code)

                # ------------------------ Aperture ID ----------------------------------------------------------------
                ap_id_item = QtWidgets.QTableWidgetItem('%d' % int(self.apertures_row + 1))
                ap_id_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                self.ui.apertures_table.setItem(self.apertures_row, 0, ap_id_item)  # Tool name/id

                # ------------------------ Aperture CODE --------------------------------------------------------------
                ap_code_item = QtWidgets.QTableWidgetItem(str(ap_code))
                ap_code_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

                # ------------------------ Aperture TYPE --------------------------------------------------------------
                ap_type_item = QtWidgets.QTableWidgetItem(str(self.tools[ap_code]['type']))
                ap_type_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

                if str(self.tools[ap_code]['type']) == 'R' or str(self.tools[ap_code]['type']) == 'O':
                    ap_dim_item = QtWidgets.QTableWidgetItem(
                        '%.*f, %.*f' % (self.decimals, self.tools[ap_code]['width'],
                                        self.decimals, self.tools[ap_code]['height']
                                        )
                    )
                    ap_dim_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                elif str(self.tools[ap_code]['type']) == 'P':
                    ap_dim_item = QtWidgets.QTableWidgetItem(
                        '%.*f, %.*f' % (self.decimals, self.tools[ap_code]['diam'],
                                        self.decimals, self.tools[ap_code]['nVertices'])
                    )
                    ap_dim_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                else:
                    ap_dim_item = QtWidgets.QTableWidgetItem('')
                    ap_dim_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

                # ------------------------ Aperture SIZE --------------------------------------------------------------
                try:
                    if self.tools[ap_code]['size'] is not None:
                        ap_size_item = QtWidgets.QTableWidgetItem(
                            '%.*f' % (self.decimals, float(self.tools[ap_code]['size'])))
                    else:
                        ap_size_item = QtWidgets.QTableWidgetItem('')
                except KeyError:
                    ap_size_item = QtWidgets.QTableWidgetItem('')
                ap_size_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

                # ------------------------ Aperture MARK --------------------------------------------------------------
                mark_item = FCCheckBox()
                mark_item.setLayoutDirection(QtCore.Qt.LayoutDirection.RightToLeft)
                # if self.ui.aperture_table_visibility_cb.isChecked():
                #     mark_item.setChecked(True)

                self.ui.apertures_table.setItem(self.apertures_row, 1, ap_code_item)  # Aperture Code
                self.ui.apertures_table.setItem(self.apertures_row, 2, ap_type_item)  # Aperture Type
                self.ui.apertures_table.setItem(self.apertures_row, 3, ap_size_item)   # Aperture Dimensions
                self.ui.apertures_table.setItem(self.apertures_row, 4, ap_dim_item)   # Aperture Dimensions

                empty_plot_item = QtWidgets.QTableWidgetItem('')
                empty_plot_item.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                self.ui.apertures_table.setItem(self.apertures_row, 5, empty_plot_item)
                self.ui.apertures_table.setCellWidget(self.apertures_row, 5, mark_item)

                self.apertures_row += 1

            self.ui.apertures_table.selectColumn(0)
            self.ui.apertures_table.resizeColumnsToContents()
            self.ui.apertures_table.resizeRowsToContents()

            vertical_header = self.ui.apertures_table.verticalHeader()
            # vertical_header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            vertical_header.hide()
            self.ui.apertures_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            horizontal_header = self.ui.apertures_table.horizontalHeader()
            horizontal_header.setMinimumSectionSize(10)
            horizontal_header.setDefaultSectionSize(70)
            horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
            horizontal_header.resizeSection(0, 27)
            horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            horizontal_header.setSectionResizeMode(4,  QtWidgets.QHeaderView.ResizeMode.Stretch)
            horizontal_header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Fixed)
            horizontal_header.resizeSection(5, 17)
            self.ui.apertures_table.setColumnWidth(5, 17)

            self.ui.apertures_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.ui.apertures_table.setSortingEnabled(False)
            self.ui.apertures_table.setMinimumHeight(self.ui.apertures_table.getHeight())
            self.ui.apertures_table.setMaximumHeight(self.ui.apertures_table.getHeight())

            # update the 'mark' checkboxes state according to what is stored in the self.marked_rows list
            if self.marked_rows:
                for row in range(self.ui.apertures_table.rowCount()):
                    try:
                        self.ui.apertures_table.cellWidget(row, 5).set_value(self.marked_rows[row])
                    except IndexError:
                        pass

            self.ui_connect()

    def ui_connect(self):
        for row in range(self.ui.apertures_table.rowCount()):
            try:
                self.ui.apertures_table.cellWidget(row, 5).clicked.disconnect(self.on_mark_cb_click_table)
            except (TypeError, AttributeError):
                pass
            self.ui.apertures_table.cellWidget(row, 5).clicked.connect(self.on_mark_cb_click_table)

        try:
            self.ui.mark_all_cb.clicked.disconnect(self.on_mark_all_click)
        except (TypeError, AttributeError):
            pass
        self.ui.mark_all_cb.clicked.connect(self.on_mark_all_click)

    def ui_disconnect(self):
        for row in range(self.ui.apertures_table.rowCount()):
            try:
                self.ui.apertures_table.cellWidget(row, 5).clicked.disconnect()
            except (TypeError, AttributeError):
                pass

        try:
            self.ui.mark_all_cb.clicked.disconnect(self.on_mark_all_click)
        except (TypeError, AttributeError):
            pass

    def on_properties(self, state):
        if state:
            self.ui.info_frame.show()
        else:
            self.ui.info_frame.hide()
            return

        self.ui.treeWidget.clear()
        self.add_properties_items(obj=self, treeWidget=self.ui.treeWidget)

        # make sure that the FCTree widget columns are resized to content
        self.ui.treeWidget.resize_sig.emit()

    def on_generate_buffer(self):
        self.app.inform.emit('[WARNING_NOTCL] %s...' % _("Buffering solid geometry"))

        def buffer_task():
            with self.app.proc_container.new('%s ...' % _("Buffering")):
                with history_of(self.app).snapshot(_("Buffer"), [self]):
                    output = self.app.pool.apply_async(self.buffer_handler, args=([self.solid_geometry]))
                    self.solid_geometry = output.get()

                self.app.inform.emit('[success] %s' % _("Done."))
                self.plot_single_object.emit()

        self.app.worker_task.emit({'fcn': buffer_task, 'params': []})

    @staticmethod
    def buffer_handler(geo):
        new_geo = geo
        if isinstance(new_geo, list):
            new_geo = MultiPolygon(new_geo)

        new_geo = new_geo.buffer(0.0000001)
        new_geo = new_geo.buffer(-0.0000001)

        return new_geo

    def on_generate_non_copper_button_click(self, *args):
        self.app.defaults.report_usage("GerberObject.on_generate_non_copper_button_click")

        self.read_form()
        name = self.obj_options["name"] + "_noncopper"

        def geo_init(geo_obj, app_obj):
            assert geo_obj.kind == 'geometry', "Expected a Geometry object got %s" % type(geo_obj)

            if isinstance(self.solid_geometry, list):
                try:
                    self.solid_geometry = MultiPolygon(self.solid_geometry)
                except Exception:
                    self.solid_geometry = unary_union(self.solid_geometry)

            bounding_box = self.solid_geometry.envelope.buffer(float(self.obj_options["noncoppermargin"]))
            if not self.obj_options["noncopperrounded"]:
                bounding_box = bounding_box.envelope
            non_copper_diff = bounding_box.difference(self.solid_geometry)
            flattened_non_copper = flatten_shapely_geometry(non_copper_diff)

            if (flattened_non_copper is None or
                    (not isinstance(flattened_non_copper, list) and flattened_non_copper.is_empty)):
                app_obj.inform.emit("[ERROR_NOTCL] %s" % _("Operation could not be done."))
                return "fail"
            geo_obj.solid_geometry = flattened_non_copper

        self.app.app_obj.new_object("geometry", name, geo_init)

    def on_generatebb_button_click(self, *args):
        self.app.defaults.report_usage("gerber_on_generatebb_button")
        self.read_form()
        name = self.obj_options["name"] + "_bbox"

        def geo_init(geo_obj, app_obj):
            assert geo_obj.kind == 'geometry', "Expected a Geometry object got %s" % type(geo_obj)

            if isinstance(self.solid_geometry, list):
                try:
                    self.solid_geometry = MultiPolygon(self.solid_geometry)
                except Exception:
                    self.solid_geometry = unary_union(self.solid_geometry)

            distance = float(self.obj_options["bboxmargin"])

            # Bounding box with rounded corners
            if distance >= 0:
                if isinstance(self.solid_geometry, Polygon):
                    bounding_box = self.solid_geometry.buffer(distance).exterior
                elif isinstance(self.solid_geometry, MultiPolygon):
                    try:
                        bounding_box = self.solid_geometry.buffer(distance).exterior
                    except Exception:
                        bounding_box = self.solid_geometry.envelope.buffer(distance)
                else:
                    bounding_box = self.solid_geometry.envelope.buffer(distance)
            else:
                if isinstance(self.solid_geometry, Polygon):
                    bounding_box = self.solid_geometry.buffer(abs(distance*2)).interiors
                elif isinstance(self.solid_geometry, MultiPolygon):
                    try:
                        bounding_box = self.solid_geometry.buffer(abs(distance*2)).interiors
                    except Exception:
                        bounding_box = self.solid_geometry.envelope.buffer(abs(distance*2)).interiors
                else:
                    bounding_box = self.solid_geometry.envelope.buffer(abs(distance*2)).interiors
                bounding_box = unary_union(bounding_box).buffer(-distance).exterior

            if not self.obj_options["bboxrounded"]:  # Remove rounded corners
                bounding_box = bounding_box.envelope

            if bounding_box is None or bounding_box.is_empty:
                app_obj.inform.emit("[ERROR_NOTCL] %s" % _("Operation could not be done."))
                return "fail"

            bounding_box_flattened = flatten_shapely_geometry(bounding_box)
            geo_obj.solid_geometry = bounding_box_flattened

        self.app.app_obj.new_object("geometry", name, geo_init)

    def isolate(self, iso_type=None, geometry=None, dia=None, passes=None, overlap=None, outname=None, combine=None,
                milling_type=None, plot=True, clear_leftovers=None, boundary=None, trace_boundary=False,
                pad_passes=None, tool_shape=None, vtipdia=None, vtipangle=None, cutz=None, simplify=None,
                except_geo=None, rest=False):
        """
        Creates an isolation routing geometry object in the project.

        :param iso_type:        type of isolation to be done: 0 = exteriors, 1 = interiors and 2 = both
        :param geometry:        specific geometry to isolate
        :param dia:             Tool diameter; negative: the passes are inside the copper features (the diameter
                                stored in the tool is positive). A list of diameters with rest = True. Not used for a
                                V-shaped tool (only its sign is)
        :param passes:          Number of tool widths to cut
        :param overlap:         Overlap between passes in percent of tool diameter
        :param outname:         Base name of the output object; the passes that are not combined get _<pass number>
        :param combine:         Boolean: if to combine passes in one resulting object in case of multiple passes
        :param milling_type:    type of milling: 'cl' climb or 'cv' conventional
        :param plot: Boolean:   if to plot the resulting geometry object
        :param clear_leftovers: Boolean: add paths that clear the copper left between the passes where the passes of
                                2 features meet (camlib.isolation_leftover_paths()); full isolation with combined passes
        :param boundary:        Polygon / MultiPolygon (e.g. the board area, camlib.outline_area()) or None. The paths
                                are clipped to it, and the passes stop at the first one that is completely outside
                                it (the next ones, further out, would be outside too)
        :param trace_boundary:  Boolean: also add the outline of the boundary as a path (once)
        :param pad_passes:      number of extra passes around the pads only, after the last pass
        :param tool_shape:      index of the tool tip shape (tools_iso_tool_shape); 5 is the V shape
        :param vtipdia:         tip diameter of the V-shaped tool
        :param vtipangle:       tip angle of the V-shaped tool
        :param cutz:            cut depth, for the effective diameter of the V-shaped tool
        :param simplify:        simplification tolerance of the paths, 0 = off, None = the Isolation preferences
        :param except_geo:      geometry in which no isolation path is left
        :param rest:            Boolean: rest machining with all the diameters of dia, the biggest first; one object
                                with all the tools results
        :return:                'fail' if no object could be made, else None
        """
        # imported here: the plugin package imports the objects
        from appPlugins.ToolIsolation import IsolationGen
        from appPlugins.ToolIsolation.Isolation import ToolIsolation, pad_solids

        work_geo = self.solid_geometry if geometry is None else geometry

        if passes is None:
            passes = int(self._app_option("tools_iso_passes"))
        if overlap is None:
            overlap = float(self._app_option("tools_iso_overlap"))
        overlap /= 100.0
        combine = self._app_option("tools_iso_combine_passes") if combine is None else bool(combine)
        if clear_leftovers is None:
            clear_leftovers = self._app_option("tools_iso_clear_leftovers", False)
        if milling_type is None:
            milling_type = self._app_option("tools_iso_milling_type")
        if pad_passes is None:
            pad_passes = int(self._app_option("tools_iso_pad_passes", 0))
        simplify = 0.0 if simplify is None else float(simplify)
        if iso_type is None:
            iso_t = {'ext': 0, 'int': 1, 'full': 2}.get(self._app_option("tools_iso_isotype", 'full'), 2)
        else:
            iso_t = iso_type
        if tool_shape is None:
            tool_shape = int(self._app_option("tools_iso_tool_shape"))
        tool_shape = int(tool_shape)
        if vtipdia is None:
            vtipdia = float(self._app_option("tools_iso_vtipdia"))
        if vtipangle is None:
            vtipangle = float(self._app_option("tools_iso_vtipangle"))
        if cutz is None:
            cutz = float(self._app_option("tools_iso_cutz"))

        # the tool diameters, signed: a negative one isolates inside the copper
        if tool_shape == ToolIsolation.V_SHAPE_INDEX:
            effective = IsolationGen.v_effective_diameter({
                'tools_mill_vtipdia': vtipdia, 'tools_mill_vtipangle': vtipangle, 'tools_mill_cutz': cutz})
            if effective is None or effective <= 0:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The effective diameter of the V-shaped tool is not valid."))
                return 'fail'
            sign = -1 if (dia is not None and (min(dia) if isinstance(dia, (list, tuple)) else dia) < 0) else 1
            dias = [sign * effective]
        else:
            if dia is None:
                dia = float(self._app_option("tools_iso_tooldia"))
            dias = [float(d) for d in dia] if isinstance(dia, (list, tuple)) else [float(dia)]
        if not rest:
            dias = dias[:1]
        # rest machining: the biggest tool first
        dias.sort(key=abs, reverse=True)

        base_name = self.obj_options["name"]
        iso_stem = base_name + ("_ext_iso" if iso_t == 0 else "_int_iso" if iso_t == 1 else "_iso")
        # climb milling (M3 spindle): the tool goes clockwise around the features; conventional reverses it
        mill_dir = 0 if milling_type == 'cl' else 1

        def tool_data(app_obj, tooldia):
            """The data of a tool of the result: the preferences, and what this isolation was made with."""
            data = {}
            for opt_key in app_obj.options:
                if opt_key.find('geometry' + "_") == 0:
                    data[opt_key[len('geometry') + 1:]] = self._app_option(opt_key)
                if opt_key.find('tools_mill' + "_") == 0 or opt_key.find('tools_iso' + "_") == 0:
                    data[opt_key] = self._app_option(opt_key)
            data.update({
                'tools_mill_tooldia':       float(tooldia),
                'tools_mill_job_type':      2,  # Isolation
                'tools_mill_tool_shape':    tool_shape,
                'tools_mill_vtipdia':       vtipdia,
                'tools_mill_vtipangle':     vtipangle,
                'tools_mill_cutz':          cutz,
                'tools_iso_milling_type':   milling_type,
                'tools_iso_isotype':        {0: 'ext', 1: 'int', 2: 'full'}.get(iso_t, 'full'),
                'tools_iso_passes':         passes,
                'tools_iso_overlap':        overlap * 100.0,
                'tools_iso_pad_passes':     pad_passes,
                'tools_iso_combine_passes': combine,
                'tools_iso_rest':           bool(rest),
                'tools_iso_simplification': simplify > 0,
                'tools_iso_simplification_tol': simplify if simplify > 0 else data.get(
                    'tools_iso_simplification_tol'),
                'tools_iso_isoexcept':      except_geo is not None,
            })
            return data

        def finish(paths):
            """Simplification and the excluded area, as the plugin does it."""
            paths = flatten_shapely_geometry(paths)
            if simplify > 0:
                paths = [g.simplify(tolerance=simplify) for g in paths if not g.is_empty]
                if boundary is not None:
                    paths = clip_paths_to_area(paths, boundary)
            if except_geo is not None:
                paths = IsolationGen.area_subtraction(paths, flatten_shapely_geometry(except_geo))
            return flatten_shapely_geometry(paths)

        def make_object(name, tools):
            """tools: [(positive tool diameter, its paths)]. Returns the result of new_object()."""
            def iso_init(geo_obj, app_obj):
                geo_obj.obj_options["tools_mill_tooldia"] = str(tools[-1][0])
                geo_obj.tool_type = tool_shape
                geo_obj.multigeo = True
                geo_obj.solid_geometry = [g for _tooldia, paths in tools for g in paths]
                if not any(g is not None and not g.is_empty for g in geo_obj.solid_geometry):
                    raise ValidationError("Empty Geometry", None)

                geo_obj.tools = {}
                for nr, (tooldia, paths) in enumerate(tools, start=1):
                    data = tool_data(app_obj, tooldia)
                    data['name'] = name
                    geo_obj.tools[nr] = {'tooldia': tooldia, 'data': data, 'solid_geometry': paths}

                if plot:
                    app_obj.inform.emit('[success] %s: %s' % (_("Isolation geometry created"),
                                                              geo_obj.obj_options["name"]))

            return self.app.app_obj.new_object("geometry", name, iso_init, plot=plot)

        def report_boundary(name, made, wanted):
            report = boundary_passes_message(name, made, wanted)
            self.app.log.info("Isolation boundary: " + report)
            self.app.inform.emit(report)
            return made == 0

        def pad_rings(envelopes, tooldia, first_pass):
            if pad_passes <= 0:
                return []
            # only the pads of the isolated geometry (a clear (LPC) flash has no 'solid': it is not a pad)
            pads = pad_solids(self, geometry)
            if not pads:
                return []
            rings = IsolationGen.pad_passes(pads, envelopes, abs(tooldia), first_pass, pad_passes, overlap,
                                            self.app.options, negative_dia=tooldia < 0, units=self.units)
            rings = flatten_shapely_geometry(rings)
            return clip_paths_to_area(rings, boundary) if boundary is not None else rings

        # the outline of the boundary, traced once, out of the excluded area
        trace_geo = []
        if boundary is not None and trace_boundary:
            trace_geo = area_outline_paths(boundary)
            if except_geo is not None:
                trace_geo = IsolationGen.area_subtraction(trace_geo, flatten_shapely_geometry(except_geo))
                trace_geo = flatten_shapely_geometry(trace_geo)

        # ------------------------------------------------------------------------------------------ rest machining
        if rest:
            name = iso_stem + "_rest" if outname is None else outname
            polygons = [g for g in flatten_shapely_geometry(work_geo) if isinstance(g, Polygon)]
            forced_rest = bool(self._app_option("tools_iso_force", True))
            tools = []
            traced = False
            for tooldia in dias:
                solid_geo, polygons = IsolationGen.generate_rest_geometry(
                    polygons, abs(tooldia), passes, overlap, mill_dir, env_iso_type=iso_t,
                    negative_dia=tooldia < 0, forced_rest=forced_rest)
                if isinstance(solid_geo, str):
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                    return 'fail'
                if boundary is not None:
                    solid_geo = clip_paths_to_area(solid_geo, boundary)
                # the boundary is traced once, with the first tool that has paths inside it
                solid_geo = finish(solid_geo)
                if trace_geo and solid_geo and not traced:
                    solid_geo += trace_geo
                    traced = True
                if solid_geo:
                    tools.append((abs(tooldia), solid_geo))
                if not polygons:
                    break
            if not tools:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                return 'fail'
            if polygons:
                self.app.inform.emit('[WARNING] %s' % _("Partial failure. The geometry was processed with all tools.\n"
                                                        "But there are still not-isolated geometry elements. "
                                                        "Try to include a tool with smaller diameter."))
            return make_object(name, tools)

        # ------------------------------------------------------------------------------------------ the passes
        tooldia = dias[0]
        tool_dia = abs(tooldia)
        # the copper left between the passes is found from the complete passes of the full isolation, outwards
        use_leftovers = bool(clear_leftovers) and combine and iso_t == 2 and tooldia > 0
        iso_offsets = [tooldia * ((2 * nr + 1) / 2.0) - (nr * overlap * tooldia) for nr in range(passes)]
        # the passes are buffered ahead, in parallel threads
        buffers = IsolationGen.pass_buffers(self, iso_offsets, self.app, geometry=work_geo)

        pass_paths = []     # (pass number, its paths)
        envelopes = []
        leftover_geo = []
        prev_geom = None
        # the number of passes up to the last one with paths inside the boundary
        passes_made = 0 if boundary is not None else passes
        for nr_pass in range(passes):
            iso_offset = iso_offsets[nr_pass]
            geom = self.generate_envelope(iso_offset, mill_dir, geometry=work_geo, env_iso_type=iso_t,
                                          nr_passes=nr_pass, buffers=buffers)
            if isinstance(geom, str):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                return 'fail'

            envelope = geom
            if boundary is not None:
                geom = clip_paths_to_area(geom, boundary)
                if not geom:
                    # a later pass can still reach an area of the boundary that is not connected to this one
                    if isinstance(boundary, MultiPolygon) and not (
                            iso_offset > 0 and pass_covers_area(envelope, boundary)):
                        prev_geom = None
                        continue
                    # this pass is completely outside the boundary, and so are the next ones
                    break
                passes_made = nr_pass + 1
            pass_paths.append((nr_pass, flatten_shapely_geometry(geom)))
            envelopes += flatten_shapely_geometry(envelope)

            if use_leftovers:
                if prev_geom is not None:
                    quad_segs = gerber_quad_segs(self.app.options, tool_dia / 2.0, self.units)
                    leftover_geo += isolation_leftover_paths(prev_geom, envelope, tool_dia / 2.0, quad_segs,
                                                             invert=mill_dir)
                prev_geom = envelope

        if boundary is not None:
            report_name = (iso_stem if outname is None else outname) if combine else base_name
            if report_boundary(report_name, passes_made, passes):
                return 'fail'

        pad_geo = pad_rings(envelopes, tooldia, passes_made)

        if combine:
            name = iso_stem if outname is None else outname
            if use_leftovers:
                self.app.log.debug("GerberObject.isolate() -> %d paths clear the leftovers" % len(leftover_geo))
                leftover_geo = clip_paths_to_area(leftover_geo, boundary) if boundary is not None else leftover_geo
            paths = [g for _nr, geo in pass_paths for g in geo] + pad_geo + leftover_geo
            return make_object(name, [(tool_dia, finish(paths) + trace_geo)])

        result = None
        for position, (nr_pass, geo) in enumerate(pass_paths):
            if passes > 1:
                name = "%s_%d" % (outname, nr_pass + 1) if outname is not None else iso_stem + str(nr_pass + 1)
            else:
                name = iso_stem if outname is None else outname
            paths = list(geo)
            # the pad rings come after the last pass
            if position == len(pass_paths) - 1:
                paths += pad_geo
            paths = finish(paths)
            # the boundary is traced once, with the first pass that has paths inside it
            if position == 0:
                paths += trace_geo
            if make_object(name, [(tool_dia, paths)]) == 'fail':
                result = 'fail'
        return result

    def generate_envelope(self, offset, invert, geometry=None, env_iso_type=2, nr_passes=0, buffers=None):
        # isolation_geometry() gives the polygons with the exteriors clockwise and the holes counter-clockwise: the
        # copper features are on the right of the tool, which is climb milling with a spindle that turns clockwise (M3).
        # invert reverses all the rings, for conventional milling.

        try:
            geom = self.isolation_geometry(offset, geometry=geometry, iso_type=env_iso_type, passes=nr_passes,
                                           buffers=buffers)
        except Exception as e:
            self.app.log.error('GerberObject.isolate().generate_envelope() --> %s' % str(e))
            return 'fail'

        # e.g. a type of isolation that does not exist: there is no geometry to orient
        if isinstance(geom, str):
            return 'fail'

        if invert:
            try:
                pl = []
                w_geo = geom.geoms if isinstance(geom, (MultiPolygon, MultiLineString)) else geom
                for p in w_geo:
                    if p is not None:
                        if isinstance(p, Polygon):
                            pl.append(milling_orient(p, conventional=True))
                        elif isinstance(p, LinearRing):
                            pl.append(Polygon(p.coords[::-1]))
                geom = MultiPolygon(pl)
            except TypeError:
                if isinstance(geom, Polygon) and geom is not None:
                    geom = milling_orient(geom, conventional=True)
                elif isinstance(geom, LinearRing) and geom is not None:
                    geom = Polygon(geom.coords[::-1])
                else:
                    self.app.log.debug("GerberObject.isolate().generate_envelope() Error --> Unexpected Geometry %s" %
                                       type(geom))
            except Exception as e:
                self.app.log.error("GerberObject.isolate().generate_envelope() Error --> %s" % str(e))
                return 'fail'
        return geom

    def on_plot_cb_click(self, *args):
        if self.muted_ui:
            return
        self.read_form_item('plot')
        # a toggle only changes the visibility; a new plot of a big object blocks the UI
        if not self.show_plotted_shapes(self.obj_options['plot']):
            self.plot()

    def on_solid_cb_click(self, *args):
        if self.muted_ui:
            return
        self.read_form_item('solid')
        self.plot()

    def on_multicolored_cb_click(self, *args):
        if self.muted_ui:
            return
        self.read_form_item('multicolored')
        self.plot()

    def on_follow_cb_click(self):
        if self.muted_ui:
            return
        self.read_form_item('follow')
        self.plot()

    def on_aperture_table_visibility_change(self):
        if self.ui.aperture_table_visibility_cb.isChecked():
            # add the shapes storage for marking apertures
            for ap_code in self.tools:
                self.mark_shapes_storage[ap_code] = []

            self.ui.apertures_table.setVisible(True)
            self.mark_shapes.enabled = True

            self.ui.mark_all_cb.setVisible(True)
            self.ui.mark_all_cb.setChecked(False)
            self.build_ui()
        else:
            self.ui.apertures_table.setVisible(False)
            self.ui.mark_all_cb.setVisible(False)

            # on hide disable all mark plots
            try:
                for row in range(self.ui.apertures_table.rowCount()):
                    self.ui.apertures_table.cellWidget(row, 5).set_value(False)
                self.clear_plot_apertures()
                self.mark_shapes.enabled = False
            except Exception as e:
                self.app.log.error(" GerberObject.on_aperture_visibility_changed() --> %s" % str(e))

    def convert_units(self, units):
        """
        Converts the units of the object by scaling dimensions in all geometry
        and options.

        :param units: Units to which to convert the object: "IN" or "MM".
        :type units: str
        :return: None
        """

        # units conversion to get a conversion should be done only once even if we found multiple
        # units declaration inside a Gerber file (it can happen to find also the obsolete declaration)
        if self.conversion_done is True:
            self.app.log.debug("Gerber units conversion cancelled. Already done.")
            return

        self.app.log.debug("FlatCAMObj.GerberObject.convert_units()")

        Gerber.convert_units(self, units)

        # self.obj_options['isotd_list'] = float(self.obj_options['isotd_list']) * factor
        # self.obj_options['bboxmargin'] = float(self.obj_options['bboxmargin']) * factor

    def plot(self, kind=None, **kwargs):
        """

        :param kind:    Not used, for compatibility with the plot method for other objects
        :param kwargs:  Color and face_color, visible
        :return:
        """
        # self.app.log.debug(str(inspect.stack()[1][3]) + " --> GerberObject.plot()")

        # Does all the required setup and returns False
        # if the 'ptint' option is set to False.
        if not FlatCAMObj.plot(self):
            return

        if 'color' in kwargs:
            color = kwargs['color']
        else:
            color = self.outline_color

        if 'face_color' in kwargs:
            face_color = kwargs['face_color']
        else:
            face_color = self.fill_color

        if 'visible' not in kwargs:
            visible = self.obj_options['plot']
        else:
            visible = kwargs['visible']

        # if the Follow Geometry checkbox is checked then plot only the follow geometry
        if self.obj_options['follow']:
            geometry = self.follow_geometry
        else:
            geometry = self.solid_geometry

        random_color = self.random_color
        # one lookup per plot, not one per polygon
        no_outline = self._app_option("gerber_plot_line_enable") is False

        try:
            plot_geometry = geometry.geoms if isinstance(geometry, (MultiPolygon, MultiLineString)) else geometry
            try:
                batch = []
                for g in plot_geometry:
                    if self.obj_options["solid"]:
                        used_color = color
                        used_face_color = random_color() if self.obj_options['multicolored'] else face_color
                    else:
                        used_color = random_color() if self.obj_options['multicolored'] else 'black'
                        used_face_color = None

                    if no_outline:
                        used_color = None
                    if isinstance(g, (Polygon, LineString)):
                        batch.append({
                            'shape': g, 'color': used_color, 'face_color': used_face_color
                        })
                    elif isinstance(g, LinearRing):
                        batch.append({
                            'shape': LineString(g), 'color': used_color, 'face_color': used_face_color
                        })

                if batch:
                    self.add_shapes_batch(batch, visible=visible)

            except TypeError:
                # Single geometry, not iterable
                if self.obj_options["solid"]:
                    used_color = color
                    used_face_color = random_color() if self.obj_options['multicolored'] else face_color
                else:
                    used_color = random_color() if self.obj_options['multicolored'] else 'black'
                    used_face_color = None

                if no_outline:
                    used_color = None
                if isinstance(plot_geometry, (Polygon, LineString)):
                    self.add_shape(shape=plot_geometry, color=used_color, face_color=used_face_color, visible=visible)
                elif isinstance(plot_geometry, LinearRing):
                    plot_geometry = LineString(plot_geometry)
                    self.add_shape(shape=plot_geometry, color=used_color, face_color=used_face_color, visible=visible)

            if not self.defer_redraw:
                self.shapes.redraw()
            self.mark_full_plot()
        except (ObjectDeleted, AttributeError):
            self.shapes.clear(update=True)
        except Exception as e:
            self.app.log.error("GerberObject.plot() --> %s" % str(e))

    def plot_aperture(self, only_flashes=False, run_thread=False, **kwargs):
        """

        :param only_flashes:    plot only flashed
        :param run_thread:      if True run the aperture plot as a thread in a worker
        :param kwargs:          color, visible, redraw (defaults to True)
        :return:
        """

        # log.debug(str(inspect.stack()[1][3]) + " --> GerberObject.plot_aperture()")

        # Does all the required setup and returns False
        # if the 'ptint' option is set to False.
        # if not FlatCAMObj.plot(self):
        #     return

        # for marking apertures, line color and fill color are the same
        color = kwargs['color'] if 'color' in kwargs else self._app_option('gerber_plot_fill')

        if 'marked_aperture' in kwargs:
            aperture_to_plot_mark = kwargs['marked_aperture']
            if aperture_to_plot_mark is None:
                return
        else:
            return

        if 'visible' not in kwargs:
            visibility = True
        else:
            visibility = kwargs['visible']

        def job_thread(app_obj):
            with self.app.proc_container.new('%s ...' % _("Plotting")):
                try:
                    if aperture_to_plot_mark in app_obj.tools:
                        # an aperture added after build_ui has no storage key yet
                        mark_storage = app_obj.mark_shapes_storage.setdefault(aperture_to_plot_mark, [])
                        batch = []
                        for elem in app_obj.tools[aperture_to_plot_mark]['geometry']:
                            if 'solid' in elem:
                                if only_flashes and not isinstance(elem['follow'], Point):
                                    continue
                                geo = elem['solid']
                                s_geo = geo.geoms if isinstance(geo, (MultiLineString, MultiPolygon)) else geo
                                try:
                                    elements = iter(s_geo)
                                except TypeError:
                                    elements = (s_geo,)
                                batch.extend({'shape': el, 'color': color, 'face_color': color} for el in elements)
                        if batch:
                            if app_obj.deleted:
                                raise ObjectDeleted()
                            mark_storage.extend(app_obj.mark_shapes.add_batch(
                                batch, tolerance=app_obj.drawing_tolerance, layer=0, visible=visibility))
                    if kwargs.get('redraw', True):
                        app_obj.mark_shapes.redraw()
                except (ObjectDeleted, AttributeError):
                    app_obj.clear_plot_apertures()
                except Exception as e:
                    self.app.log.error("GerberObject.plot_aperture() --> %s" % str(e))

        if run_thread:
            self.app.worker_task.emit({'fcn': job_thread, 'params': [self]})
        else:
            job_thread(self)

    def clear_plot_apertures(self, aperture='all'):
        """

        :param aperture: aperture for which to clear the mark shapes
        :type aperture: str, int
        :return:
        """

        if self.mark_shapes_storage:
            if aperture == 'all':
                self.mark_shapes.clear(update=True)
            else:
                for shape_key in self.mark_shapes_storage.get(aperture, []):
                    try:
                        self.mark_shapes.remove(shape_key)
                    except Exception as e:
                        self.app.log.error("GerberObject.clear_plot_apertures() -> %s" % str(e))

                self.mark_shapes_storage[aperture] = []
                self.mark_shapes.redraw()

    def clear_mark_all(self):
        self.ui.mark_all_cb.set_value(False)
        self.marked_rows[:] = []

    def on_mark_cb_click_table(self):
        """
        Will mark aperture geometries on canvas or delete the markings depending on the checkbox state
        :return:
        """

        self.ui_disconnect()
        try:
            cw = self.sender()
            cw_index = self.ui.apertures_table.indexAt(cw.pos())
            cw_row = cw_index.row()
        except AttributeError:
            cw_row = 0
        except TypeError:
            return

        try:
            aperture = int(self.ui.apertures_table.item(cw_row, 1).text())
        except AttributeError:
            self.ui_connect()
            return

        # keep one entry per table row, the table rebuild indexes this list by row
        row_count = self.ui.apertures_table.rowCount()
        if len(self.marked_rows) != row_count:
            self.marked_rows[:] = [False] * row_count
        self.marked_rows[cw_row] = bool(self.ui.apertures_table.cellWidget(cw_row, 5).isChecked())

        if self.ui.apertures_table.cellWidget(cw_row, 5).isChecked():
            # self.plot_aperture(color='#2d4606bf', marked_aperture=aperture, visible=True)
            color = self._app_option('global_sel_draw_color')
            color = (color + 'AF') if len(color) == 7 else (color[:-2] + 'AF')
            self.plot_aperture(color=color, marked_aperture=aperture, visible=True, run_thread=True)
        else:
            self.clear_plot_apertures(aperture=aperture)

        # make sure that the Mark All is disabled if one of the row mark's are disabled and
        # if all the row mark's are enabled also enable the Mark All checkbox
        cb_cnt = 0
        total_row = self.ui.apertures_table.rowCount()
        for row in range(total_row):
            if self.ui.apertures_table.cellWidget(row, 5).isChecked():
                cb_cnt += 1
            else:
                cb_cnt -= 1
        if cb_cnt < total_row:
            self.ui.mark_all_cb.setChecked(False)
        else:
            self.ui.mark_all_cb.setChecked(True)
        self.ui_connect()

    def on_mark_all_click(self):
        self.ui_disconnect()
        mark_all = self.ui.mark_all_cb.isChecked()
        # update the mark_rows list: one entry per table row
        self.marked_rows[:] = [mark_all] * self.ui.apertures_table.rowCount()
        for row in range(self.ui.apertures_table.rowCount()):
            mark_cb = self.ui.apertures_table.cellWidget(row, 5)
            mark_cb.setChecked(mark_all)

        if mark_all:
            color = self._app_option('global_sel_draw_color')
            color = (color + 'AF') if len(color) == 7 else (color[:-2] + 'AF')
            for aperture in self.tools:
                self.plot_aperture(color=color, marked_aperture=aperture, visible=True, redraw=False)
            self.mark_shapes.redraw()
            # HACK: enable/disable the grid for a better look
            self.app.ui.grid_snap_btn.trigger()
            self.app.ui.grid_snap_btn.trigger()
        else:
            self.clear_plot_apertures()

        self.ui_connect()

    def dxf_layers(self):
        """
        DXF layers. Mode 'object': one layer "<name>" with the solid geometry (outline); in centerline mode the
        tracks go to one layer "<name>_W<width>" per distinct aperture size, flashes and regions stay on "<name>".
        Mode 'tool': one layer "<name>_D<code>" per aperture (centerline tracks drawn with the aperture size as
        width, their flashes/regions on "<name>_D<code>_flash"). Centerline mode puts clear geometry on "<name>_clear".
        An object without aperture geometry falls back to solid_geometry on one layer.
        Outline mode cuts each aperture with solid_geometry, so the clear geometry stays subtracted.
        """
        name = self.obj_options.get('name') or 'gerber'
        color = self._app_option(self.plot_default_line_option or 'gerber_plot_line', None)
        per_tool = self._app_option('geometry_dxf_layers', 'tool') != 'object'
        centerline = self._app_option('geometry_dxf_gerber_traces', 'outline') == 'centerline'
        tools = self.tools or {}
        if not any(tool.get('geometry') for tool in tools.values()) or not (per_tool or centerline):
            return [{'name': name, 'geometry': self.solid_geometry, 'color': color}]
        copper = None if centerline else unary_union(flatten_shapely_geometry(self.solid_geometry))

        def usable(geo):
            return geo is not None and not (hasattr(geo, 'is_empty') and geo.is_empty) and \
                not (isinstance(geo, (list, tuple)) and not geo)

        layers = []
        clear = []
        flash_all = []
        widths = {}
        for code, tool in tools.items():
            track = tool.get('type') in ('C', 'R', 'O')
            width = tool.get('size') or min(tool.get('width') or 0.0, tool.get('height') or 0.0) or None
            solids, lines = [], []
            for element in tool.get('geometry') or []:
                if usable(element.get('clear')):
                    clear.append(element['clear'])
                follow, solid = element.get('follow'), element.get('solid')
                if centerline and track and width and follow is not None and \
                        getattr(follow, 'geom_type', '') in ('LineString', 'MultiLineString') and usable(follow):
                    lines.append(follow)
                elif usable(solid):
                    solids.append(solid)
            if copper is not None and solids:
                solids = [unary_union(solids).intersection(copper)]
            if per_tool:
                if lines:
                    layers.append({'name': "%s_D%s" % (name, code), 'geometry': lines, 'color': color, 'width': width})
                if solids:
                    suffix = "_flash" if lines else ""
                    layers.append({'name': "%s_D%s%s" % (name, code, suffix), 'geometry': solids, 'color': color})
            else:
                flash_all += solids
                if lines:
                    widths.setdefault(width, []).extend(lines)
        if not per_tool:
            if flash_all or not widths:
                layers.append({'name': name, 'geometry': flash_all, 'color': color})
            for width, lines in widths.items():
                layers.append({'name': "%s_W%g" % (name, width), 'geometry': lines, 'color': color, 'width': width})
        if centerline and clear:
            layers.append({'name': "%s_clear" % name, 'geometry': clear, 'color': color})
        return layers

    def export_dxf(self):
        """Return a complete DXF drawing of the object on layers, or None on failure."""
        try:
            return build_dxf(self.dxf_layers(), self.app.app_units, self._app_option('geometry_dxf_format'),
                             fit_arcs=bool(self._app_option('geometry_dxf_arcs', True)))
        except Exception as e:
            self.app.log.error("%s.export_dxf() -> %s" % (type(self).__name__, str(e)))
            return None

    def export_gerber(self, whole, fract, g_zeros='L', factor=1):
        """
        Creates a Gerber file content to be exported to a file.

        :param whole: how many digits in the whole part of coordinates
        :param fract: how many decimals in coordinates
        :param g_zeros: type of the zero suppression used: LZ or TZ; string
        :param factor: factor to be applied onto the Gerber coordinates
        :return: Gerber_code
        """
        self.app.log.debug("GerberObject.export_gerber() --> Generating the Gerber code from the selected Gerber file")

        # 'T': the whole part is left padded with zeros to 'whole' digits and the sign stays in front of the padding
        # (the forced '+' sign makes the padding width the same for positive and negative values).
        # 'L': no padding; with the leading zeros omitted the reader takes the last 'fract' digits as the decimals,
        # so trailing zeros added here would multiply the coordinate.
        # A whole path is formatted with one '%' operation; the decimal dot and the '+' sign are removed after.
        if g_zeros == 'T':
            num_fmt = '%%+0%d.%df' % (whole + 1 + (fract + 1 if fract > 0 else 0), fract)
        else:
            num_fmt = '%%.%df' % fract
        move_fmt = 'X%sY%sD02*\n' % (num_fmt, num_fmt)
        draw_fmt = 'X%sY%sD01*\n' % (num_fmt, num_fmt)
        flash_fmt = 'X%sY%sD03*\n' % (num_fmt, num_fmt)

        def clean(text):
            text = text.replace('.', '')
            return text.replace('+', '') if g_zeros == 'T' else text

        def flash_code(point):
            return clean(flash_fmt % (point.x * factor, point.y * factor))

        def path_code(coords, skip_repeated=True):
            # first command is a move with pen-up D02 at the beginning of the geo
            coords = np.asarray(coords, dtype=float)
            if skip_repeated and len(coords) > 1:
                keep = np.ones(len(coords), dtype=bool)
                keep[1:] = np.any(coords[1:] != coords[:-1], axis=1)
                coords = coords[keep]
            flat = (coords[:, :2] * factor).ravel().tolist()
            return clean((move_fmt + draw_fmt * (len(coords) - 1)) % tuple(flat))

        def region_code(parts, geo_elem, where):
            # the elements of the 0 aperture and of the AM and P apertures are written as regions
            if 'solid' in geo_elem:
                geo = geo_elem['solid']
                if not geo.is_empty and not isinstance(geo, (LineString, MultiLineString, Point)):
                    parts.append('G36*\n')
                    parts.append(path_code(geo.exterior.coords, skip_repeated=False))
                    parts.append('D02*\nG37*\n')

                    clear_list = list(geo.interiors)
                    if clear_list:
                        parts.append('%LPC*%\n')
                        for clear_geo in clear_list:
                            parts.append('G36*\n')
                            parts.append(path_code(clear_geo.coords))
                            parts.append('D02*\nG37*\n')
                        parts.append('%LPD*%\n')
                elif isinstance(geo, (LineString, MultiLineString, Point)):
                    try:
                        if not geo.is_empty:
                            parts.append(flash_code(geo) if isinstance(geo, Point) else path_code(geo.coords))
                    except Exception as e:
                        self.app.log.error(
                            "FlatCAMObj.GerberObject.export_gerber() 'follow' for %s --> %s" % (where, str(e)))
            if 'clear' in geo_elem:
                geo = geo_elem['clear']
                if not geo.is_empty:
                    parts.append('%LPC*%\nG36*\n')
                    parts.append(path_code(geo.exterior.coords))
                    parts.append('D02*\nG37*\n%LPD*%\n')

        # Gerber code is stored here
        gerber_parts = []

        # apertures processing
        try:
            if 0 in self.tools:
                if 'geometry' in self.tools[0]:
                    for geo_elem in self.tools[0]['geometry']:
                        region_code(gerber_parts, geo_elem, '0 aperture')
        except Exception as e:
            self.app.log.error("FlatCAMObj.GerberObject.export_gerber() 0 aperture --> %s" % str(e))

        for apid in self.tools:
            if apid == 0:
                continue
            elif self.tools[apid]['type'] in ['AM', 'P']:
                if 'geometry' in self.tools[apid]:
                    for geo_elem in self.tools[apid]['geometry']:
                        region_code(gerber_parts, geo_elem, 'AM')
            else:
                gerber_parts.append('D%s*\n' % str(apid))
                if 'geometry' in self.tools[apid]:
                    for geo_elem in self.tools[apid]['geometry']:
                        try:
                            if 'follow' in geo_elem:
                                geo = geo_elem['follow']
                                if not geo.is_empty:
                                    if isinstance(geo, Point):
                                        gerber_parts.append(flash_code(geo))
                                    else:
                                        gerber_parts.append(path_code(geo.coords))
                        except Exception as e:
                            self.app.log.error(
                                "FlatCAMObj.GerberObject.export_gerber() 'follow' normal aperture--> %s" % str(e))

                        try:
                            if 'clear' in geo_elem:
                                gerber_parts.append('%LPC*%\n')

                                geo = geo_elem['clear']
                                if not geo.is_empty:
                                    if isinstance(geo, Point):
                                        gerber_parts.append(flash_code(geo))
                                    elif isinstance(geo, Polygon):
                                        gerber_parts.append(path_code(geo.exterior.coords))
                                        for geo_int in geo.interiors:
                                            gerber_parts.append(path_code(geo_int.coords))
                                    else:
                                        gerber_parts.append(path_code(geo.coords))
                                    gerber_parts.append('%LPD*%\n')
                        except Exception as e:
                            self.app.log.error("FlatCAMObj.GerberObject.export_gerber() 'clear' --> %s" % str(e))

        if not self.tools:
            self.app.log.debug("FlatCAMObj.GerberObject.export_gerber() --> Gerber Object is empty: no apertures.")
            return 'fail'

        return ''.join(gerber_parts)

    def from_dict(self, d):
        FlatCAMObj.from_dict(self, d)
        # projects saved by older copper thieving code hold [] for missing record keys
        drop_placeholder_record_keys(self.tools)

    @staticmethod
    def merge(grb_list, grb_final, log=None):
        """
        Merges the geometry of objects in grb_list into
        the geometry of grb_final.

        :param grb_list:    List of GerberObject Objects to join.
        :param grb_final:   Destination GerberObject object.
        :param log:         Optional logger used for warnings
        :return:            None
        """

        if grb_final.solid_geometry is None:
            grb_final.solid_geometry = []
        elif type(grb_final.solid_geometry) is not list:
            grb_final.solid_geometry = [grb_final.solid_geometry]

        follow_final = getattr(grb_final, 'follow_geometry', None)
        if follow_final is None:
            grb_final.follow_geometry = []
        elif type(follow_final) is not list:
            grb_final.follow_geometry = [follow_final]

        if not grb_final.tools:
            grb_final.tools = {}

        for grb in grb_list:

            # Expand lists
            if type(grb) is list:
                GerberObject.merge(grb_list=grb, grb_final=grb_final, log=log)
            else:   # If not list, just append
                for option in grb.obj_options:
                    if option != 'name':
                        try:
                            grb_final.obj_options[option] = grb.obj_options[option]
                        except KeyError:
                            if log:
                                log.warning("Failed to copy option: %s" % option)

                try:
                    for geos in grb.solid_geometry:
                        grb_final.solid_geometry.append(geos)
                except TypeError:
                    grb_final.solid_geometry.append(grb.solid_geometry)

                follow_src = getattr(grb, 'follow_geometry', None)
                if follow_src is not None:
                    if type(follow_src) is list:
                        grb_final.follow_geometry.extend(follow_src)
                    else:
                        grb_final.follow_geometry.append(follow_src)

                for ap in grb.tools:
                    if ap not in grb_final.tools:
                        grb_final.tools[ap] = deepcopy(grb.tools[ap])
                    else:
                        # create a list of integers out of the grb.tools keys and find the max of that value
                        # then, the aperture duplicate is assigned an id value incremented with 1,
                        max_ap = max([int(k) for k in grb_final.tools.keys()]) + 1
                        grb_final.tools[max_ap] = {}
                        grb_final.tools[max_ap]['geometry'] = []

                        for k, v in grb.tools[ap].items():
                            grb_final.tools[max_ap][k] = deepcopy(v)

        # once the aperture metadata of a source can't describe its geometry, it can't describe the merge either
        grb_final.export_as_regions = bool(getattr(grb_final, 'export_as_regions', False) or
                                           any(getattr(grb, 'export_as_regions', False)
                                               for grb in grb_list if not isinstance(grb, list)))

        grb_final.solid_geometry = unary_union(flatten_shapely_geometry(grb_final.solid_geometry))
        grb_final.follow_geometry = unary_union(flatten_shapely_geometry(grb_final.follow_geometry))

    def _after_transform(self):
        """Hook of every geometry transform below: the aperture table has to be redrawn."""
        self.replotApertures.emit()

    def mirror(self, axis, point):
        result = Gerber.mirror(self, axis=axis, point=point)
        self._after_transform()
        return result

    def offset(self, vect):
        result = Gerber.offset(self, vect=vect)
        self._after_transform()
        return result

    def rotate(self, angle, point):
        result = Gerber.rotate(self, angle=angle, point=point)
        self._after_transform()
        return result

    def scale(self, xfactor, yfactor=None, point=None):
        result = Gerber.scale(self, xfactor=xfactor, yfactor=yfactor, point=point)
        self._after_transform()
        return result

    def skew(self, angle_x, angle_y, point):
        result = Gerber.skew(self, angle_x=angle_x, angle_y=angle_y, point=point)
        self._after_transform()
        return result

    def buffer(self, distance, join="mitre", factor=None, only_exterior=False, muted=False):
        result = Gerber.buffer(self, distance=distance, join=join, factor=factor, only_exterior=only_exterior)
        self._after_transform()
        return result

    def serialize(self):
        return {
            "obj_options": self.obj_options,
            "kind": self.kind
        }
