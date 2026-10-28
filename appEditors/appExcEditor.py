# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 8/17/2019                                          #
# MIT Licence                                              #
# ##########################################################

from appHandlers.appHistory import history_of
from camlib import distance, arc, translate_geometry, object_circle_steps, circle_quad_segs

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import Qt
from appEditors.cursor_text import show_cursor_text, hide_cursor_text
from appEditors.exc_plugins.ExcDrillPlugin import ExcDrillEditorTool
from appEditors.exc_plugins.ExcSlotPlugin import ExcSlotEditorTool
from appEditors.exc_plugins.ExcDrillArrayPlugin import ExcDrillArrayEditorTool
from appEditors.exc_plugins.ExcSlotArrayPlugin import ExcSlotArrayEditorTool
from appEditors.exc_plugins.ExcResizePlugin import ExcResizeEditorTool
from appEditors.exc_plugins.ExcCopyPlugin import ExcCopyEditorTool
from appEditors.exc_plugins.ExcHistory import ExcellonHistory, excellon_operation, excellon_session
from appEditors.exc_plugins.ExcProperties import (diagnose_features, feature_properties, filter_features,
                                                native_feature_properties)

from appGUI.GUIElements import FCEntry, FCTable, FCDoubleSpinner, FCButton, FCLabel, FCFrame, GLay, \
    show_selected_rows
from appEditors.appGeoEditor import (FCShapeTool, DrawTool, DrawToolShape, DrawToolUtilityShape, AppGeoEditor,
                                     PICK_TOLERANCE_PX, CLICK_DRAG_THRESHOLD_PX)
from appEditors.appGerberEditor import edit_copy_name, get_finite_bounds

from shapely import LineString, LinearRing, MultiLineString, Polygon, MultiPolygon, Point, STRtree, box
from shapely.geometry.base import BaseGeometry
from shapely.affinity import scale, rotate, translate
# from appCommon.Common import LoudDict

import numpy as np


import math
import logging
from copy import deepcopy

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# property that tags the menu/toolbar actions owned by an AppExcEditor instance
EDITOR_ACTION_PROPERTY = 'exc_editor_action'

# tool key and feature tag of the drills a plugin generated and owns (DblSidedGen.GENERATED): a save must keep it
GENERATED_DRILLS = '_2sided_generated'

# display text of the internal feature types
FEATURE_TYPE_LABELS = {'drill': _('Drill'), 'slot': _('Slot')}


def table_diameter(table, row):
    """The display precision must never change a tool's storage identity."""
    item = table.item(row, 1)
    diameter = item.data(QtCore.Qt.ItemDataRole.UserRole)
    return float(item.text()) if diameter is None else float(diameter)


def replace_exc_records(storage, removed, records):
    """Batch only changed geometry, retaining the original feature order and point index."""
    removed = list(removed)
    old_ids = {id(shape) for shape in removed}
    inserted = [shape for shape in records if id(shape) not in storage.indexes
                or storage.objects[storage.indexes[id(shape)]] is not shape or id(shape) in old_ids]
    storage.replace(removed=removed, inserted=inserted)
    # replace() may compact or append. Reorder its object slots, not its geometry/R-tree entries.
    positions = [storage.indexes[id(shape)] for shape in records]
    points = [storage.obj2points[index] for index in positions]
    storage.objects = list(records)
    storage.indexes = {id(shape): index for index, shape in enumerate(records)}
    storage.obj2points = points
    for index, (previous, point_ids) in enumerate(zip(positions, points)):
        if previous != index:
            for point_id in point_ids:
                storage.points2obj[point_id] = index



def circle_steps_of(editor, radius, edited=None):
    """
    The segments of a full circle of a hole or a slot end of the given radius, the same count that the Excellon object
    gets in create_geometry(): the fixed count of the edited object, or what the preferences set for that radius.

    :param editor:  the Excellon editor (units of the radius, application options)
    :param radius:  the radius of the circle, in the units of the editor
    :param edited:  the edited Excellon object; the object of the editor if None
    """
    edited = edited if edited is not None else getattr(editor, 'edited_obj', None)
    fixed = getattr(edited, '_fixed_steps', None)
    if fixed is not None:
        return int(fixed)
    app = getattr(editor, 'app', None) or getattr(edited, 'app', None)
    units = getattr(editor, 'units', None) or getattr(app, 'app_units', None) or getattr(edited, 'units', None) or 'MM'
    return object_circle_steps(getattr(app, 'options', None), 'excellon', radius, units)


def circle_quad_segs_of(editor, radius, edited=None):
    return circle_quad_segs(circle_steps_of(editor, radius, edited))


class SlotEndpointEditorExc(DrawTool):
    """Drag native centerline endpoints; the capsule is only a preview."""

    def __init__(self, draw_app):
        super().__init__(draw_app)
        self.name = 'slot_endpoint'
        self.drag = None
        self.draw_app.app.inform.emit(_('Drag an endpoint of a selected native slot.'))

    def click(self, point):
        self.clean_up()
        editor = self.draw_app
        if not editor.history.ready() or editor.history.pending is not None or editor.history.suspended:
            return
        radius = float(editor.app.geo_editor.editor_options.get('global_snap_max', 0.05))
        if not math.isfinite(radius) or radius < 0:
            return
        hits = []
        for diameter, storage in editor.storage_dict.items():
            for shape in storage.get_objects():
                native = getattr(shape, 'exc_slot', None)
                if shape not in editor.selected or native is None or \
                        getattr(shape, 'exc_slot_geometry', None) is not shape.geo:
                    continue
                for index, endpoint in enumerate(native):
                    delta = math.dist(point, endpoint)
                    if delta <= radius:
                        hits.append((delta, len(hits), shape, diameter, index, native))
        if hits:
            _, _, shape, diameter, index, native = min(hits, key=lambda hit: hit[:2])
            self.drag = (shape, shape.geo, diameter, index, native,
                         (editor.history.session, editor.history.revision))
            editor.in_action = True

    def utility_geometry(self, data=None):
        if self.drag is None or data is None:
            return None
        shape, geo, diameter, index, native, expected = self.drag
        editor = self.draw_app
        if expected != (editor.history.session, editor.history.revision) or editor.history.pending is not None:
            self.clean_up()
            return None
        endpoints = list(native)
        endpoints[index] = tuple(data)
        if not all(math.isfinite(value) for point in endpoints for value in point) or endpoints[0] == endpoints[1]:
            editor.tool_shape.clear(update=True)
            return None
        return DrawToolUtilityShape(LineString(endpoints).buffer(diameter / 2, circle_quad_segs_of(editor, diameter / 2)))

    def click_release(self, point):
        if self.drag is None:
            return
        shape, geo, diameter, index, native, expected = self.drag
        endpoints = list(native)
        endpoints[index] = tuple(point)
        self.clean_up()
        if shape.geo is not geo:
            return
        self.draw_app.edit_slot_endpoints(shape, endpoints, expected=expected)

    def clean_up(self):
        self.drag = None
        self.draw_app.in_action = False
        self.draw_app.delete_utility_geometry()

    def on_key(self, key):
        if key == 'Escape' or key == Qt.Key.Key_Escape:
            self.clean_up()


class SelectEditorExc(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'drill_select'

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.draw_app = draw_app
        self.storage = self.draw_app.storage_dict
        # self.selected = self.draw_app.selected

        # here we store the selected tools
        self.sel_tools = set()

        # here we store all shapes that were selected so we can search for the nearest to our click location
        self.sel_storage = AppExcEditor.make_storage()

        # make sure that the cursor text from the DrillAdd is deleted
        if self.draw_app.app.plotcanvas.text_cursor.parent:
            self.draw_app.app.plotcanvas.text_cursor.parent = None
            self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        # make sure that the Tools tab is removed
        try:
            self.draw_app.app.ui.notebook.removeTab(2)
        except Exception:
            pass

    def click(self, point):
        key_modifier = QtWidgets.QApplication.keyboardModifiers()

        if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
            mod_key = 'Shift'
        elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
            mod_key = 'Control'
        else:
            mod_key = None

        if mod_key == self.draw_app.app.options.get(
                "global_mselect_key",
                self.draw_app.app.defaults.get(
                    "global_mselect_key",
                    self.draw_app.app.defaults.factory_defaults.get("global_mselect_key")
                )
        ):
            pass
        else:
            self.draw_app.selected = []

    def click_release(self, pos):
        self.draw_app.click_select(pos)
        return ""

    def clean_up(self):
        pass


class DrillAdd(FCShapeTool):
    """
    Resulting type: MultiLineString
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'drill_add'
        self.draw_app = draw_app
        self.app = self.draw_app.app

        self.cursor_data_control = True

        self.selected_dia = None
        try:
            self.draw_app.app.inform.emit(_("Click to place ..."))
            self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]

            # as a visual marker, select again in tooltable the actual tool that we are using
            # remember that it was deselected when clicking on canvas
            table = self.draw_app.ui.tools_table_exc
            row = next(row for row in range(table.rowCount() - 2)
                       if table_diameter(table, row) == self.selected_dia)
            item = table.item(row, 1)
            self.draw_app.ui.tools_table_exc.setCurrentItem(item)
        except KeyError:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("To add a drill first select a tool"))
            self.draw_app.select_tool("drill_select")
            return

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_drill.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.drill_tool = ExcDrillEditorTool(self.app, self.draw_app, plugin_name=_("Drill"))
        self.ui = self.drill_tool.ui
        self.drill_tool.run()

        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))

        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            self.draw_app.draw_utility_geometry(geo=geo)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.drill_tool.length = self.draw_app.last_length
        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Drill"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.points = deepcopy(self.draw_app.clicked_pos) if \
            self.draw_app.clicked_pos and self.draw_app.clicked_pos[0] and self.draw_app.clicked_pos[1] else (0.0, 0.0)
        self.drill_point = None

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_drill)

        self.draw_app.app.inform.emit(_("Click to place ..."))

    def set_plugin_ui(self):
        curr_row = self.draw_app.ui.tools_table_exc.currentRow()
        tool_dia = table_diameter(self.draw_app.ui.tools_table_exc, curr_row)
        self.ui.dia_entry.set_value(tool_dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

    def click(self, point):
        self.drill_point = point
        self.draw_app.last_length = self.drill_tool.length
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))
        self.make()
        return "Done."

    def utility_geometry(self, data=None):
        return DrawToolUtilityShape(self.util_shape(data))

    def util_shape(self, point):
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]

        if point[0] is None and point[1] is None:
            point_x = self.draw_app.x
            point_y = self.draw_app.y
        else:
            point_x = point[0]
            point_y = point[1]

        start_hor_line = ((point_x - (self.selected_dia / 2)), point_y)
        stop_hor_line = ((point_x + (self.selected_dia / 2)), point_y)
        start_vert_line = (point_x, (point_y - (self.selected_dia / 2)))
        stop_vert_line = (point_x, (point_y + (self.selected_dia / 2)))

        return MultiLineString([(start_hor_line, stop_hor_line), (start_vert_line, stop_vert_line)])

    def make(self):
        if self.drill_point is None:
            self.drill_point = (self.draw_app.snap_x, self.draw_app.snap_y)
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # add the point to drills if the diameter is a key in the dict, if not, create it and add the drill location
        # to the value, as a list of itself
        if self.selected_dia in self.draw_app.points_edit:
            self.draw_app.points_edit[self.selected_dia].append(self.drill_point)
        else:
            self.draw_app.points_edit[self.selected_dia] = [self.drill_point]

        self.draw_app.current_storage = self.draw_app.storage_dict[self.selected_dia]
        self.geometry = DrawToolShape(self.util_shape(self.drill_point))
        self.draw_app.in_action = False
        self.complete = True

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        if not self.points:
            self.points = self.draw_app.snap_x, self.draw_app.snap_y
        show_cursor_text(self.draw_app, pos, ref=self.points)

    def on_key(self, key):
        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.drill_tool.length == self.draw_app.last_length:
                    self.drill_tool.length = str(key.name)
                else:
                    self.drill_tool.length = str(self.drill_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.drill_tool.length == self.draw_app.last_length:
                    self.drill_tool.length = chr(key)
                else:
                    self.drill_tool.length = str(self.drill_tool.length) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if self.drill_tool.length != 0:
                target_length = self.drill_tool.length
                if target_length is None:
                    self.drill_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.ui.x_entry.get_value(), self.ui.y_entry.get_value()
                last_pt = self.draw_app.snap_x, self.draw_app.snap_y

                seg_length = math.sqrt((last_pt[0] - first_pt[0])**2 + (last_pt[1] - first_pt[1])**2)
                if seg_length == 0.0:
                    return
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if first_pt != (new_x, new_y):
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    self.add_drill(drill_pos=(new_x, new_y))

        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

    @excellon_operation(_('Add drill'))
    def add_drill(self, drill_pos):
        curr_pos = self.draw_app.snap(drill_pos[0], drill_pos[1])
        self.draw_app.snap_x = curr_pos[0]
        self.draw_app.snap_y = curr_pos[1]

        self.draw_app.on_canvas_click_left_handler(curr_pos)
        if self.draw_app.active_tool.complete:
            self.draw_app.on_shape_complete()

        self.drill_point = curr_pos
        self.draw_app.clicked_pos = curr_pos

    def on_add_drill(self):
        x = self.ui.x_entry.get_value()
        y = self.ui.y_entry.get_value()
        self.add_drill(drill_pos=(x, y))

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class DrillArray(FCShapeTool):
    """
    Resulting type: MultiLineString
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'drill_array'
        self.draw_app = draw_app
        self.app = self.draw_app.app

        self.selected_dia = None

        self.drill_axis = 'X'
        self.drill_array = 'linear'    # 'linear'
        self.drill_array_size = None
        self.drill_pitch = None
        self.drill_linear_angle = None

        self.drill_angle = None
        self.drill_direction = None
        self.drill_radius = None

        self.origin = None
        self.destination = None
        self.flag_for_circ_array = None

        self.last_dx = 0
        self.last_dy = 0

        self.pt = []

        self.cursor_data_control = True

        try:
            self.draw_app.app.inform.emit(_("Click to place ..."))
            self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]
            # as a visual marker, select again in tooltable the actual tool that we are using
            # remember that it was deselected when clicking on canvas
            table = self.draw_app.ui.tools_table_exc
            row = next(row for row in range(table.rowCount() - 2)
                       if table_diameter(table, row) == self.selected_dia)
            item = table.item(row, 1)
            self.draw_app.ui.tools_table_exc.setCurrentItem(item)
        except KeyError:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("To add an Drill Array first select a tool in Tool Table"))
            return

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_drill_array.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.darray_tool = ExcDrillArrayEditorTool(self.app, self.draw_app, plugin_name=_("Drill Array"))
        self.ui = self.darray_tool.ui
        self.darray_tool.run()

        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y), static=True)

        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            self.draw_app.draw_utility_geometry(geo=geo)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Drill Array"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_drill_array)

        if self.ui.array_type_radio.get_value() == 'linear':
            self.draw_app.app.inform.emit(_("Click on target location ..."))
        else:
            self.draw_app.app.inform.emit(_("Click on the circular array Center position"))
        # self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

    def set_plugin_ui(self):
        curr_row = self.draw_app.ui.tools_table_exc.currentRow()
        tool_dia = table_diameter(self.draw_app.ui.tools_table_exc, curr_row)
        self.ui.dia_entry.set_value(tool_dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

        self.ui.array_type_radio.set_value(self.draw_app.last_darray_type)
        self.ui.on_array_type_radio(val=self.ui.array_type_radio.get_value())

        self.ui.array_size_entry.set_value(self.draw_app.last_darray_size)
        self.ui.axis_radio.set_value(self.draw_app.last_darray_lin_dir)
        self.ui.pitch_entry.set_value(self.draw_app.last_darray_pitch)
        self.ui.linear_angle_entry.set_value(self.draw_app.last_darray_lin_angle)
        self.ui.array_dir_radio.set_value(self.draw_app.last_darray_circ_dir)
        self.ui.circular_angle_entry.set_value(self.draw_app.last_darray_circ_angle)
        self.ui.radius_entry.set_value(self.draw_app.last_darray_radius)

    def click(self, point):
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

        if self.drill_array == 'linear':   # 'Linear'
            self.make()
            return

        if self.flag_for_circ_array is None:
            self.draw_app.in_action = True
            self.pt.append(point)

            self.flag_for_circ_array = True
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click on the Circular Array Start position"))
        else:
            self.destination = point
            self.make()
            self.flag_for_circ_array = None

    def set_origin(self, origin):
        self.origin = origin

    def utility_geometry(self, data=None, static=None):
        self.drill_axis = self.ui.axis_radio.get_value()
        self.drill_direction = self.ui.array_dir_radio.get_value()
        self.drill_array = self.ui.array_type_radio.get_value()

        self.drill_array_size = int(self.ui.array_size_entry.get_value())
        self.drill_pitch = float(self.ui.pitch_entry.get_value())
        self.drill_linear_angle = float(self.ui.linear_angle_entry.get_value())
        self.drill_angle = float(self.ui.circular_angle_entry.get_value())

        if self.drill_array == 'linear':   # 'Linear'
            if data[0] is None and data[1] is None:
                dx = self.draw_app.snap_x
                dy = self.draw_app.snap_y
            else:
                dx = data[0]
                dy = data[1]

            geo_list = []
            geo = None
            self.points = [dx, dy]

            for item in range(self.drill_array_size):
                if self.drill_axis == 'X':
                    geo = self.util_shape(((dx + (self.drill_pitch * item)), dy))
                if self.drill_axis == 'Y':
                    geo = self.util_shape((dx, (dy + (self.drill_pitch * item))))
                if self.drill_axis == 'A':
                    x_adj = self.drill_pitch * math.cos(math.radians(self.drill_linear_angle))
                    y_adj = self.drill_pitch * math.sin(math.radians(self.drill_linear_angle))
                    geo = self.util_shape(
                        ((dx + (x_adj * item)), (dy + (y_adj * item)))
                    )

                if static is None or static is False:
                    geo_list.append(translate(geo, xoff=(dx - self.last_dx), yoff=(dy - self.last_dy)))
                else:
                    geo_list.append(geo)
            # self.origin = data

            self.last_dx = dx
            self.last_dy = dy
            return DrawToolUtilityShape(geo_list)
        elif self.drill_array == 'circular':  # 'Circular'
            if data[0] is None and data[1] is None:
                cdx = self.draw_app.snap_x
                cdy = self.draw_app.snap_y
            else:
                cdx = data[0]
                cdy = data[1]

            utility_list = []
            self.points = [cdx, cdy]

            try:
                radius = distance((cdx, cdy), self.origin)
            except Exception:
                radius = 0
            if radius == 0:
                self.draw_app.delete_utility_geometry()
            # no radius to show before the center is clicked: a mouse move over the canvas must not set the
            # radius typed for the 'Add' button to 0
            if self.origin is not None:
                self.ui.radius_entry.set_value(radius)

            if len(self.pt) >= 1 and radius > 0:
                try:
                    if cdx < self.origin[0]:
                        radius = -radius

                    # draw the temp geometry
                    initial_angle = math.asin((cdy - self.origin[1]) / radius)

                    temp_circular_geo = self.circular_util_shape(radius, initial_angle)

                    # draw the line
                    temp_points = [x for x in self.pt]
                    temp_points.append([cdx, cdy])
                    temp_line = LineString(temp_points)

                    for geo_shape in temp_circular_geo:
                        utility_list.append(geo_shape.geo)
                    utility_list.append(temp_line)

                    return DrawToolUtilityShape(utility_list)
                except Exception as e:
                    log.error("DrillArray.utility_geometry -- circular -> %s" % str(e))

    def circular_util_shape(self, radius, angle):
        self.drill_direction = self.ui.array_dir_radio.get_value()
        self.drill_angle = self.ui.circular_angle_entry.get_value()

        circular_geo = []
        if self.drill_direction == 'CW':
            for i in range(self.drill_array_size):
                angle_radians = math.radians(self.drill_angle * i)
                x = self.origin[0] + radius * math.cos(-angle_radians + angle)
                y = self.origin[1] + radius * math.sin(-angle_radians + angle)

                geo_sol = self.util_shape((x, y))
                # geo_sol = affinity.rotate(geo_sol, angle=(math.pi - angle_radians), use_radians=True)

                circular_geo.append(DrawToolShape(geo_sol))
        else:
            for i in range(self.drill_array_size):
                angle_radians = math.radians(self.drill_angle * i)
                x = self.origin[0] + radius * math.cos(angle_radians + angle)
                y = self.origin[1] + radius * math.sin(angle_radians + angle)

                geo_sol = self.util_shape((x, y))
                # geo_sol = affinity.rotate(geo_sol, angle=(angle_radians - math.pi), use_radians=True)

                circular_geo.append(DrawToolShape(geo_sol))

        return circular_geo

    def util_shape(self, point):
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]

        if point[0] is None and point[1] is None:
            point_x = self.draw_app.x
            point_y = self.draw_app.y
        else:
            point_x = point[0]
            point_y = point[1]

        start_hor_line = ((point_x - (self.selected_dia / 2)), point_y)
        stop_hor_line = ((point_x + (self.selected_dia / 2)), point_y)
        start_vert_line = (point_x, (point_y - (self.selected_dia / 2)))
        stop_vert_line = (point_x, (point_y + (self.selected_dia / 2)))

        return MultiLineString([(start_hor_line, stop_hor_line), (start_vert_line, stop_vert_line)])

    def make(self):
        self.geometry = []
        self.complete = False
        if self.drill_array_size < 1 or (self.drill_array != 'linear' and
                (not math.isfinite(self.drill_angle) or abs(self.drill_angle) * self.drill_array_size > 360 or
                 distance(self.destination, self.origin) == 0)):
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % _('Invalid array size, angle or radius.'))
            return 'fail'
        if self.drill_array == 'linear' and (self.drill_axis not in ('X', 'Y', 'A') or
                not math.isfinite(self.drill_pitch) or not all(math.isfinite(value) for value in self.points) or
                (self.drill_axis == 'A' and not math.isfinite(self.drill_linear_angle))):
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % _('Invalid array pitch, direction or angle.'))
            return 'fail'
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]
        self.geometry = []
        geo = None

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # add the point to drills if the diameter is a key in the dict, if not, create it add the drill location
        # to the value, as a list of itself
        if self.selected_dia not in self.draw_app.points_edit:
            self.draw_app.points_edit[self.selected_dia] = []
        for i in range(self.drill_array_size):
            self.draw_app.points_edit[self.selected_dia].append(self.points)

        self.draw_app.current_storage = self.draw_app.storage_dict[self.selected_dia]

        if self.drill_array == 'linear':   # 'Linear'
            for item in range(self.drill_array_size):
                if self.drill_axis == 'X':
                    geo = self.util_shape(((self.points[0] + (self.drill_pitch * item)), self.points[1]))
                if self.drill_axis == 'Y':
                    geo = self.util_shape((self.points[0], (self.points[1] + (self.drill_pitch * item))))
                if self.drill_axis == 'A':
                    x_adj = self.drill_pitch * math.cos(math.radians(self.drill_linear_angle))
                    y_adj = self.drill_pitch * math.sin(math.radians(self.drill_linear_angle))
                    geo = self.util_shape(
                        ((self.points[0] + (x_adj * item)), (self.points[1] + (y_adj * item)))
                    )

                self.geometry.append(DrawToolShape(geo))
        else:   # 'Circular'
            # the angle, size and radius were validated at the top of make()
            radius = distance(self.destination, self.origin)
            if self.destination[0] < self.origin[0]:
                radius = -radius
            initial_angle = math.asin((self.destination[1] - self.origin[1]) / radius)

            circular_geo = self.circular_util_shape(radius, initial_angle)
            self.geometry += circular_geo

        self.complete = True
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        self.draw_app.in_action = False

        self.draw_app.last_darray_type = self.ui.array_type_radio.get_value()
        self.draw_app.last_darray_size = self.ui.array_size_entry.get_value()
        self.draw_app.last_darray_lin_dir = self.ui.axis_radio.get_value()
        self.draw_app.last_darray_circ_dir = self.ui.array_dir_radio.get_value()
        self.draw_app.last_darray_pitch = self.ui.pitch_entry.get_value()
        self.draw_app.last_darray_lin_angle = self.ui.linear_angle_entry.get_value()
        self.draw_app.last_darray_circ_angle = self.ui.circular_angle_entry.get_value()
        self.draw_app.last_darray_radius = self.ui.radius_entry.get_value()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (AttributeError, TypeError):
            pass

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        if not self.points:
            self.points = self.draw_app.snap_x, self.draw_app.snap_y
        show_cursor_text(self.draw_app, pos, length=abs(self.ui.radius_entry.get_value()), ref=self.points)

    def on_key(self, key):
        key_modifier = QtWidgets.QApplication.keyboardModifiers()

        if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
            mod_key = 'Shift'
        elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
            mod_key = 'Control'
        else:
            mod_key = None

        if mod_key is None:
            # Toggle Drill Array Direction
            if key == QtCore.Qt.Key.Key_Space:
                if self.ui.axis_radio.get_value() == 'X':
                    self.ui.axis_radio.set_value('Y')
                elif self.ui.axis_radio.get_value() == 'Y':
                    self.ui.axis_radio.set_value('A')
                elif self.ui.axis_radio.get_value() == 'A':
                    self.ui.axis_radio.set_value('X')

                # ## Utility geometry (animated)
                self.draw_app.update_utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))

            if key == 'C' or key == QtCore.Qt.Key.Key_C:
                self.cursor_data_control = not self.cursor_data_control

            # Jump to coords
            if key == QtCore.Qt.Key.Key_J or key == 'J':
                self.draw_app.app.on_jump_to()

    @excellon_operation(_('Drill array'))
    def add_drill_array(self, array_pos):
        self.drill_radius = self.ui.radius_entry.get_value()

        # the values in the panel now: utility_geometry() read them at the last mouse move over the canvas
        self.drill_axis = self.ui.axis_radio.get_value()
        self.drill_direction = self.ui.array_dir_radio.get_value()
        self.drill_array = self.ui.array_type_radio.get_value()

        self.drill_array_size = int(self.ui.array_size_entry.get_value())
        self.drill_pitch = float(self.ui.pitch_entry.get_value())
        self.drill_linear_angle = float(self.ui.linear_angle_entry.get_value())
        self.drill_angle = float(self.ui.circular_angle_entry.get_value())

        curr_pos = self.draw_app.snap(array_pos[0], array_pos[1])
        self.draw_app.snap_x = curr_pos[0]
        self.draw_app.snap_y = curr_pos[1]

        self.points = [self.draw_app.snap_x, self.draw_app.snap_y]
        self.origin = [self.draw_app.snap_x, self.draw_app.snap_y]
        self.destination = ((self.origin[0] + self.drill_radius), self.origin[1])
        self.flag_for_circ_array = True
        self.make()

        if self.draw_app.current_storage is not None:
            self.draw_app.on_exc_shape_complete(self.draw_app.current_storage)
            self.draw_app.build_ui()

        if self.draw_app.active_tool.complete:
            self.draw_app.on_shape_complete()

        self.draw_app.select_tool("drill_select")

        self.draw_app.clicked_pos = curr_pos

    def on_add_drill_array(self):
        x = self.ui.x_entry.get_value()
        y = self.ui.y_entry.get_value()
        self.add_drill_array(array_pos=(x, y))

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class SlotAdd(FCShapeTool):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'slot_add'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.steps_per_circ = None      # set in util_shape() from the tool diameter
        self.half_height = 0.0
        self.half_width = 0.0

        self.selected_dia = None

        self.cursor_data_control = True

        try:
            self.draw_app.app.inform.emit(_("Click to place ..."))
            self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]

            # as a visual marker, select again in tooltable the actual tool that we are using
            # remember that it was deselected when clicking on canvas
            table = self.draw_app.ui.tools_table_exc
            row = next(row for row in range(table.rowCount() - 2)
                       if table_diameter(table, row) == self.selected_dia)
            item = table.item(row, 1)
            self.draw_app.ui.tools_table_exc.setCurrentItem(item)
        except KeyError:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("To add a slot first select a tool"))
            self.draw_app.select_tool("drill_select")
            return

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_slot.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.radius = float(self.selected_dia / 2.0)

        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.slot_tool = ExcSlotEditorTool(self.app, self.draw_app, plugin_name=_("Slot"))
        self.ui = self.slot_tool.ui
        self.slot_tool.run()

        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            self.draw_app.draw_utility_geometry(geo=geo)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.slot_tool.length = self.draw_app.last_length
        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Slot"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.points = deepcopy(self.draw_app.clicked_pos) if \
            self.draw_app.clicked_pos and self.draw_app.clicked_pos[0] and self.draw_app.clicked_pos[1] else (0.0, 0.0)
        self.slot_point = None

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_slot)

        self.draw_app.app.inform.emit(_("Click to place ..."))

    def set_plugin_ui(self):
        self.ui.slot_length_entry.set_value(self.draw_app.last_slot_length)
        self.ui.slot_direction_radio.set_value(self.draw_app.last_slot_direction)
        self.ui.on_slot_angle_radio()
        self.ui.slot_angle_spinner.set_value(self.draw_app.last_slot_angle)

        curr_row = self.draw_app.ui.tools_table_exc.currentRow()
        tool_dia = table_diameter(self.draw_app.ui.tools_table_exc, curr_row)
        self.ui.dia_entry.set_value(tool_dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

    def click(self, point):
        self.slot_point = point
        self.draw_app.last_length = self.slot_tool.length
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))
        self.make()
        return "Done."

    def utility_geometry(self, data=None):
        geo_data = self.util_shape(data)
        return DrawToolUtilityShape(geo_data) if geo_data else None

    def util_shape(self, point):
        # updating values here allows us to change the aperture on the fly, after the Tool has been started
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]
        self.radius = float(self.selected_dia / 2.0)
        self.steps_per_circ = circle_steps_of(self.draw_app, self.radius)

        slot_length = float(self.ui.slot_length_entry.get_value())
        slot_angle = float(self.ui.slot_angle_spinner.get_value())
        if self.ui.slot_direction_radio.get_value() == 'X':
            self.half_width = slot_length / 2.0
            self.half_height = self.radius
        else:
            self.half_width = self.radius
            self.half_height = slot_length / 2.0

        if point[0] is None and point[1] is None:
            point_x = self.draw_app.x
            point_y = self.draw_app.y
        else:
            point_x = point[0]
            point_y = point[1]

        geo = []
        if self.half_height > self.half_width:
            p1 = (point_x - self.half_width, point_y - self.half_height + self.half_width)
            p2 = (point_x + self.half_width, point_y - self.half_height + self.half_width)
            p3 = (point_x + self.half_width, point_y + self.half_height - self.half_width)
            p4 = (point_x - self.half_width, point_y + self.half_height - self.half_width)

            down_center = [point_x, point_y - self.half_height + self.half_width]
            d_start_angle = math.pi
            d_stop_angle = 0.0
            down_arc = arc(down_center, self.half_width, d_start_angle, d_stop_angle, 'ccw', self.steps_per_circ)

            up_center = [point_x, point_y + self.half_height - self.half_width]
            u_start_angle = 0.0
            u_stop_angle = math.pi
            up_arc = arc(up_center, self.half_width, u_start_angle, u_stop_angle, 'ccw', self.steps_per_circ)

            geo.append(p1)
            for pt in down_arc:
                geo.append(pt)
            geo.append(p2)
            geo.append(p3)
            for pt in up_arc:
                geo.append(pt)
            geo.append(p4)

            if self.ui.slot_direction_radio.get_value() == 'A':
                return rotate(geom=Polygon(geo), angle=-slot_angle)
            else:
                return Polygon(geo)
        else:
            p1 = (point_x - self.half_width + self.half_height, point_y - self.half_height)
            p2 = (point_x + self.half_width - self.half_height, point_y - self.half_height)
            p3 = (point_x + self.half_width - self.half_height, point_y + self.half_height)
            p4 = (point_x - self.half_width + self.half_height, point_y + self.half_height)

            left_center = [point_x - self.half_width + self.half_height, point_y]
            d_start_angle = math.pi / 2
            d_stop_angle = 1.5 * math.pi
            left_arc = arc(left_center, self.half_height, d_start_angle, d_stop_angle, 'ccw', self.steps_per_circ)

            right_center = [point_x + self.half_width - self.half_height, point_y]
            u_start_angle = 1.5 * math.pi
            u_stop_angle = math.pi / 2
            right_arc = arc(right_center, self.half_height, u_start_angle, u_stop_angle, 'ccw', self.steps_per_circ)

            geo.append(p1)
            geo.append(p2)
            for pt in right_arc:
                geo.append(pt)
            geo.append(p3)
            geo.append(p4)
            for pt in left_arc:
                geo.append(pt)

            return Polygon(geo)

    def native_shape(self, point, array_angle=0.0):
        """Build a capsule and its exact native centerline using the same rotations."""
        diameter = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]
        length = float(self.ui.slot_length_entry.get_value())
        angle_entry = self.ui.slot_angle_spinner if isinstance(self, SlotAdd) else self.ui.slot_angle_entry
        angle = float(angle_entry.get_value())
        if not all(math.isfinite(value) for value in (*point, diameter, length, angle, array_angle)) or \
                diameter <= 0 or length <= diameter:
            raise ValueError(_('Invalid slot length, diameter, angle or position.'))
        direction = self.ui.slot_direction_radio.get_value()
        if direction not in ('X', 'Y', 'A'):
            raise ValueError(_('Invalid slot direction.'))
        half_length = (length - diameter) / 2.0
        dx, dy = (half_length, 0) if direction == 'X' else (0, half_length)
        centerline = LineString([(point[0] - dx, point[1] - dy), (point[0] + dx, point[1] + dy)])
        if direction == 'A':
            centerline = rotate(centerline, -angle, origin=point)
        geometry = self.util_shape(point)
        if array_angle:
            geometry = rotate(geometry, array_angle, origin=point, use_radians=True)
            centerline = rotate(centerline, array_angle, origin=point, use_radians=True)
        shape = DrawToolShape(geometry)
        shape.exc_slot = tuple(centerline.coords)
        shape.exc_slot_geometry = geometry
        return shape

    def make(self):
        self.complete = False
        if self.slot_point is None:
            self.slot_point = (self.draw_app.snap_x, self.draw_app.snap_y)
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]

        try:
            self.geometry = self.native_shape(self.slot_point)
        except (ValueError, TypeError) as error:
            self.geometry = []
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % str(error))
            return 'fail'

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # add the point to drills/slots if the diameter is a key in the dict, if not, create it add the drill location
        # to the value, as a list of itself
        if self.selected_dia in self.draw_app.slot_points_edit:
            self.draw_app.slot_points_edit[self.selected_dia].append(self.geometry.exc_slot)
        else:
            self.draw_app.slot_points_edit[self.selected_dia] = [self.geometry.exc_slot]

        self.draw_app.current_storage = self.draw_app.storage_dict[self.selected_dia]
        self.draw_app.in_action = False
        self.complete = True

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

        self.draw_app.last_slot_length = self.ui.slot_length_entry.get_value()
        self.draw_app.last_slot_direction = self.ui.slot_direction_radio.get_value()
        self.draw_app.last_slot_angle = self.ui.slot_angle_spinner.get_value()

        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        if not self.points:
            self.points = self.draw_app.snap_x, self.draw_app.snap_y
        show_cursor_text(self.draw_app, pos, ref=self.points)

    def on_key(self, key):
        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        # Toggle Pad Direction
        if key == QtCore.Qt.Key.Key_Space:
            if self.ui.slot_direction_radio.get_value() == 'X':
                self.ui.slot_direction_radio.set_value('Y')
            elif self.ui.slot_direction_radio.get_value() == 'Y':
                self.ui.slot_direction_radio.set_value('A')
            elif self.ui.slot_direction_radio.get_value() == 'A':
                self.ui.slot_direction_radio.set_value('X')

            # ## Utility geometry (animated)
            self.draw_app.update_utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.slot_tool.length == self.draw_app.last_length:
                    self.slot_tool.length = str(key.name)
                else:
                    self.slot_tool.length = str(self.slot_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.slot_tool.length == self.draw_app.last_length:
                    self.slot_tool.length = chr(key)
                else:
                    self.slot_tool.length = str(self.slot_tool.length) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if self.slot_tool.length != 0:
                target_length = self.slot_tool.length
                if target_length is None:
                    self.slot_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.ui.x_entry.get_value(), self.ui.y_entry.get_value()
                last_pt = self.draw_app.snap_x, self.draw_app.snap_y

                seg_length = math.sqrt((last_pt[0] - first_pt[0])**2 + (last_pt[1] - first_pt[1])**2)
                if seg_length == 0.0:
                    return
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if first_pt != (new_x, new_y):
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    self.add_slot(slot_pos=(new_x, new_y))

        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

    @excellon_operation(_('Add slot'))
    def add_slot(self, slot_pos):
        curr_pos = self.draw_app.snap(slot_pos[0], slot_pos[1])
        self.draw_app.snap_x = curr_pos[0]
        self.draw_app.snap_y = curr_pos[1]

        self.draw_app.on_canvas_click_left_handler(curr_pos)
        if self.draw_app.active_tool.complete:
            self.draw_app.on_shape_complete()

        self.slot_point = curr_pos
        self.draw_app.clicked_pos = curr_pos

    def on_add_slot(self):
        x = self.ui.x_entry.get_value()
        y = self.ui.y_entry.get_value()
        self.add_slot(slot_pos=(x, y))

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class SlotArray(FCShapeTool):
    """
    Resulting type: MultiPolygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'slot_array'
        self.draw_app = draw_app
        self.app = self.draw_app.app

        self.selected_dia = None

        self.steps_per_circ = None

        self.half_width = 0.0
        self.half_height = 0.0

        self.array_radius = 0.0
        self.slot_axis = 'X'
        self.slot_array = 'linear'     # 'linear'
        self.slot_array_size = None
        self.slot_pitch = None
        self.slot_linear_angle = None

        self.slot_angle = None
        self.slot_direction = None
        self.slot_radius = None

        self.origin = None
        self.destination = None
        self.flag_for_circ_array = None

        self.last_dx = 0
        self.last_dy = 0

        self.pt = []

        self.cursor_data_control = True

        try:
            self.draw_app.app.inform.emit(_("Click to place ..."))
            self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]
            # as a visual marker, select again in tooltable the actual tool that we are using
            # remember that it was deselected when clicking on canvas
            table = self.draw_app.ui.tools_table_exc
            row = next(row for row in range(table.rowCount() - 2)
                       if table_diameter(table, row) == self.selected_dia)
            item = table.item(row, 1)
            self.draw_app.ui.tools_table_exc.setCurrentItem(item)
        except KeyError:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("To add an Slot Array first select a tool in Tool Table"))
            return
        self.radius = float(self.selected_dia / 2.0)

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_array.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.sarray_tool = ExcSlotArrayEditorTool(self.app, self.draw_app, plugin_name=_("Slot Array"))
        self.ui = self.sarray_tool.ui
        self.sarray_tool.run()

        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y), static=True)
        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            self.draw_app.draw_utility_geometry(geo=geo)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Slot Array"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_slot_array)

        # self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

    def set_plugin_ui(self):
        curr_row = self.draw_app.ui.tools_table_exc.currentRow()
        tool_dia = table_diameter(self.draw_app.ui.tools_table_exc, curr_row)
        self.ui.dia_entry.set_value(tool_dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

        # Slot set-up
        self.ui.slot_length_entry.set_value(self.draw_app.last_slot_length)
        self.ui.slot_direction_radio.set_value(self.draw_app.last_slot_direction)
        self.ui.on_slot_angle_radio()
        self.ui.slot_angle_entry.set_value(self.draw_app.last_slot_angle)

        # Slot Array set-up
        self.ui.array_type_radio.set_value(self.draw_app.last_sarray_type)
        self.ui.on_array_type_radio(val=self.ui.array_type_radio.get_value())

        self.ui.array_size_entry.set_value(self.draw_app.last_sarray_size)
        self.ui.array_axis_radio.set_value(self.draw_app.last_sarray_lin_dir)
        self.ui.array_pitch_entry.set_value(self.draw_app.last_sarray_pitch)
        self.ui.array_linear_angle_entry.set_value(self.draw_app.last_sarray_lin_angle)
        self.ui.array_direction_radio.set_value(self.draw_app.last_sarray_circ_dir)
        self.ui.array_angle_entry.set_value(self.draw_app.last_sarray_circ_angle)
        self.ui.radius_entry.set_value(self.draw_app.last_sarray_radius)

        self.ui.on_slot_array_linear_angle_radio()

    def click(self, point):
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

        if self.slot_array == 'linear':    # 'Linear'
            self.make()
            return
        else:   # 'Circular'
            if self.flag_for_circ_array is None:
                self.draw_app.in_action = True
                self.pt.append(point)

                self.flag_for_circ_array = True
                self.set_origin(point)
                self.draw_app.app.inform.emit(_("Click on the Circular Array Start position"))
            else:
                self.destination = point
                self.make()
                self.flag_for_circ_array = None
                return

    def set_origin(self, origin):
        self.origin = origin

    def utility_geometry(self, data=None, static=None):
        self.slot_axis = self.ui.array_axis_radio.get_value()
        self.slot_direction = self.ui.array_direction_radio.get_value()
        self.slot_array = self.ui.array_type_radio.get_value()

        self.slot_array_size = int(self.ui.array_size_entry.get_value())
        self.slot_pitch = float(self.ui.array_pitch_entry.get_value())
        self.slot_linear_angle = float(self.ui.array_linear_angle_entry.get_value())
        self.slot_angle = float(self.ui.array_angle_entry.get_value())

        if self.slot_array == 'linear':    # 'Linear'
            if data[0] is None and data[1] is None:
                dx = self.draw_app.x
                dy = self.draw_app.y
            else:
                dx = data[0]
                dy = data[1]

            geo_el_list = []
            geo_el = []
            self.points = [dx, dy]

            for item in range(self.slot_array_size):
                if self.slot_axis == 'X':
                    geo_el = self.util_shape(((dx + (self.slot_pitch * item)), dy))
                if self.slot_axis == 'Y':
                    geo_el = self.util_shape((dx, (dy + (self.slot_pitch * item))))
                if self.slot_axis == 'A':
                    x_adj = self.slot_pitch * math.cos(math.radians(self.slot_linear_angle))
                    y_adj = self.slot_pitch * math.sin(math.radians(self.slot_linear_angle))
                    geo_el = self.util_shape(
                        ((dx + (x_adj * item)), (dy + (y_adj * item)))
                    )

                if static is None or static is False:
                    geo_el = translate(geo_el, xoff=(dx - self.last_dx), yoff=(dy - self.last_dy))
                geo_el_list.append(geo_el)

            self.last_dx = dx
            self.last_dy = dy
            return DrawToolUtilityShape(geo_el_list)
        else:   # 'Circular'
            if data[0] is None and data[1] is None:
                cdx = self.draw_app.x
                cdy = self.draw_app.y
            else:
                cdx = data[0]
                cdy = data[1]

            # if len(self.pt) > 0:
            #     temp_points = [x for x in self.pt]
            #     temp_points.append([cdx, cdy])
            #     return DrawToolUtilityShape(LineString(temp_points))

            utility_list = []

            try:
                radius = distance((cdx, cdy), self.origin)
            except Exception:
                radius = 0
            if radius == 0:
                self.draw_app.delete_utility_geometry()
            # no radius to show before the center is clicked: a mouse move over the canvas must not set the
            # radius typed for the 'Add' button to 0
            if self.origin is not None:
                self.ui.radius_entry.set_value(radius)

            if len(self.pt) >= 1 and radius > 0:
                try:
                    if cdx < self.origin[0]:
                        radius = -radius

                    # draw the temp geometry
                    initial_angle = math.asin((cdy - self.origin[1]) / radius)

                    temp_circular_geo = self.circular_util_shape(radius, initial_angle)

                    # draw the line
                    temp_points = [x for x in self.pt]
                    temp_points.append([cdx, cdy])
                    temp_line = LineString(temp_points)

                    for geo_shape in temp_circular_geo:
                        utility_list.append(geo_shape.geo)
                    utility_list.append(temp_line)

                    return DrawToolUtilityShape(utility_list)
                except Exception as e:
                    log.error("SlotArray.utility_geometry -- circular -> %s" % str(e))

    def circular_util_shape(self, radius, angle):
        self.slot_direction = self.ui.array_direction_radio.get_value()
        self.slot_angle = self.ui.array_angle_entry.get_value()
        self.slot_array_size = self.ui.array_size_entry.get_value()

        circular_geo = []
        if self.slot_direction == 'CW':
            for i in range(self.slot_array_size):
                angle_radians = math.radians(self.slot_angle * i)
                x = self.origin[0] + radius * math.cos(-angle_radians + angle)
                y = self.origin[1] + radius * math.sin(-angle_radians + angle)

                circular_geo.append(SlotAdd.native_shape(self, (x, y), math.pi - angle_radians + angle))
        else:
            for i in range(self.slot_array_size):
                angle_radians = math.radians(self.slot_angle * i)
                x = self.origin[0] + radius * math.cos(angle_radians + angle)
                y = self.origin[1] + radius * math.sin(angle_radians + angle)

                circular_geo.append(SlotAdd.native_shape(self, (x, y), angle_radians + angle - math.pi))

        return circular_geo

    def util_shape(self, point):
        # updating values here allows us to change the aperture on the fly, after the Tool has been started
        self.selected_dia = self.draw_app.tool2tooldia[self.draw_app.last_tool_selected]
        self.radius = float(self.selected_dia / 2.0)
        self.steps_per_circ = circle_steps_of(self.draw_app, self.radius)

        slot_length = float(self.ui.slot_length_entry.get_value())
        slot_angle = float(self.ui.slot_angle_entry.get_value())

        if self.ui.slot_direction_radio.get_value() == 'X':
            self.half_width = slot_length / 2.0
            self.half_height = self.radius
        else:
            self.half_width = self.radius
            self.half_height = slot_length / 2.0

        if point[0] is None and point[1] is None:
            point_x = self.draw_app.x
            point_y = self.draw_app.y
        else:
            point_x = point[0]
            point_y = point[1]

        geo = []

        if self.half_height > self.half_width:
            p1 = (point_x - self.half_width, point_y - self.half_height + self.half_width)
            p2 = (point_x + self.half_width, point_y - self.half_height + self.half_width)
            p3 = (point_x + self.half_width, point_y + self.half_height - self.half_width)
            p4 = (point_x - self.half_width, point_y + self.half_height - self.half_width)

            down_center = [point_x, point_y - self.half_height + self.half_width]
            d_start_angle = math.pi
            d_stop_angle = 0.0
            down_arc = arc(down_center, self.half_width, d_start_angle, d_stop_angle, 'ccw', self.steps_per_circ)

            up_center = [point_x, point_y + self.half_height - self.half_width]
            u_start_angle = 0.0
            u_stop_angle = math.pi
            up_arc = arc(up_center, self.half_width, u_start_angle, u_stop_angle, 'ccw', self.steps_per_circ)

            geo.append(p1)
            for pt in down_arc:
                geo.append(pt)
            geo.append(p2)
            geo.append(p3)
            for pt in up_arc:
                geo.append(pt)
            geo.append(p4)
        else:
            p1 = (point_x - self.half_width + self.half_height, point_y - self.half_height)
            p2 = (point_x + self.half_width - self.half_height, point_y - self.half_height)
            p3 = (point_x + self.half_width - self.half_height, point_y + self.half_height)
            p4 = (point_x - self.half_width + self.half_height, point_y + self.half_height)

            left_center = [point_x - self.half_width + self.half_height, point_y]
            d_start_angle = math.pi / 2
            d_stop_angle = 1.5 * math.pi
            left_arc = arc(left_center, self.half_height, d_start_angle, d_stop_angle, 'ccw', self.steps_per_circ)

            right_center = [point_x + self.half_width - self.half_height, point_y]
            u_start_angle = 1.5 * math.pi
            u_stop_angle = math.pi / 2
            right_arc = arc(right_center, self.half_height, u_start_angle, u_stop_angle, 'ccw', self.steps_per_circ)

            geo.append(p1)
            geo.append(p2)
            for pt in right_arc:
                geo.append(pt)
            geo.append(p3)
            geo.append(p4)
            for pt in left_arc:
                geo.append(pt)

        # this function return one slot in the slot array and the following will rotate that one slot around it's
        # center if the radio value is "A".
        if self.ui.slot_direction_radio.get_value() == 'A':
            return rotate(Polygon(geo), -slot_angle)
        else:
            return Polygon(geo)

    def make(self):
        self.complete = False
        if self.slot_array_size < 1 or (self.slot_array != 'linear' and
                (not math.isfinite(self.slot_angle) or abs(self.slot_angle) * self.slot_array_size > 360 or
                 distance(self.destination, self.origin) == 0)):
            self.geometry = []
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % _('Invalid array size, angle or radius.'))
            return 'fail'
        try:
            SlotAdd.native_shape(self, self.points)
            if self.slot_array == 'linear' and (self.slot_axis not in ('X', 'Y', 'A') or
                    not math.isfinite(self.slot_pitch) or
                    (self.slot_axis == 'A' and not math.isfinite(self.slot_linear_angle))):
                raise ValueError(_('Invalid array pitch, direction or angle.'))
        except (ValueError, TypeError) as error:
            self.geometry = []
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % str(error))
            return 'fail'
        self.geometry = []
        geo = None

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.draw_app.current_storage = self.draw_app.storage_dict[self.selected_dia]

        if self.slot_array == 'linear':    # 'Linear'
            for item in range(self.slot_array_size):
                if self.slot_axis == 'X':
                    geo = SlotAdd.native_shape(self, ((self.points[0] + (self.slot_pitch * item)), self.points[1]))
                if self.slot_axis == 'Y':
                    geo = SlotAdd.native_shape(self, (self.points[0], (self.points[1] + (self.slot_pitch * item))))
                if self.slot_axis == 'A':
                    x_adj = self.slot_pitch * math.cos(math.radians(self.slot_linear_angle))
                    y_adj = self.slot_pitch * math.sin(math.radians(self.slot_linear_angle))
                    geo = SlotAdd.native_shape(self,
                        ((self.points[0] + (x_adj * item)), (self.points[1] + (y_adj * item)))
                    )

                self.geometry.append(geo)
        else:   # 'Circular'
            # the angle, size and radius were validated at the top of make()
            radius = distance(self.destination, self.origin)
            if self.destination[0] < self.origin[0]:
                radius = -radius
            initial_angle = math.asin((self.destination[1] - self.origin[1]) / radius)

            circular_geo = self.circular_util_shape(radius, initial_angle)
            self.geometry += circular_geo

        self.draw_app.slot_points_edit.setdefault(self.selected_dia, []).extend(
            shape.exc_slot for shape in self.geometry)
        self.complete = True
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        self.draw_app.in_action = False

        # Slot
        self.draw_app.last_slot_length = self.ui.slot_length_entry.get_value()
        self.draw_app.last_slot_direction = self.ui.slot_direction_radio.get_value()
        self.draw_app.last_slot_angle = self.ui.slot_angle_entry.get_value()

        # Slot array
        self.draw_app.last_sarray_type = self.ui.array_type_radio.get_value()
        self.draw_app.last_sarray_size = self.ui.array_size_entry.get_value()
        self.draw_app.last_sarray_lin_dir = self.ui.array_axis_radio.get_value()
        self.draw_app.last_sarray_circ_dir = self.ui.array_direction_radio.get_value()
        self.draw_app.last_sarray_pitch = self.ui.array_pitch_entry.get_value()
        self.draw_app.last_sarray_lin_angle = self.ui.array_linear_angle_entry.get_value()
        self.draw_app.last_sarray_circ_angle = self.ui.array_angle_entry.get_value()
        self.draw_app.last_sarray_radius = self.ui.radius_entry.get_value()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (AttributeError, TypeError):
            pass

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        if not self.points:
            self.points = self.draw_app.snap_x, self.draw_app.snap_y
        show_cursor_text(self.draw_app, pos, length=abs(self.ui.radius_entry.get_value()), ref=self.points)

    def on_key(self, key):
        key_modifier = QtWidgets.QApplication.keyboardModifiers()

        if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
            mod_key = 'Shift'
        elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
            mod_key = 'Control'
        else:
            mod_key = None

        if mod_key == 'Control':
            # Toggle Pad Array Direction
            if key == QtCore.Qt.Key.Key_Space:
                if self.ui.array_axis_radio.get_value() == 'X':
                    self.ui.array_axis_radio.set_value('Y')
                elif self.ui.array_axis_radio.get_value() == 'Y':
                    self.ui.array_axis_radio.set_value('A')
                elif self.ui.array_axis_radio.get_value() == 'A':
                    self.ui.array_axis_radio.set_value('X')

                # ## Utility geometry (animated)
                self.draw_app.update_utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
        elif mod_key is None:
            if key == 'C' or key == QtCore.Qt.Key.Key_C:
                self.cursor_data_control = not self.cursor_data_control

            # Jump to coords
            if key == QtCore.Qt.Key.Key_J or key == 'J':
                self.draw_app.app.on_jump_to()

            # Toggle Pad Direction
            if key == QtCore.Qt.Key.Key_Space:
                if self.ui.slot_direction_radio.get_value() == 'X':
                    self.ui.slot_direction_radio.set_value('Y')
                elif self.ui.slot_direction_radio.get_value() == 'Y':
                    self.ui.slot_direction_radio.set_value('A')
                elif self.ui.slot_direction_radio.get_value() == 'A':
                    self.ui.slot_direction_radio.set_value('X')

                # ## Utility geometry (animated)
                self.draw_app.update_utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))

    @excellon_operation(_('Slot array'))
    def add_slot_array(self, array_pos):
        self.array_radius = self.ui.radius_entry.get_value()

        self.slot_axis = self.ui.array_axis_radio.get_value()
        self.slot_direction = self.ui.array_direction_radio.get_value()
        self.slot_array = self.ui.array_type_radio.get_value()

        self.slot_array_size = int(self.ui.array_size_entry.get_value())
        self.slot_pitch = float(self.ui.array_pitch_entry.get_value())
        self.slot_linear_angle = float(self.ui.array_linear_angle_entry.get_value())
        self.slot_angle = float(self.ui.array_angle_entry.get_value())

        curr_pos = self.draw_app.snap(array_pos[0], array_pos[1])
        self.draw_app.snap_x = curr_pos[0]
        self.draw_app.snap_y = curr_pos[1]

        self.points = [self.draw_app.snap_x, self.draw_app.snap_y]
        self.origin = [self.draw_app.snap_x, self.draw_app.snap_y]
        self.destination = ((self.origin[0] + self.array_radius), self.origin[1])
        self.flag_for_circ_array = True
        self.make()

        if self.draw_app.current_storage is not None:
            self.draw_app.on_exc_shape_complete(self.draw_app.current_storage)
            self.draw_app.build_ui()

        if self.draw_app.active_tool.complete:
            self.draw_app.on_shape_complete()

        self.draw_app.select_tool("drill_select")

        self.draw_app.clicked_pos = curr_pos

    def on_add_slot_array(self):
        x = self.ui.x_entry.get_value()
        y = self.ui.y_entry.get_value()
        self.add_slot_array(array_pos=(x, y))

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class ResizeEditorExc(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'drill_resize'
        self.draw_app = draw_app
        self.app = self.draw_app.app

        self.resize_dia = None
        self.points = None

        # made this a set so there are no duplicates
        self.selected_dia_set = set()

        self.current_storage = None
        self.geometry = []
        self.destination_storage = None

        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.resize_tool = ExcResizeEditorTool(self.app, self.draw_app, plugin_name=_("Resize"))
        self.ui = self.resize_tool.ui
        self.resize_tool.run()

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Resize"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.resize_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        try:
            self.ui.res_dia_entry.editingFinished.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.resize_btn.clicked.connect(self.on_resize)
        self.ui.res_dia_entry.editingFinished.connect(self.on_resize)

        self.draw_app.app.inform.emit(_("Click on the Drill(s) to resize ..."))

    def set_plugin_ui(self):
        # curr_row = self.draw_app.ui.tools_table_exc.currentRow()
        # tool_dia = float(self.draw_app.ui.tools_table_exc.item(curr_row, 1).text())
        # self.ui.dia_entry.set_value(tool_dia)

        self.update_diameters()

    def utility_geometry(self, data=None):
        return DrawToolUtilityShape([])

    def click_release(self, point):
        self.update_diameters()

    def update_diameters(self):
        sel_dia_list = set()
        for index in self.draw_app.ui.tools_table_exc.selectedIndexes():
            row = index.row()
            if row < 0:
                continue
            # on column 1 in tool tables we hold the diameters, and we retrieve them as strings
            # therefore below we convert to float
            sel_dia_list.add(repr(table_diameter(self.draw_app.ui.tools_table_exc, row)))

        self.ui.dia_entry.set_value(', '.join([i for i in list(sel_dia_list)]))

    @excellon_operation(_('Resize features'))
    def make(self):
        new_dia = self.ui.res_dia_entry.get_value()
        result = self.draw_app.resize_features(self.draw_app.selected, new_dia)
        if result == 'fail':
            return result
        self.complete = True
        self.draw_app.select_tool("drill_select")

    def on_resize(self):
        self.make()
        self.clean_up()

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        if not self.points:
            self.points = self.draw_app.snap_x, self.draw_app.snap_y
        show_cursor_text(self.draw_app, pos, ref=self.points)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class MoveEditorExc(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'drill_move'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.origin = None
        self.destination = None
        self.sel_limit = self.draw_app.app.options.get(
            "excellon_editor_sel_limit",
            self.draw_app.app.defaults.get(
                "excellon_editor_sel_limit",
                self.draw_app.app.defaults.factory_defaults.get("excellon_editor_sel_limit")
            )
        )
        self.selection_shape = self.selection_bbox()
        self.selected_dia_list = []

        self.current_storage = None
        self.geometry = []

        for index in self.draw_app.ui.tools_table_exc.selectedIndexes():
            row = index.row()
            # on column 1 in tool tables we hold the diameters, and we retrieve them as strings
            # therefore below we convert to float
            self.selected_dia_list.append(table_diameter(self.draw_app.ui.tools_table_exc, row))

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        # Switch notebook to Properties page
        self.draw_app.app.ui.notebook.setCurrentWidget(self.draw_app.app.ui.properties_tab)

        if self.draw_app.launched_from_shortcuts is True:
            self.draw_app.launched_from_shortcuts = False
        else:
            if not self.draw_app.get_selected():
                self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Nothing selected."))
                self.draw_app.app.ui.select_drill_btn.setChecked(True)
                self.draw_app.on_tool_select('drill_select')
            else:
                self.draw_app.app.inform.emit(_("Click on reference location ..."))

    def set_origin(self, origin):
        self.origin = origin

    def click(self, point):
        if not self.draw_app.get_selected():
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Nothing selected."))
            return "Nothing to move."

        if self.origin is None:
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click on target location ..."))
            return
        else:
            self.destination = point
            self.make()

            # MS: always return to the Select Tool
            self.draw_app.select_tool("drill_select")
            return

    @excellon_operation(_('Move features'))
    def make(self):
        # Create new geometry
        dx = self.destination[0] - self.origin[0]
        dy = self.destination[1] - self.origin[1]

        # Each tool storage is read 1 time, its shapes are translated in 1 vectorized batch and the editor
        # is plotted 1 time at the end (not 1 time for each moved shape).
        if dx != 0 or dy != 0:
            selected = self.draw_app.get_selected()
            moved_ids = set()
            for sel_dia in self.selected_dia_list:
                self.current_storage = self.draw_app.storage_dict[sel_dia]
                storage_ids = {id(shape) for shape in self.current_storage.get_objects()}
                shapes = [shape for shape in selected if id(shape) in storage_ids and id(shape) not in moved_ids]
                if not shapes:
                    continue

                translated = translate_geometry([shape.geo for shape in shapes], dx, dy)
                # 1 change of the storage index instead of a delete and an insert for each point
                self.current_storage.replace(removed=shapes, inserted=[
                    translated_exc_shape(shape, geo) for shape, geo in zip(shapes, translated)
                ])
                moved_ids |= {id(shape) for shape in shapes}

            if moved_ids:
                self.draw_app.selected[:] = [shape for shape in self.draw_app.selected if id(shape) not in moved_ids]

        self.draw_app.delete_utility_geometry()
        self.draw_app.tool_shape.clear(update=True)
        self.draw_app.replot()
        self.draw_app.build_ui()
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        try:
            self.draw_app.app.jump_signal.disconnect()
        except TypeError:
            pass

    def selection_bbox(self):
        geo_list = []
        for select_shape in self.draw_app.get_selected():
            if isinstance(select_shape.geo, (MultiLineString, MultiPolygon)):
                geometric_data = select_shape.geo.geoms
            else:
                geometric_data = select_shape.geo

            try:
                for g in geometric_data:
                    geo_list.append(g)
            except TypeError:
                geo_list.append(geometric_data)

        xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_list)

        pt1 = (xmin, ymin)
        pt2 = (xmax, ymin)
        pt3 = (xmax, ymax)
        pt4 = (xmin, ymax)

        return Polygon([pt1, pt2, pt3, pt4])

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """
        geo_list = []

        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        if len(self.draw_app.get_selected()) <= self.sel_limit:
            try:
                geo_list = translate_geometry([geom.geo for geom in self.draw_app.get_selected()], dx, dy)
            except AttributeError:
                self.draw_app.select_tool('drill_select')
                self.draw_app.selected.clear()
                return
            return DrawToolUtilityShape(geo_list)
        else:
            try:
                ss_el = translate(self.selection_shape, xoff=dx, yoff=dy)
            except ValueError:
                ss_el = None
            return DrawToolUtilityShape(ss_el)

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class CopyEditorExc(FCShapeTool):
    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'drill_copy'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.storage = self.draw_app.storage_dict

        self.origin = None
        self.destination = None
        self.sel_limit = self.draw_app.app.options.get(
            "excellon_editor_sel_limit",
            self.draw_app.app.defaults.get(
                "excellon_editor_sel_limit",
                self.draw_app.app.defaults.factory_defaults.get("excellon_editor_sel_limit")
            )
        )
        self.selection_shape = self.selection_bbox()
        self.selected_dia_list = []

        self.current_storage = None
        self.geometry = []

        self.cursor_data_control = True

        for index in self.draw_app.ui.tools_table_exc.selectedIndexes():
            row = index.row()
            # on column 1 in tool tables we hold the diameters, and we retrieve them as strings
            # therefore below we convert to float
            self.selected_dia_list.append(table_diameter(self.draw_app.ui.tools_table_exc, row))

        # store here the utility geometry, so we can use it on the last step
        self.util_geo = None

        if not self.draw_app.get_selected():
            self.has_selection = False
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Nothing selected.")))
            self.draw_app.app.ui.select_drill_btn.setChecked(True)
            self.draw_app.on_tool_select('drill_select')
        else:
            self.has_selection = True
            self.draw_app.app.inform.emit(_("Click on reference location ..."))

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data

        self.copy_tool = ExcCopyEditorTool(self.app, self.draw_app, plugin_name=_("Copy"))
        self.ui = self.copy_tool.ui
        self.copy_tool.run()

        self.app.ui.notebook.setTabText(2, _("Copy"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

    def set_origin(self, origin):
        self.draw_app.app.inform.emit(_("Click on destination point ..."))
        self.origin = origin

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if self.has_selection is False:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _(" Nothing selected.")))
            return "Nothing to move."

        if self.origin is None:
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click on target location ..."))
            return
        else:
            self.destination = point
            self.make()

            # MS: always return to the Select Tool
            self.draw_app.select_tool("drill_select")
            return

    @excellon_operation(_('Copy features'))
    def make(self):
        selected = list(self.draw_app.get_selected())
        if not selected:
            return 'fail'
        dx, dy = self.destination[0] - self.origin[0], self.destination[1] - self.origin[1]
        if self.ui.mode_radio.get_value() == 'n':
            geometries = translate_geometry([shape.geo for shape in selected], dx, dy)
        else:
            preview = self.array_util_geometry((dx, dy))
            if preview is None:
                return 'fail'
            geometries = list(preview.geo)
            if self.ui.array_type_radio.get_value() == 'circular':
                geometries = geometries[:-1]  # cursor radius line, not an Excellon feature
        if not geometries or len(geometries) % len(selected):
            return 'fail'
        owners = {id(shape): diameter for diameter, storage in self.draw_app.storage_dict.items()
                  for shape in storage.get_objects()}
        batches = {}
        circular = self.ui.mode_radio.get_value() != 'n' and self.ui.array_type_radio.get_value() == 'circular'
        for index, geo in enumerate(geometries):
            source = selected[index % len(selected)]
            diameter = owners[id(source)]
            replacement = translated_exc_shape(source, geo)
            if circular and isinstance(source.geo, Polygon):
                angle = float(self.ui.angle_entry.get_value()) * (index // len(selected))
                if self.ui.array_dir_radio.get_value() == 'CW':
                    angle = -angle
                endpoints = feature_properties(source, diameter, 0)['endpoints']
                line = rotate(LineString(endpoints), angle, origin=self.origin)
                rotated = rotate(source.geo, angle, origin=self.origin)
                line = translate(line, geo.centroid.x - rotated.centroid.x, geo.centroid.y - rotated.centroid.y)
                replacement.exc_slot = tuple(tuple(point) for point in line.coords)
                replacement.exc_slot_geometry = geo
            if hasattr(source, 'exc_source_diameter'):
                replacement.exc_source_diameter = source.exc_source_diameter
            batches.setdefault(diameter, []).append(replacement)
        for diameter, shapes in batches.items():
            self.draw_app.add_exc_shape(shapes, self.draw_app.storage_dict[diameter])
            for shape in shapes:
                counters = self.draw_app.points_edit if isinstance(shape.geo, MultiLineString) \
                    else self.draw_app.slot_points_edit
                counters.setdefault(diameter, []).append((0, 0))
        self.draw_app.selected.clear()
        self.geometry = []
        self.draw_app.delete_utility_geometry()
        self.draw_app.replot()

        self.complete = True
        self.origin = None
        self.draw_cursor_data(delete=True)

        self.draw_app.build_ui()
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def selection_bbox(self):
        geo_list = []
        for select_shape in self.draw_app.get_selected():
            if isinstance(select_shape.geo, (MultiLineString, MultiPolygon)):
                geometric_data = select_shape.geo.geoms
            else:
                geometric_data = select_shape.geo

            try:
                for g in geometric_data:
                    geo_list.append(g)
            except TypeError:
                geo_list.append(geometric_data)

        xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_list)

        pt1 = (xmin, ymin)
        pt2 = (xmax, ymin)
        pt3 = (xmax, ymax)
        pt4 = (xmin, ymax)

        return Polygon([pt1, pt2, pt3, pt4])

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """
        geo_list = []

        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        copy_mode = self.ui.mode_radio.get_value()
        copies = 1
        if copy_mode != 'n':
            copies = (int(self.ui.rows.get_value()) * int(self.ui.columns.get_value())
                      if self.ui.array_type_radio.get_value() == '2D'
                      else int(self.ui.array_size_entry.get_value()))
        if len(self.draw_app.get_selected()) * copies <= self.sel_limit:
            if copy_mode == 'n':
                try:
                    for geom in self.draw_app.get_selected():
                        geo_list.append(translate(geom.geo, xoff=dx, yoff=dy))
                except AttributeError:
                    self.draw_app.select_tool('drill_select')
                    self.draw_app.selected.clear()
                    return
                self.util_geo = DrawToolUtilityShape(geo_list)
            else:
                self.util_geo = self.array_util_geometry((dx, dy))
        else:
            try:
                ss_el = translate(self.selection_shape, xoff=dx, yoff=dy)
            except ValueError:
                ss_el = None
            self.util_geo = DrawToolUtilityShape(ss_el)

        return self.util_geo

    def array_util_geometry(self, pos, static=None):
        array_type = self.ui.array_type_radio.get_value()      # 'linear', '2D', 'circular'

        if array_type == 'linear':  # 'Linear'
            return self.linear_geo(pos, static)
        elif array_type == '2D':
            return self.dd_geo(pos)
        elif array_type == 'circular':  # 'Circular'
            return self.circular_geo(pos)

    def linear_geo(self, pos, static):
        axis = self.ui.axis_radio.get_value()  # X, Y or A
        pitch = float(self.ui.pitch_entry.get_value())
        linear_angle = float(self.ui.linear_angle_spinner.get_value())
        array_size = int(self.ui.array_size_entry.get_value())

        if pos[0] is None and pos[1] is None:
            dx = self.draw_app.x
            dy = self.draw_app.y
        else:
            dx = pos[0]
            dy = pos[1]

        geo_list = []
        self.points = [(dx, dy)]

        for item in range(array_size):
            if axis == 'X':
                new_pos = ((dx + (pitch * item)), dy)
            elif axis == 'Y':
                new_pos = (dx, (dy + (pitch * item)))
            else:  # 'A'
                x_adj = pitch * math.cos(math.radians(linear_angle))
                y_adj = pitch * math.sin(math.radians(linear_angle))
                new_pos = ((dx + (x_adj * item)), (dy + (y_adj * item)))

            for g in self.draw_app.get_selected():
                if static is None or static is False:
                    geo_list.append(translate(g.geo, xoff=new_pos[0], yoff=new_pos[1]))
                else:
                    geo_list.append(g.geo)
        return DrawToolUtilityShape(geo_list)

    def dd_geo(self, pos):
        trans_geo = []
        array_2d_type = self.ui.placement_radio.get_value()

        rows = self.ui.rows.get_value()
        columns = self.ui.columns.get_value()

        spacing_rows = self.ui.spacing_rows.get_value()
        spacing_columns = self.ui.spacing_columns.get_value()

        off_x = self.ui.offsetx_entry.get_value()
        off_y = self.ui.offsety_entry.get_value()

        geo_source = [s.geo for s in self.draw_app.get_selected()]

        def geo_bounds(geo: (BaseGeometry, list)):
            minx = np.inf
            miny = np.inf
            maxx = -np.inf
            maxy = -np.inf

            if type(geo) == list:
                for shp in geo:
                    minx_, miny_, maxx_, maxy_ = geo_bounds(shp)
                    minx = min(minx, minx_)
                    miny = min(miny, miny_)
                    maxx = max(maxx, maxx_)
                    maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's an object, return its bounds
                return geo.bounds

        xmin, ymin, xmax, ymax = geo_bounds(geo_source)

        currentx = pos[0]
        currenty = pos[1]

        def translate_recursion(geom):
            if type(geom) == list:
                geoms = []
                for local_geom in geom:
                    res_geo = translate_recursion(local_geom)
                    try:
                        geoms += res_geo
                    except TypeError:
                        geoms.append(res_geo)
                return geoms
            else:
                return translate(geom, xoff=currentx, yoff=currenty)

        for row in range(rows):
            currentx = pos[0]

            for col in range(columns):
                trans_geo += translate_recursion(geo_source)
                if array_2d_type == 's':  # 'spacing'
                    currentx += (xmax - xmin + spacing_columns)
                else:   # 'offset'
                    currentx = pos[0] + off_x * (col + 1)    # because 'col' starts from 0 we increment by 1

            if array_2d_type == 's':  # 'spacing'
                currenty += (ymax - ymin + spacing_rows)
            else:   # 'offset;
                currenty = pos[1] + off_y * (row + 1)    # because 'row' starts from 0 we increment by 1

        return DrawToolUtilityShape(trans_geo)

    def circular_geo(self, pos):
        if pos[0] is None and pos[1] is None:
            cdx = self.draw_app.x
            cdy = self.draw_app.y
        else:
            cdx = pos[0] + self.origin[0]
            cdy = pos[1] + self.origin[1]

        utility_list = []

        try:
            radius = distance((cdx, cdy), self.origin)
        except Exception:
            radius = 0

        if radius == 0:
            self.draw_app.delete_utility_geometry()

        # the circle needs only the reference point (origin); self.points is set only by the Linear preview
        if self.origin is not None and radius > 0:
            try:
                if cdx < self.origin[0]:
                    radius = -radius

                # draw the temp geometry
                initial_angle = math.asin((cdy - self.origin[1]) / radius)
                temp_circular_geo = self.circular_util_shape(radius, initial_angle)

                temp_points = [
                    (self.origin[0], self.origin[1]),
                    (self.origin[0] + pos[0], self.origin[1] + pos[1])
                ]
                temp_line = LineString(temp_points)

                for geo_shape in temp_circular_geo:
                    utility_list.append(geo_shape.geo)
                utility_list.append(temp_line)

                return DrawToolUtilityShape(utility_list)
            except Exception as e:
                log.error("DrillArray.utility_geometry -- circular -> %s" % str(e))

    def circular_util_shape(self, radius, ini_angle):
        direction = self.ui.array_dir_radio.get_value()      # CW or CCW
        angle = float(self.ui.angle_entry.get_value())
        array_size = int(self.ui.array_size_entry.get_value())
        if not all(math.isfinite(value) for value in (radius, ini_angle, angle)) or array_size < 1:
            raise ValueError(_('Invalid circular array.'))

        circular_geo = []
        for i in range(array_size):
            angle_radians = math.radians(angle * i)
            if direction == 'CW':
                x = radius * math.cos(-angle_radians + ini_angle)
                y = radius * math.sin(-angle_radians + ini_angle)
            else:
                x = radius * math.cos(angle_radians + ini_angle)
                y = radius * math.sin(angle_radians + ini_angle)

            for sshp in self.draw_app.get_selected():
                rotation = -angle * i if direction == 'CW' else angle * i
                geo_sol = translate(rotate(sshp.geo, rotation, origin=self.origin), x, y)

                circular_geo.append(DrawToolShape(geo_sol))

        return circular_geo

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        ref = self.origin
        length = None if ref is not None else math.hypot(pos[0], pos[1])
        show_cursor_text(self.draw_app, pos, length=length, ref=ref)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.copy_tool.length == 0:
                    self.copy_tool.length = str(key.name)
                else:
                    self.copy_tool.length = str(self.copy_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.copy_tool.length == 0:
                    self.copy_tool.length = chr(key)
                else:
                    self.copy_tool.length = str(self.copy_tool.length) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if self.copy_tool.length != 0:
                target_length = self.copy_tool.length
                if target_length is None:
                    self.copy_tool.length = 0.0
                    return _("Failed.")

                # the copy goes at the typed distance from the reference point, towards the cursor; self.points is
                # not a position here (it is empty, or the offset of the Linear array preview)
                if self.origin is None:
                    return '[WARNING_NOTCL] %s' % _("Click on reference location ...")
                first_pt = self.origin
                last_pt = self.draw_app.snap_x, self.draw_app.snap_y

                seg_length = math.sqrt((last_pt[0] - first_pt[0]) ** 2 + (last_pt[1] - first_pt[1]) ** 2)
                if seg_length == 0.0:
                    return
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.points = []
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                self.destination = (new_x, new_y)
                # make() adds and plots the copies and gives the message, as for a click
                self.make()

                # MS: always return to the Select Tool
                self.draw_app.select_tool("drill_select")
                return

    def clean_up(self):
        self.draw_app.selected.clear()
        self.draw_app.ui.tools_table_exc.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.copy_tool.on_tab_close()


def translated_exc_shape(source, geometry):
    """Keep native slot centerlines when translation creates a replacement wrapper."""
    shape = DrawToolShape(geometry)
    if hasattr(source, 'exc_source_diameter'):
        shape.exc_source_diameter = source.exc_source_diameter
    if getattr(source, GENERATED_DRILLS, False):
        setattr(shape, GENERATED_DRILLS, True)
    native = getattr(source, 'exc_slot', None)
    if native is not None and getattr(source, 'exc_slot_geometry', None) is source.geo:
        dx = geometry.centroid.x - source.geo.centroid.x
        dy = geometry.centroid.y - source.geo.centroid.y
        shape.exc_slot = tuple((x + dx, y + dy) for x, y in native)
        shape.exc_slot_geometry = geometry
    return shape


def match_tool_data(old_tools, new_tools, olddia_newdia, decimals):
    """
    Match each tool of the edited content to the tool of the source object that it comes from, by diameter. The
    editor can renumber the tools and change a diameter (olddia_newdia: source diameter -> diameter in the editor),
    so an old tool is matched by the diameter that it has in the editor. An old tool is used for 1 new tool at most.

    :param old_tools:       tools of the source object (the tool id is the key, 'tooldia' in the value)
    :param new_tools:       tools of the edited content, same structure
    :param olddia_newdia:   the editor dict: source diameter -> diameter now
    :param decimals:        diameters are compared rounded to this number of decimals
    :return:                dict: new tool id -> old tool id; None for a tool with no match
    """
    def key(dia):
        return round(float(dia), decimals)

    renamed = {key(old): key(new) for old, new in olddia_newdia.items()}
    unused = list(old_tools)
    matches = {}
    for new_id, new_tool in new_tools.items():
        wanted = key(new_tool['tooldia'])
        found = None
        for old_id in unused:
            old_dia = key(old_tools[old_id]['tooldia'])
            if renamed.get(old_dia, old_dia) == wanted:
                found = old_id
                break
        matches[new_id] = found
        if found is not None:
            unused.remove(found)
    return matches


def tools_with_data(old_tools, new_tools, olddia_newdia, data_defaults, decimals):
    """
    A deep copy of the tools of the edited content, each with a 'data' dict: the data of the matching tool of the
    source object (match_tool_data()), or the defaults for a tool with no match.

    :return: dict of tools
    """
    matches = match_tool_data(old_tools, new_tools, olddia_newdia, decimals)
    result = deepcopy(new_tools)
    for new_id, old_id in matches.items():
        if old_id is not None and old_tools[old_id].get('data'):
            result[new_id]['data'] = deepcopy(old_tools[old_id]['data'])
        else:
            result[new_id]['data'] = deepcopy(data_defaults)
    return result


class AppExcEditor(QtCore.QObject):

    history_changed = QtCore.pyqtSignal()
    worker_result = QtCore.pyqtSignal(object)

    draw_shape_idx = -1

    def __init__(self, app):
        # assert isinstance(app, FlatCAMApp.App), "Expected the app to be a FlatCAMApp.App, got %s" % type(app)

        super(AppExcEditor, self).__init__()

        self.app = app
        self.canvas = self.app.plotcanvas
        self.units = self.app.app_units.upper()

        self.dec_format = self.app.dec_format

        # Number of decimals used by tools in this class
        self.decimals = self.app.decimals

        self.ui = AppExcEditorUI(app=self.app)

        self.edited_obj = None

        # ## Toolbar events and properties
        self.tools_exc = {}

        # ## Data
        self.active_tool = None
        self.in_action = False

        self.storage_dict = {}
        self.current_storage = []

        # build the data from the Excellon point into a dictionary
        #  {tool_dia: [geometry_in_points]}
        self.points_edit = {}
        self.slot_points_edit = {}

        self.sorted_diameters = []

        # here store the tools dict for the new excellon object
        self.new_tools = {}

        # dictionary to store the tool_row and diameters in plugin_table
        # it will be updated everytime self.build_ui() is called
        self.olddia_newdia = {}

        self.tool2tooldia = {}

        # this will store the value for the last selected tool, for use after clicking on canvas when the selection
        # is cleared but as a side effect also the selected tool is cleared
        self.last_tool_selected = None
        self.utility = []

        # this will flag if the Editor "tools" are launched from key shortcuts (True) or from menu toolbar (False)
        self.launched_from_shortcuts = False

        # this var will store the state of the toolbar before starting the editor
        self.toolbar_old_state = False

        if self.units == 'MM':
            self.tolerance = float(self.app.options.get(
                "global_tolerance",
                self.app.defaults.get(
                    "global_tolerance",
                    self.app.defaults.factory_defaults.get("global_tolerance")
                )
            ))
        else:
            self.tolerance = float(self.app.options.get(
                "global_tolerance",
                self.app.defaults.get(
                    "global_tolerance",
                    self.app.defaults.factory_defaults.get("global_tolerance")
                )
            )) / 20

        # VisPy Visuals
        self.shapes = self.canvas.new_shape_collection(layers=1)
        if self.canvas.big_cursor is True:
            self.tool_shape = self.canvas.new_shape_collection(layers=1, line_width=2)
        else:
            self.tool_shape = self.canvas.new_shape_collection(layers=1)

        # Remove from scene
        self.shapes.enabled = False
        self.tool_shape.enabled = False

        # ## List of selected shapes.
        self.selected = []

        self.move_timer = QtCore.QTimer()
        self.move_timer.setSingleShot(True)

        self.key = None  # Currently pressed key
        self.modifiers = None
        self.x = None  # Current mouse cursor clicked_pos
        self.y = None
        # Current snapped mouse clicked_pos
        self.snap_x = None
        self.snap_y = None
        self.clicked_pos = None

        # #############################################################################################################
        # Plugin Attributes
        # #############################################################################################################
        self.last_length = 0.0

        self.last_darray_type = None
        self.last_darray_size = None
        self.last_darray_lin_dir = None
        self.last_darray_circ_dir = None
        self.last_darray_pitch = None
        self.last_darray_lin_angle = None
        self.last_darray_circ_angle = None
        self.last_darray_radius = None

        self.last_sarray_radius = None
        self.last_sarray_circ_angle = None
        self.last_sarray_lin_angle = None
        self.last_sarray_pitch = None
        self.last_sarray_circ_dir = None
        self.last_sarray_lin_dir = None
        self.last_sarray_size = None
        self.last_sarray_type = None

        self.last_slot_length = None
        self.last_slot_direction = None
        self.last_slot_angle = None

        self.complete = False

        # read by MainGUI (the G shortcut); snap() shares the grid preferences of app.geo_editor.editor_options
        self.editor_options = {
            "global_gridx":     0.1,
            "global_gridy":     0.1,
            "snap_max":         0.05,
            "grid_snap":        True,
            "corner_snap":      False,
            "grid_gap_link":    True
        }
        self.editor_options.update(self.app.options)

        for option in self.editor_options:
            if option in self.app.options:
                self.editor_options[option] = self.app.options.get(
                    option,
                    self.app.defaults.get(option, self.app.defaults.factory_defaults.get(option))
                )

        self.data_defaults = {}

        # flag to show if the object was modified
        self.is_modified = False
        self.history = ExcellonHistory(self)
        self.worker_result.connect(self.history.finish)
        self.history_changed.connect(self.update_history_ui)

        self.edited_obj_name = ""

        # variable to store the total amount of drills per job
        self.tot_drill_cnt = 0
        self.tool_row = 0

        # variable to store the total amount of slots per job
        self.tot_slot_cnt = 0
        self.tool_row_slots = 0

        self.tool_row = 0

        # def entry2option(option, entry):
        #     self.editor_options[option] = float(entry.text())

        # #############################################################################################################
        # ######################### Excellon Editor Signals ###########################################################
        # #############################################################################################################

        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.diagnostics_button.clicked.connect(self.on_diagnostics)
        self.ui.history_list.activated.connect(self.on_history_row)
        self.ui.feature_apply.clicked.connect(self.refresh_feature_table)
        self.ui.feature_select.clicked.connect(lambda: self.on_feature_filter_action())
        self.ui.feature_delete.clicked.connect(lambda: self.on_feature_filter_action(delete=True))
        self.ui.feature_table.itemSelectionChanged.connect(self.on_feature_selection)
        # the app rebuilds the editors (New Project) while the UI stays: drop the actions of the previous editor
        self.remove_editor_actions()
        self.undo_action = self.app.ui.exc_editor_menu.addAction(_('Undo'), self.undo)
        self.redo_action = self.app.ui.exc_editor_menu.addAction(_('Redo'), self.redo)
        self.undo_action.setIcon(QtGui.QIcon(self.app.resource_location + '/undo64.png'))
        self.redo_action.setIcon(QtGui.QIcon(self.app.resource_location + '/redo64.png'))
        self.undo_action.setProperty(EDITOR_ACTION_PROPERTY, True)
        self.redo_action.setProperty(EDITOR_ACTION_PROPERTY, True)
        self.app.ui.exc_edit_toolbar.addAction(self.undo_action)
        self.app.ui.exc_edit_toolbar.addAction(self.redo_action)
        self.slot_endpoint_action = self.app.ui.exc_editor_menu.addAction(_('Edit slot endpoints'))
        self.slot_endpoint_action.setIcon(QtGui.QIcon(self.app.resource_location + '/move32.png'))
        self.slot_endpoint_action.setProperty(EDITOR_ACTION_PROPERTY, True)
        self.app.ui.exc_edit_toolbar.addAction(self.slot_endpoint_action)
        self.ui.slot_endpoint_drag.clicked.connect(lambda: self.select_tool('slot_endpoint'))
        self.ui.slot_endpoint_load.clicked.connect(self.load_slot_endpoints)
        self.ui.slot_endpoint_apply.clicked.connect(self.on_slot_endpoints_apply)
        self.transform_actions = {}
        for kind, label in (('rotate', _('Rotate selected features')), ('mirror_x', _('Mirror X')),
                            ('mirror_y', _('Mirror Y')), ('scale', _('Scale selected features')),
                            ('offset', _('Offset selected features'))):
            action =self.app.ui.exc_editor_menu.addAction(label)
            action.setToolTip(self.ui.transform_buttons[kind].toolTip())
            action.setIcon(QtGui.QIcon(self.app.resource_location + '/move32.png'))
            action.setProperty(EDITOR_ACTION_PROPERTY, True)
            action.triggered.connect(lambda checked=False, operation=kind: self.on_transform_features(operation))
            self.app.ui.exc_edit_toolbar.addAction(action)
            self.transform_actions[kind] = action
            self.ui.transform_buttons[kind].clicked.connect(
                lambda checked=False, operation=kind: self.on_transform_features(operation))
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)

        # connect the toolbar signals
        self.connect_exc_toolbar_signals()

        self.ui.convert_slots_btn.clicked.connect(self.on_slots_conversion)
        self.app.ui.delete_drill_btn.triggered.connect(self.on_delete_btn)
        self.ui.name_entry.returnPressed.connect(self.on_name_activate)
        self.ui.addtool_btn.clicked.connect(self.on_tool_add)
        self.ui.addtool_entry.editingFinished.connect(self.on_tool_add)
        self.ui.deltool_btn.clicked.connect(self.on_tool_delete)
        # self.ui.tools_table_exc.selectionModel().currentChanged.connect(self.on_row_selected)
        self.ui.tools_table_exc.cellPressed.connect(self.on_row_selected)
        self.ui.tools_table_exc.selectionModel().selectionChanged.connect(self.on_table_selection)  # noqa

        self.app.ui.exc_add_array_drill_menuitem.triggered.connect(self.exc_add_drill_array)
        self.app.ui.exc_add_drill_menuitem.triggered.connect(self.exc_add_drill)

        self.app.ui.exc_add_array_slot_menuitem.triggered.connect(self.exc_add_slot_array)
        self.app.ui.exc_add_slot_menuitem.triggered.connect(self.exc_add_slot)

        self.app.ui.exc_resize_drill_menuitem.triggered.connect(self.exc_resize_drills)
        self.app.ui.exc_copy_drill_menuitem.triggered.connect(self.exc_copy_drills)
        self.app.ui.exc_delete_drill_menuitem.triggered.connect(self.on_delete_btn)

        self.app.ui.exc_move_drill_menuitem.triggered.connect(self.exc_move_drills)
        self.ui.exit_editor_button.clicked.connect(lambda: self.app.on_editing_finished())

        # #############################################################################################################
        # ############################### TOOLS TABLE context menu ####################################################
        # #############################################################################################################
        self.ui.tools_table_exc.setupContextMenu()
        # self.ui.tools_table_exc.addContextMenu(
        #     _("Add"), self.on_aperture_add,
        #     icon=QtGui.QIcon(self.app.resource_location + "/plus16.png"))
        self.ui.tools_table_exc.addContextMenu(
            _("Delete"), lambda: self.on_tool_delete(),
            icon=QtGui.QIcon(self.app.resource_location + "/trash16.png"))

        self.app.log.debug("Initialization of the Excellon Editor is finished ...")

    def remove_editor_actions(self):
        """Remove the menu/toolbar actions added by a previous Excellon editor and cut their connections."""
        # the Excellon-only app actions are still connected to the previous (orphaned) editor
        ui = self.app.ui
        for action in (ui.select_drill_btn, ui.add_drill_btn, ui.add_drill_array_btn, ui.add_slot_btn,
                       ui.add_slot_array_btn, ui.resize_drill_btn, ui.copy_drill_btn, ui.move_drill_btn,
                       ui.delete_drill_btn, ui.exc_add_array_drill_menuitem, ui.exc_add_drill_menuitem,
                       ui.exc_add_array_slot_menuitem, ui.exc_add_slot_menuitem, ui.exc_resize_drill_menuitem,
                       ui.exc_copy_drill_menuitem, ui.exc_delete_drill_menuitem, ui.exc_move_drill_menuitem):
            try:
                action.triggered.disconnect()
            except (TypeError, RuntimeError):
                pass
        for container in (self.app.ui.exc_editor_menu, self.app.ui.exc_edit_toolbar):
            for action in container.actions():
                if action.property(EDITOR_ACTION_PROPERTY):
                    try:
                        action.triggered.disconnect()
                    except (TypeError, RuntimeError):
                        pass
                    container.removeAction(action)
                    action.deleteLater()

    def make_callback(self, thetool):
        def f():
            self.on_tool_select(thetool)

        return f

    def connect_exc_toolbar_signals(self) -> None:
        self.tools_exc.update({
            "drill_select":     {"button": self.app.ui.select_drill_btn,    "constructor": SelectEditorExc},
            "drill_add":        {"button": self.app.ui.add_drill_btn,       "constructor": DrillAdd},
            "drill_array":      {"button": self.app.ui.add_drill_array_btn, "constructor": DrillArray},
            "slot_add":         {"button": self.app.ui.add_slot_btn,        "constructor": SlotAdd},
            "slot_array":       {"button": self.app.ui.add_slot_array_btn,  "constructor": SlotArray},
            "slot_endpoint":    {"button": self.slot_endpoint_action,     "constructor": SlotEndpointEditorExc},
            "drill_resize":     {"button": self.app.ui.resize_drill_btn,    "constructor": ResizeEditorExc},
            "drill_copy":       {"button": self.app.ui.copy_drill_btn,      "constructor": CopyEditorExc},
            "drill_move":       {"button": self.app.ui.move_drill_btn,      "constructor": MoveEditorExc},
        })

        for tool in self.tools_exc:
            self.tools_exc[tool]["button"].triggered.connect(self.make_callback(tool))  # Events
            self.tools_exc[tool]["button"].setCheckable(True)  # Checkable

    @staticmethod
    def make_storage():
        # ## Shape storage.
        return AppGeoEditor.make_storage()

    def set_editor_ui(self):
        # updated units
        self.units = self.app.app_units.upper()
        # the default diagnostics tolerance follows the current units
        self.ui.diagnostics_tolerance.set_value(0.001 if self.units == 'MM' else 0.00004)

        self.olddia_newdia.clear()
        self.tool2tooldia.clear()

        # update the olddia_newdia dict to make sure we have an updated state of the plugin_table
        for key in self.points_edit:
            self.olddia_newdia[key] = key

        for key in self.slot_points_edit:
            if key not in self.olddia_newdia:
                self.olddia_newdia[key] = key

        sort_temp = []
        for diam in self.olddia_newdia:
            sort_temp.append(float(diam))
        self.sorted_diameters = sorted(sort_temp)

        # populate self.intial_table_rows dict with the tool number as keys and tool diameters as values
        if self.edited_obj.diameterless is False:
            for i in range(len(self.sorted_diameters)):
                tt_dia = self.sorted_diameters[i]
                self.tool2tooldia[i + 1] = tt_dia
        else:
            # the Excellon object has diameters that are bogus information, added by the application because the
            # Excellon file has no tool diameter information. In this case do not order the diameter in the table
            # but use the real order found in the edited_obj.tools
            for k, v in self.edited_obj.tools.items():
                tool_dia = float('%.*f' % (self.decimals, v['tooldia']))
                self.tool2tooldia[int(k)] = tool_dia

        # Init appGUI
        self.ui.addtool_entry.set_value(float(self.app.options.get(
            'excellon_editor_newdia',
            self.app.defaults.get(
                'excellon_editor_newdia',
                self.app.defaults.factory_defaults.get('excellon_editor_newdia')
            )
        )))

        self.last_darray_type = 'linear'
        self.last_darray_size = int(self.app.options.get(
            'excellon_editor_array_size',
            self.app.defaults.get(
                'excellon_editor_array_size',
                self.app.defaults.factory_defaults.get('excellon_editor_array_size')
            )
        ))
        self.last_darray_lin_dir = self.app.options.get(
            'excellon_editor_lin_dir',
            self.app.defaults.get(
                'excellon_editor_lin_dir',
                self.app.defaults.factory_defaults.get('excellon_editor_lin_dir')
            )
        )
        self.last_darray_circ_dir = self.app.options.get(
            'excellon_editor_circ_dir',
            self.app.defaults.get(
                'excellon_editor_circ_dir',
                self.app.defaults.factory_defaults.get('excellon_editor_circ_dir')
            )
        )
        self.last_darray_pitch = float(self.app.options.get(
            'excellon_editor_lin_pitch',
            self.app.defaults.get(
                'excellon_editor_lin_pitch',
                self.app.defaults.factory_defaults.get('excellon_editor_lin_pitch')
            )
        ))
        self.last_darray_lin_angle = float(self.app.options.get(
            'excellon_editor_lin_angle',
            self.app.defaults.get(
                'excellon_editor_lin_angle',
                self.app.defaults.factory_defaults.get('excellon_editor_lin_angle')
            )
        ))
        self.last_darray_circ_angle = float(self.app.options.get(
            'excellon_editor_circ_angle',
            self.app.defaults.get(
                'excellon_editor_circ_angle',
                self.app.defaults.factory_defaults.get('excellon_editor_circ_angle')
            )
        ))
        self.last_darray_radius = 0.0

        self.last_slot_length = self.app.options.get(
            'excellon_editor_slot_length',
            self.app.defaults.get(
                'excellon_editor_slot_length',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_length')
            )
        )
        self.last_slot_direction = self.app.options.get(
            'excellon_editor_slot_direction',
            self.app.defaults.get(
                'excellon_editor_slot_direction',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_direction')
            )
        )
        self.last_slot_angle = self.app.options.get(
            'excellon_editor_slot_angle',
            self.app.defaults.get(
                'excellon_editor_slot_angle',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_angle')
            )
        )

        self.last_sarray_type = 'linear'
        self.last_sarray_size = int(self.app.options.get(
            'excellon_editor_slot_array_size',
            self.app.defaults.get(
                'excellon_editor_slot_array_size',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_array_size')
            )
        ))
        self.last_sarray_lin_dir = self.app.options.get(
            'excellon_editor_slot_lin_dir',
            self.app.defaults.get(
                'excellon_editor_slot_lin_dir',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_lin_dir')
            )
        )
        self.last_sarray_circ_dir = self.app.options.get(
            'excellon_editor_slot_circ_dir',
            self.app.defaults.get(
                'excellon_editor_slot_circ_dir',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_circ_dir')
            )
        )
        self.last_sarray_pitch = float(self.app.options.get(
            'excellon_editor_slot_lin_pitch',
            self.app.defaults.get(
                'excellon_editor_slot_lin_pitch',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_lin_pitch')
            )
        ))
        self.last_sarray_lin_angle = float(self.app.options.get(
            'excellon_editor_slot_lin_angle',
            self.app.defaults.get(
                'excellon_editor_slot_lin_angle',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_lin_angle')
            )
        ))
        self.last_sarray_circ_angle = float(self.app.options.get(
            'excellon_editor_slot_circ_angle',
            self.app.defaults.get(
                'excellon_editor_slot_circ_angle',
                self.app.defaults.factory_defaults.get('excellon_editor_slot_circ_angle')
            )
        ))
        self.last_sarray_radius = 0.0

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            self.app.defaults.get(
                "global_app_level",
                self.app.defaults.factory_defaults.get("global_app_level")
            )
        )
        self.change_level(app_mode)

    def build_ui(self, first_run=None):

        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.ui.tools_table_exc.itemChanged.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.tools_table_exc.cellPressed.disconnect()
        except (TypeError, AttributeError):
            pass

        # updated units
        self.units = self.app.app_units.upper()

        # make a new name for the new Excellon object (the one with edited content)
        self.edited_obj_name = self.edited_obj.obj_options['name']
        self.ui.name_entry.set_value(self.edited_obj_name)

        sort_temp = []

        for diam in self.olddia_newdia:
            sort_temp.append(float(diam))
        self.sorted_diameters = sorted(sort_temp)

        # here, self.sorted_diameters will hold in a oblique way, the number of tools
        n = len(self.sorted_diameters)
        # we have (n+2) rows because there are 'n' tools, each a row, plus the last 2 rows for totals.
        self.ui.tools_table_exc.setRowCount(n + 2)

        self.tot_drill_cnt = 0
        self.tot_slot_cnt = 0

        self.tool_row = 0
        # this variable will serve as the real tool_number
        tool_id = 0
        cached_counts = getattr(self, '_exc_tool_counts', {})
        tool_counts = {}

        for tool_no in self.sorted_diameters:
            tool_id += 1
            drill_cnt = 0  # variable to store the nr of drills per tool
            slot_cnt = 0  # variable to store the nr of slots per tool

            if first_run is not True and tool_no in self.storage_dict:
                storage = self.storage_dict[tool_no]
                cached = cached_counts.get(tool_no)
                if cached is not None and cached[0] is storage and cached[1] == storage.revision:
                    drill_cnt, slot_cnt = cached[2:]
                else:
                    for shape in storage.get_objects():
                        drill_cnt += isinstance(shape.geo, MultiLineString)
                        slot_cnt += isinstance(shape.geo, Polygon)
                tool_counts[tool_no] = (storage, storage.revision, drill_cnt, slot_cnt)
            else:
                drill_cnt = len(self.points_edit.get(tool_no, ()))
                slot_cnt = len(self.slot_points_edit.get(tool_no, ()))
            self.tot_drill_cnt += drill_cnt
            self.tot_slot_cnt += slot_cnt

            idd = QtWidgets.QTableWidgetItem('%d' % int(tool_id))
            idd.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table_exc.setItem(self.tool_row, 0, idd)  # Tool name/id

            # Make sure that the drill diameter when in MM is with no more than 2 decimals
            # There are no drill bits in MM with more than 2 decimals diameter
            # For INCH the decimals should be no more than 4. There are no drills under 10mils
            dia = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals, self.olddia_newdia[tool_no]))
            dia.setData(QtCore.Qt.ItemDataRole.UserRole, tool_no)
            dia.setToolTip(repr(tool_no))

            dia.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

            drill_count = QtWidgets.QTableWidgetItem('%d' % drill_cnt)
            drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

            # if the slot number is zero is better to not clutter the GUI with zero's so we print a space
            if slot_cnt > 0:
                slot_count = QtWidgets.QTableWidgetItem('%d' % slot_cnt)
            else:
                slot_count = QtWidgets.QTableWidgetItem('')
            slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

            self.ui.tools_table_exc.setItem(self.tool_row, 1, dia)  # Diameter
            self.ui.tools_table_exc.setItem(self.tool_row, 2, drill_count)  # Number of drills per tool
            self.ui.tools_table_exc.setItem(self.tool_row, 3, slot_count)  # Number of drills per tool

            if first_run is True:
                # set now the last tool selected
                self.last_tool_selected = int(tool_id)

            self.tool_row += 1

        self._exc_tool_counts = tool_counts

        # make the diameter column editable
        for row in range(self.tool_row):
            self.ui.tools_table_exc.item(row, 1).setFlags(
                QtCore.Qt.ItemFlag.ItemIsEditable |
                QtCore.Qt.ItemFlag.ItemIsSelectable |
                QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table_exc.item(row, 2).setForeground(QtGui.QColor(0, 0, 0))
            self.ui.tools_table_exc.item(row, 3).setForeground(QtGui.QColor(0, 0, 0))

        # add a last row with the Total number of drills
        # HACK: made the text on this cell '9999' such it will always be the one before last when sorting
        # it will have to have the foreground color (font color) white
        empty = QtWidgets.QTableWidgetItem('9998')
        empty.setForeground(QtGui.QColor(255, 255, 255))

        empty.setFlags(empty.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)
        empty_b = QtWidgets.QTableWidgetItem('')
        empty_b.setFlags(empty_b.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)

        label_tot_drill_count = QtWidgets.QTableWidgetItem(_('Total Drills'))
        tot_drill_count = QtWidgets.QTableWidgetItem('%d' % self.tot_drill_cnt)

        label_tot_drill_count.setFlags(label_tot_drill_count.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)
        tot_drill_count.setFlags(tot_drill_count.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table_exc.setItem(self.tool_row, 0, empty)
        self.ui.tools_table_exc.setItem(self.tool_row, 1, label_tot_drill_count)
        self.ui.tools_table_exc.setItem(self.tool_row, 2, tot_drill_count)  # Total number of drills
        self.ui.tools_table_exc.setItem(self.tool_row, 3, empty_b)

        font = QtGui.QFont()
        font.setBold(True)
        # font.setWeight(75)

        for k in [1, 2]:
            self.ui.tools_table_exc.item(self.tool_row, k).setForeground(QtGui.QColor(127, 0, 255))
            self.ui.tools_table_exc.item(self.tool_row, k).setFont(font)

        self.tool_row += 1

        # add a last row with the Total number of slots
        # HACK: made the text on this cell '9999' such it will always be the last when sorting
        # it will have to have the foreground color (font color) white
        empty_2 = QtWidgets.QTableWidgetItem('9999')
        empty_2.setForeground(QtGui.QColor(255, 255, 255))

        empty_2.setFlags(empty_2.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)

        empty_3 = QtWidgets.QTableWidgetItem('')
        empty_3.setFlags(empty_3.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)

        label_tot_slot_count = QtWidgets.QTableWidgetItem(_('Total Slots'))
        tot_slot_count = QtWidgets.QTableWidgetItem('%d' % self.tot_slot_cnt)
        label_tot_slot_count.setFlags(label_tot_slot_count.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)
        tot_slot_count.setFlags(tot_slot_count.flags() ^ QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table_exc.setItem(self.tool_row, 0, empty_2)
        self.ui.tools_table_exc.setItem(self.tool_row, 1, label_tot_slot_count)
        self.ui.tools_table_exc.setItem(self.tool_row, 2, empty_3)
        self.ui.tools_table_exc.setItem(self.tool_row, 3, tot_slot_count)  # Total number of slots

        for kl in [1, 2, 3]:
            self.ui.tools_table_exc.item(self.tool_row, kl).setFont(font)
            self.ui.tools_table_exc.item(self.tool_row, kl).setForeground(QtGui.QColor(0, 70, 255))

        # all the tools are selected by default
        self.ui.tools_table_exc.selectColumn(0)
        #
        self.ui.tools_table_exc.resizeColumnsToContents()
        self.ui.tools_table_exc.resizeRowsToContents()

        vertical_header = self.ui.tools_table_exc.verticalHeader()
        # vertical_header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        vertical_header.hide()
        self.ui.tools_table_exc.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.tools_table_exc.horizontalHeader()
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        # horizontal_header.setStretchLastSection(True)

        # self.ui.tools_table_exc.setSortingEnabled(True)
        # sort by tool diameter
        self.ui.tools_table_exc.sortItems(1)

        # After sorting, to display also the number of drills in the right row we need to update self.initial_rows dict
        # with the new order. Of course the last 2 rows in the tool table are just for display therefore we don't
        # use them
        self.tool2tooldia.clear()
        for row in range(self.ui.tools_table_exc.rowCount() - 2):
            tool = int(self.ui.tools_table_exc.item(row, 0).text())
            diameter = table_diameter(self.ui.tools_table_exc, row)
            self.tool2tooldia[tool] = diameter

        self.ui.tools_table_exc.setMinimumHeight(self.ui.tools_table_exc.getHeight())
        self.ui.tools_table_exc.setMaximumHeight(self.ui.tools_table_exc.getHeight())

        # make sure no rows are selected so the user have to click the correct row, meaning selecting the correct tool
        self.ui.tools_table_exc.clearSelection()

        # Remove anything else in the GUI Properties Tab
        self.app.ui.properties_scroll_area.takeWidget()
        # Put ourselves in the GUI Properties Tab
        self.app.ui.properties_scroll_area.setWidget(self.ui.exc_edit_widget)
        # Switch notebook to Properties page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

        # we reactivate the signals after the after the tool adding as we don't need to see the tool been populated
        self.ui.tools_table_exc.itemChanged.connect(self.on_tool_edit)
        self.ui.tools_table_exc.cellPressed.connect(self.on_row_selected)

        self.sync_tool_rows()

    def sync_tool_rows(self):
        selected = set(self.selected)
        diameters = {dia for dia, storage in self.storage_dict.items()
                     if any(id(shape) in storage.indexes and
                            storage.objects[storage.indexes[id(shape)]] is shape for shape in selected)}
        table = self.ui.tools_table_exc
        blocker = QtCore.QSignalBlocker(table)
        selection_blocker = QtCore.QSignalBlocker(table.selectionModel())
        table.clearSelection()
        for row in range(table.rowCount() - 2):
            item = table.item(row, 1)
            if item is not None and table_diameter(table, row) in diameters:
                table.selectionModel().select(table.model().index(row, 0),
                                              QtCore.QItemSelectionModel.SelectionFlag.Select |
                                              QtCore.QItemSelectionModel.SelectionFlag.Rows)
        del selection_blocker, blocker
        show_selected_rows(table)

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
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)

            # Context Menu section
            # self.ui.tools_table_exc.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            # Context Menu section
            # self.ui.tools_table_exc.setupContextMenu()

    def on_transform_features(self, operation):
        reference = self.ui.transform_reference.currentData()
        if reference == 'point':
            reference = tuple(entry.get_value() for entry in self.ui.transform_point)
        if operation == 'offset':
            return self.transform_features(operation, tuple(entry.get_value() for entry in self.ui.transform_offset))
        value = self.ui.transform_angle.get_value() if operation == 'rotate' else self.ui.transform_scale.get_value()
        return self.transform_features(operation, value, reference)

    @excellon_operation(_('Transform features'), selected=True)
    def transform_features(self, operation, value=1.0, reference='origin'):
        """Transform native features, including circular width for uniform scaling."""
        if self.history.suspended:
            return 'fail'
        selected = set(self.selected)
        try:
            if self.in_action:
                raise ValueError(_('Finish the active drawing operation first.'))
            if operation == 'offset':
                # the value is the (dx, dy) translation in editor units
                offset = tuple(float(coordinate) for coordinate in value)
                if len(offset) != 2 or not all(math.isfinite(coordinate) for coordinate in offset):
                    raise ValueError(_('Invalid offset.'))
                if offset == (0.0, 0.0) or not selected:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % (
                        _('Nothing selected.') if not selected else _('The offset is zero.')))
                    return 'fail'
                value = 0.0
            else:
                value = float(value)
            if operation not in ('rotate', 'mirror_x', 'mirror_y', 'scale', 'offset') or not math.isfinite(value):
                raise ValueError(_('Invalid transform.'))
            if operation == 'scale' and value <= 0:
                raise ValueError(_('Scale must be positive and uniform.'))
            if not selected:
                raise ValueError(_('Nothing selected.'))
            if any(shape.geo is None or shape.geo.is_empty for shape in selected):
                raise ValueError(_('Empty Excellon feature.'))
            if reference == 'origin':
                origin = (0.0, 0.0)
            elif isinstance(reference, str) and reference in (
                    'selection', 'minimum', 'lower_left', 'lower_right', 'upper_right', 'upper_left'):
                bounds = [shape.geo.bounds for shape in selected]
                xmin, ymin = min(b[0] for b in bounds), min(b[1] for b in bounds)
                xmax, ymax = max(b[2] for b in bounds), max(b[3] for b in bounds)
                origin = {'selection': ((xmin + xmax) / 2, (ymin + ymax) / 2),
                          'minimum': (xmin, ymin), 'lower_left': (xmin, ymin),
                          'lower_right': (xmax, ymin), 'upper_right': (xmax, ymax),
                          'upper_left': (xmin, ymax)}[reference]
            else:
                origin = tuple(float(coordinate) for coordinate in reference)
            if len(origin) != 2 or not all(math.isfinite(coordinate) for coordinate in origin):
                raise ValueError(_('Invalid reference point.'))
            angle = math.radians(value)
            cosine, sine = math.cos(angle), math.sin(angle)

            def point_transform(point):
                x, y = point[0] - origin[0], point[1] - origin[1]
                if operation == 'rotate':
                    x, y = x * cosine - y * sine, x * sine + y * cosine
                elif operation == 'mirror_x':
                    y = -y
                elif operation == 'mirror_y':
                    x = -x
                elif operation == 'offset':
                    x, y = x + offset[0], y + offset[1]
                else:
                    x, y = x * value, y * value
                result = (x + origin[0], y + origin[1])
                if not all(math.isfinite(coordinate) for coordinate in result):
                    raise ValueError(_('Transform exceeds the coordinate range.'))
                return result

            removed, inserted, replacements = {}, {}, []
            replacement_map = {}
            found = set()
            for diameter, storage in self.storage_dict.items():
                for shape in sorted(selected, key=lambda item: storage.indexes.get(id(item), math.inf)):
                    index = storage.indexes.get(id(shape))
                    if index is None or storage.objects[index] is not shape:
                        continue
                    found.add(shape)
                    record = native_feature_properties(shape, diameter, 0)
                    target = diameter * value if operation == 'scale' else diameter
                    if not math.isfinite(target) or target <= 0:
                        raise ValueError(_('Invalid transformed tool diameter.'))
                    if record['type'] == 'drill':
                        x, y = point_transform(record['center'])
                        radius = target / 2
                        geometry = MultiLineString([[(x - radius, y), (x + radius, y)],
                                                    [(x, y - radius), (x, y + radius)]])
                        replacement = DrawToolShape(geometry)
                    else:
                        start, stop = map(point_transform, record['endpoints'])
                        quad_segs = circle_quad_segs_of(self, target / 2)
                        geometry = (LineString([start, stop]).buffer(target / 2, quad_segs) if start != stop
                                    else Point(start).buffer(target / 2, quad_segs))
                        replacement = DrawToolShape(geometry)
                        replacement.exc_slot = (start, stop)
                        replacement.exc_slot_geometry = geometry
                    native_feature_properties(replacement, target, 0)
                    replacement.exc_source_diameter = getattr(shape, 'exc_source_diameter', diameter)
                    if getattr(shape, GENERATED_DRILLS, False):
                        setattr(replacement, GENERATED_DRILLS, True)
                    removed.setdefault(diameter, []).append(shape)
                    inserted.setdefault(target, []).append(replacement)
                    replacements.append(replacement)
                    replacement_map[shape] = (target, replacement)
            if found != selected:
                raise ValueError(_('Selection changed. Retry the operation.'))
        except (ValueError, TypeError, OverflowError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Invalid Excellon transform'), str(error)))
            return 'fail'
        if (operation == 'rotate' and value % 360 == 0) or (operation == 'scale' and value == 1):
            return
        for diameter in dict.fromkeys([*removed, *inserted]):
            if diameter not in self.storage_dict:
                self.storage_dict[diameter] = self.make_storage()
                self.olddia_newdia[diameter] = diameter
            storage = self.storage_dict[diameter]
            previous = list(storage.get_objects())
            records = [replacement_map[shape][1] if shape in replacement_map else shape
                       for shape in previous if shape not in replacement_map or replacement_map[shape][0] == diameter]
            local = {replacement_map[shape][1] for shape in removed.get(diameter, [])
                     if replacement_map[shape][0] == diameter}
            records.extend(shape for shape in inserted.get(diameter, []) if shape not in local)
            replace_exc_records(storage, removed.get(diameter, []), records)
            # These lists are counters after loading; native positions live on the stored features.
            for counters, kind in ((self.points_edit, MultiLineString), (self.slot_points_edit, Polygon)):
                count = sum(isinstance(shape.geo, kind) for shape in records)
                values = counters.get(diameter, [])
                if diameter not in counters or len(values) != count:
                    counters[diameter] = values[:count] + [(0, 0)] * max(0, count - len(values))
            if not records:
                self.storage_dict.pop(diameter)
                self.olddia_newdia.pop(diameter, None)
                self.points_edit.pop(diameter, None)
                self.slot_points_edit.pop(diameter, None)
        self.selected = replacements
        self.tool2tooldia = {index: diameter for index, diameter in enumerate(sorted(self.storage_dict), 1)}
        self.last_tool_selected = next(key for key, diameter in self.tool2tooldia.items()
                                       if diameter == next(iter(inserted)))
        self.current_storage = self.storage_dict[next(iter(inserted))]
        self.is_modified = True
        self.build_ui()
        self.replot()

    @excellon_operation(_('Resize features'))
    def resize_features(self, shapes, diameter):
        try:
            diameter = float(diameter)
            if not math.isfinite(diameter) or diameter <= 0:
                raise ValueError
        except (TypeError, ValueError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Please enter a finite positive tool diameter.'))
            return 'fail'
        selected = set(shapes)
        if not selected:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('Nothing selected'))
            return 'fail'
        changes = {}
        replacements = []
        found = set()
        # Compute and validate the entire result before touching any tool storage.
        for source_dia, storage in self.storage_dict.items():
            for shape in storage.get_objects():
                if shape not in selected:
                    continue
                found.add(shape)
                if source_dia == diameter:
                    continue
                geo = shape.geo
                if geo is None or geo.is_empty or not geo.is_valid or source_dia <= 0:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _('Empty or invalid Excellon feature.'))
                    return 'fail'
                if isinstance(geo, MultiLineString):
                    new_shape = DrawToolShape(scale(geo, xfact=diameter / source_dia,
                                                   yfact=diameter / source_dia, origin='center'))
                elif isinstance(geo, Polygon):
                    record = feature_properties(shape, source_dia, 0)
                    start, stop = record['endpoints']
                    line = LineString([start, stop])
                    quad_segs = circle_quad_segs_of(self, source_dia / 2)
                    native = line.buffer(source_dia / 2, quad_segs) if start != stop else                         Point(start).buffer(source_dia / 2, quad_segs)
                    if native.hausdorff_distance(geo) > max(1e-8, source_dia * 0.005):
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _('The selected polygon is not a native slot.'))
                        return 'fail'
                    quad_segs = circle_quad_segs_of(self, diameter / 2)
                    resized = line.buffer(diameter / 2, quad_segs) if start != stop else                         Point(start).buffer(diameter / 2, quad_segs)
                    new_shape = DrawToolShape(resized)
                    new_shape.exc_slot = (start, stop)
                    new_shape.exc_slot_geometry = resized
                else:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _('Unsupported Excellon feature.'))
                    return 'fail'
                new_shape.exc_source_diameter = getattr(shape, 'exc_source_diameter', source_dia)
                if getattr(shape, GENERATED_DRILLS, False):
                    setattr(new_shape, GENERATED_DRILLS, True)
                changes.setdefault(source_dia, []).append(shape)
                replacements.append(new_shape)
        if found != selected:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Selection changed. Retry the operation.'))
            return 'fail'
        if not replacements:
            return
        if diameter not in self.storage_dict:
            self.storage_dict[diameter] = AppGeoEditor.make_storage()
            self.olddia_newdia[diameter] = diameter
        self.storage_dict[diameter].insert_many(replacements)
        for source_dia, removed in changes.items():
            self.storage_dict[source_dia].replace(removed=removed, inserted=[])
        for source_dia in set(changes) | {diameter}:
            records = list(self.storage_dict[source_dia].get_objects())
            self.points_edit[source_dia] = [shape.geo.centroid for shape in records
                                             if isinstance(shape.geo, MultiLineString)]
            self.slot_points_edit[source_dia] = [(0, 0) for shape in records if isinstance(shape.geo, Polygon)]
            if not records:
                self.storage_dict.pop(source_dia)
                self.olddia_newdia.pop(source_dia, None)
                self.points_edit.pop(source_dia, None)
                self.slot_points_edit.pop(source_dia, None)
        self.tool2tooldia = {index: dia for index, dia in enumerate(sorted(self.storage_dict), 1)}
        self.last_tool_selected = next(key for key, dia in self.tool2tooldia.items() if dia == diameter)
        self.current_storage = self.storage_dict[diameter]
        unchanged = [shape for shape in self.selected if shape in found and
                     all(shape not in removed for removed in changes.values())]
        self.selected = unchanged + replacements
        self.is_modified = True
        self.build_ui()
        self.replot()
        self.app.inform.emit('[success] %s' % _('Done.'))

    @excellon_operation(_('Add tool'))
    def on_tool_add(self, tooldia=None):
        if tooldia is not None and tooldia is not False:
            tool_dia = tooldia
        else:
            try:
                tool_dia = float(self.ui.addtool_entry.get_value())
            except ValueError:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    tool_dia = float(self.ui.addtool_entry.get_value().replace(',', '.'))
                except ValueError:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                    return

        try:
            tool_dia = float(tool_dia)
        except (ValueError, TypeError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
            return 'fail'
        if math.isfinite(tool_dia):
            # storage keys and tool2tooldia values use the rounded diameter
            tool_dia = float('%.*f' % (self.decimals, tool_dia))
        if not math.isfinite(tool_dia) or tool_dia <= 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Please enter a finite positive tool diameter.'))
            return 'fail'

        if tool_dia not in self.olddia_newdia:
            storage_elem = AppGeoEditor.make_storage()
            self.storage_dict[tool_dia] = storage_elem

            # self.olddia_newdia dict keeps the evidence on current tools diameters as keys and gets updated on values
            # each time a tool diameter is edited or added
            self.olddia_newdia[tool_dia] = tool_dia
            self.is_modified = True
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Tool already in the original or actual tool list.\n" 
                                                          "Save and reedit Excellon if you need to add this tool. "))
            return

        # since we add a new tool, we update also the initial state of the plugin_table through it's dictionary
        # we add a new entry in the tool2tooldia dict
        self.tool2tooldia[len(self.olddia_newdia)] = tool_dia

        self.app.inform.emit('[success] %s: %s %s' % (_("Added new tool with dia"), str(tool_dia), str(self.units)))

        self.build_ui()

        # make a quick sort through the tool2tooldia dict so we find which row to select
        row_to_be_selected = None
        for key in sorted(self.tool2tooldia):
            if self.tool2tooldia[key] == tool_dia:
                table = self.ui.tools_table_exc
                row_to_be_selected = next((row for row in range(table.rowCount() - 2)
                                           if table_diameter(table, row) == tool_dia), int(key) - 1)
                self.last_tool_selected = int(key)
                break
        try:
            self.ui.tools_table_exc.selectRow(row_to_be_selected)
        except TypeError as e:
            self.app.log.debug("AppExcEditor.on_tool_add() --> %s" % str(e))

    @excellon_operation(_('Delete tools'))
    def on_tool_delete(self, dia=None):
        self.is_modified = True
        deleted_tool_dia_list = []

        try:
            if dia is None or dia is False:
                # deleted_tool_dia = float(
                #     self.ui.tools_table_exc.item(self.ui.tools_table_exc.currentRow(), 1).text())
                for index in self.ui.tools_table_exc.selectionModel().selectedRows():
                    row = index.row()
                    deleted_tool_dia_list.append(table_diameter(self.ui.tools_table_exc, row))
            else:
                if isinstance(dia, list):
                    for dd in dia:
                        deleted_tool_dia_list.append(float(dd))
                else:
                    deleted_tool_dia_list.append(float(dia))
        except Exception:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select a tool in Tool Table"))
            return

        for deleted_tool_dia in deleted_tool_dia_list:

            removed = set(self.storage_dict[deleted_tool_dia].get_objects())
            self.selected[:] = [shape for shape in self.selected if shape not in removed]

            # delete the storage used for that tool
            storage_elem = AppGeoEditor.make_storage()
            self.storage_dict[deleted_tool_dia] = storage_elem
            self.storage_dict.pop(deleted_tool_dia, None)

            # I've added this flag_del variable because dictionary don't like
            # having keys deleted while iterating through them
            flag_del = []
            # self.points_edit.pop(deleted_tool_dia, None)
            for deleted_tool in self.tool2tooldia:
                if self.tool2tooldia[deleted_tool] == deleted_tool_dia:
                    flag_del.append(deleted_tool)

            if flag_del:
                for tool_to_be_deleted in flag_del:
                    # delete the tool
                    self.tool2tooldia.pop(tool_to_be_deleted, None)

                    # delete also the drills from points_edit dict just in case we add the tool again,
                    # we don't want to show the number of drills from before was deleter
                    self.points_edit[deleted_tool_dia] = []

            self.olddia_newdia.pop(deleted_tool_dia, None)
            self.slot_points_edit.pop(deleted_tool_dia, None)

            self.app.inform.emit('[success] %s: %s %s' %
                                 (_("Deleted tool with diameter"), str(deleted_tool_dia), str(self.units)))

        self.replot()
        # self.app.inform.emit("Could not delete selected tool")

        self.build_ui()

    @excellon_operation(_('Change tool diameter'))
    def on_tool_edit(self, item_changed):
        try:
            if item_changed.column() != 1:
                return
            new_dia = float(item_changed.text())
            old_dia = table_diameter(self.ui.tools_table_exc, item_changed.row())
            if not math.isfinite(new_dia) or new_dia <= 0:
                raise ValueError
        except (ValueError, TypeError, AttributeError, KeyError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Please enter a finite positive tool diameter.'))
            self.build_ui()
            return 'fail'
        if new_dia == old_dia:
            return
        shapes = list(self.storage_dict[old_dia].get_objects())
        if shapes:
            return self.resize_features(shapes, new_dia)
        if new_dia not in self.storage_dict:
            self.storage_dict[new_dia] = self.storage_dict[old_dia]
            self.olddia_newdia[new_dia] = new_dia
        self.storage_dict.pop(old_dia)
        self.olddia_newdia.pop(old_dia, None)
        self.points_edit.pop(old_dia, None)
        self.slot_points_edit.pop(old_dia, None)
        self.tool2tooldia = {index: dia for index, dia in enumerate(sorted(self.storage_dict), 1)}
        self.last_tool_selected = next(key for key, dia in self.tool2tooldia.items() if dia == new_dia)
        self.current_storage = self.storage_dict[new_dia]
        self.is_modified = True
        self.build_ui()
        self.replot()

    def on_name_activate(self):
        self.edited_obj_name = self.ui.name_entry.get_value()

    def activate(self):
        # adjust the status of the menu entries related to the editor
        self.app.ui.menueditedit.setDisabled(True)
        self.app.ui.menueditok.setDisabled(False)
        self.app.ui.on_history_changed('', '')
        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(False)
        self.app.ui.popmenu_save.setVisible(True)

        self.connect_canvas_event_handlers()

        # initialize working objects
        self.storage_dict = {}
        self.current_storage = []
        self.points_edit = {}
        self.sorted_diameters = []

        self.new_tools = {}

        self.olddia_newdia = {}

        self.shapes.enabled = True
        self.tool_shape.enabled = True
        # self.app.app_cursor.enabled = True

        # the Excellon editor has its own snap modes (see snap()), the app corner snap button does not apply
        self.app.ui.corner_snap_btn.setVisible(False)
        self.app.ui.snap_magnet.setVisible(True)

        self.app.ui.exc_editor_menu.setDisabled(False)
        self.app.ui.exc_editor_menu.menuAction().setVisible(True)

        self.app.ui.editor_exit_btn_ret_action.setVisible(True)
        self.app.ui.editor_start_btn.setVisible(False)
        self.app.ui.e_editor_cmenu.setEnabled(True)

        self.app.ui.exc_edit_toolbar.setDisabled(False)
        self.app.ui.exc_edit_toolbar.setVisible(True)
        # self.app.ui.grid_toolbar.setDisabled(False)

        # start with GRID toolbar activated
        if self.app.ui.grid_snap_btn.isChecked() is False:
            self.app.ui.grid_snap_btn.trigger()

        self.app.ui.popmenu_disable.setVisible(False)
        self.app.ui.cmenu_newmenu.menuAction().setVisible(False)
        self.app.ui.popmenu_properties.setVisible(False)
        self.app.ui.e_editor_cmenu.menuAction().setVisible(True)
        self.app.ui.g_editor_cmenu.menuAction().setVisible(False)
        self.app.ui.grb_editor_cmenu.menuAction().setVisible(False)

        self.app.ui.pop_menucolor.menuAction().setVisible(False)
        self.app.ui.popmenu_numeric_move.setVisible(False)
        self.app.ui.popmenu_move2origin.setVisible(False)

        # show the UI
        self.ui.drills_frame.show()

    def deactivate(self):
        self.history.reset()
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # adjust the status of the menu entries related to the editor
        self.app.ui.menueditedit.setDisabled(False)
        self.app.ui.menueditok.setDisabled(True)
        history_of(self.app).notify()
        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(True)
        self.app.ui.popmenu_save.setVisible(False)

        self.disconnect_canvas_event_handlers()
        self.clear()
        self.app.ui.exc_edit_toolbar.setDisabled(True)

        self.app.ui.corner_snap_btn.setVisible(False)
        self.app.ui.snap_magnet.setVisible(False)

        # set the Editor Toolbar visibility to what was before entering in the Editor
        self.app.ui.exc_edit_toolbar.setVisible(False) if self.toolbar_old_state is False \
            else self.app.ui.exc_edit_toolbar.setVisible(True)

        # Disable visuals
        self.shapes.enabled = False
        self.tool_shape.enabled = False
        # self.app.app_cursor.enabled = False

        self.app.ui.exc_editor_menu.setDisabled(True)
        self.app.ui.exc_editor_menu.menuAction().setVisible(False)

        self.app.ui.editor_exit_btn_ret_action.setVisible(False)
        self.app.ui.editor_start_btn.setVisible(True)

        self.app.ui.popmenu_disable.setVisible(True)
        self.app.ui.cmenu_newmenu.menuAction().setVisible(True)
        self.app.ui.popmenu_properties.setVisible(True)
        self.app.ui.g_editor_cmenu.menuAction().setVisible(False)
        self.app.ui.e_editor_cmenu.menuAction().setVisible(False)
        self.app.ui.grb_editor_cmenu.menuAction().setVisible(False)

        self.app.ui.pop_menucolor.menuAction().setVisible(True)
        self.app.ui.popmenu_numeric_move.setVisible(True)
        self.app.ui.popmenu_move2origin.setVisible(True)

        # Show original geometry
        if self.edited_obj:
            self.edited_obj.visible = True

        # hide the UI
        self.ui.drills_frame.hide()

    def connect_canvas_event_handlers(self):
        # ## Canvas events

        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.canvas.graph_event_connect('mouse_press', self.on_canvas_click)
        self.canvas.graph_event_connect('mouse_move', self.on_canvas_move)
        self.canvas.graph_event_connect('mouse_release', self.on_exc_click_release)

        # make sure that the shortcuts key and mouse events will no longer be linked to the methods from FlatCAMApp
        # but those from AppExcEditor
        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_double_click', self.app.on_mouse_double_click_over_plot)

        self.app.collection.view.clicked.disconnect()

        self.app.ui.popmenu_copy.triggered.disconnect()
        self.app.ui.popmenu_delete.triggered.disconnect()
        self.app.ui.popmenu_move.triggered.disconnect()

        self.app.ui.popmenu_copy.triggered.connect(self.exc_copy_drills)
        self.app.ui.popmenu_delete.triggered.connect(self.on_delete_btn)
        self.app.ui.popmenu_move.triggered.connect(self.exc_move_drills)

        # Excellon Editor
        self.app.ui.drill.triggered.connect(self.exc_add_drill)
        self.app.ui.drill_array.triggered.connect(self.exc_add_drill_array)

    def disconnect_canvas_event_handlers(self):
        # we restore the key and mouse control to FlatCAMApp method
        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_double_click', self.app.on_mouse_double_click_over_plot)
        self.app.collection.view.clicked.connect(self.app.collection.on_mouse_down)

        self.canvas.graph_event_disconnect('mouse_press', self.on_canvas_click)
        self.canvas.graph_event_disconnect('mouse_move', self.on_canvas_move)
        self.canvas.graph_event_disconnect('mouse_release', self.on_exc_click_release)

        try:
            self.app.ui.popmenu_copy.triggered.disconnect(self.exc_copy_drills)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.popmenu_delete.triggered.disconnect(self.on_delete_btn)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.popmenu_move.triggered.disconnect(self.exc_move_drills)
        except (TypeError, AttributeError):
            pass

        self.app.ui.popmenu_copy.triggered.connect(self.app.on_copy_command)
        self.app.ui.popmenu_delete.triggered.connect(self.app.on_delete)
        self.app.ui.popmenu_move.triggered.connect(self.app.obj_move)

        # Excellon Editor
        try:
            self.app.ui.drill.triggered.disconnect(self.exc_add_drill)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.drill_array.triggered.disconnect(self.exc_add_drill_array)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def clear(self):
        if isinstance(self.active_tool, SlotEndpointEditorExc):
            self.active_tool.clean_up()
        self._exc_rendered = {}
        self._exc_render_state = None
        self._exc_render_owned = {}
        self._exc_feature_rows = None
        self._slot_numeric_target = None
        self.active_tool = None
        # self.shape_buffer = []
        self.selected.clear()

        self.points_edit = {}
        self.new_tools = {}

        # self.storage_dict = {}

        self.shapes.clear(update=True)
        self.tool_shape.clear(update=True)

        # self.storage = AppExcEditor.make_storage()
        self.replot()

    @excellon_session
    def edit_fcexcellon(self, edited_obj):
        """
        Imports the geometry from the given FlatCAM Excellon object into the editor.

        :param edited_obj:  ExcellonObject object
        :return:            None
        """

        self.deactivate()
        self.activate()

        # create a reference to the edited object
        self.edited_obj = edited_obj

        # Hide original geometry
        edited_obj.visible = False

        if self.edited_obj:
            outname = self.edited_obj.obj_options['name']
        else:
            outname = ''

        app_option = lambda key: self.app.options.get(
            key,
            self.app.defaults.get(key, self.app.defaults.factory_defaults.get(key))
        )

        self.data_defaults = {
            "name":                         outname + '_drill',
            "plot":                         app_option("excellon_plot"),
            "solid":                        app_option("excellon_solid"),
            "multicolored":                 app_option("excellon_multicolored"),
            "merge_fuse_tools":             app_option("excellon_merge_fuse_tools"),
            "format_upper_in":              app_option("excellon_format_upper_in"),
            "format_lower_in":              app_option("excellon_format_lower_in"),
            "format_upper_mm":              app_option("excellon_format_upper_mm"),
            "lower_mm":                     app_option("excellon_format_lower_mm"),
            "zeros":                        app_option("excellon_zeros"),

            "tools_drill_tool_order":       app_option("tools_drill_tool_order"),
            "tools_drill_cutz":             app_option("tools_drill_cutz"),
            "tools_drill_multidepth":       app_option("tools_drill_multidepth"),
            "tools_drill_depthperpass":     app_option("tools_drill_depthperpass"),
            "tools_drill_travelz":          app_option("tools_drill_travelz"),

            "tools_drill_feedrate_z":       app_option("tools_drill_feedrate_z"),
            "tools_drill_feedrate_rapid":   app_option("tools_drill_feedrate_rapid"),

            "tools_drill_toolchange":       app_option("tools_drill_toolchange"),
            "tools_drill_toolchangez":      app_option("tools_drill_toolchangez"),
            "tools_drill_toolchangexy":     app_option("tools_drill_toolchangexy"),

            # Drill Slots
            "tools_drill_drill_slots":      app_option("tools_drill_drill_slots"),
            "tools_drill_drill_overlap":    app_option("tools_drill_drill_overlap"),
            "tools_drill_last_drill":       app_option("tools_drill_last_drill"),

            "tools_drill_endz":             app_option("tools_drill_endz"),
            "tools_drill_endxy":            app_option("tools_drill_endxy"),
            "tools_drill_startz":           app_option("tools_drill_startz"),
            "tools_drill_offset":           app_option("tools_drill_offset"),
            "tools_drill_spindlespeed":     app_option("tools_drill_spindlespeed"),
            "tools_drill_dwell":            app_option("tools_drill_dwell"),
            "tools_drill_dwelltime":        app_option("tools_drill_dwelltime"),
            "tools_drill_ppname_e":         app_option("tools_drill_ppname_e"),
            "tools_drill_z_p_depth":        app_option("tools_drill_z_p_depth"),
            "tools_drill_probe_plate":    app_option("tools_drill_probe_plate"),
            "tools_drill_feedrate_probe":   app_option("tools_drill_feedrate_probe"),
            "tools_drill_spindledir":       app_option("tools_drill_spindledir"),
            "tools_drill_f_plunge":         app_option("tools_drill_f_plunge"),
            "tools_drill_f_retract":        app_option("tools_drill_f_retract"),

            "tools_drill_area_exclusion":   app_option("tools_drill_area_exclusion"),
            "tools_drill_area_shape":       app_option("tools_drill_area_shape"),
            "tools_drill_area_strategy":    app_option("tools_drill_area_strategy"),
            "tools_drill_area_overz":       app_option("tools_drill_area_overz"),

            "tools_drill_dwell_bottom":     app_option("tools_drill_dwell_bottom"),
            "tools_drill_peck":             app_option("tools_drill_peck"),
            "tools_drill_peck_depth":       app_option("tools_drill_peck_depth"),
            "tools_drill_peck_retract":     app_option("tools_drill_peck_retract"),

            "tools_drill_spot":             app_option("tools_drill_spot"),
            "tools_drill_spot_dia":         app_option("tools_drill_spot_dia"),
            "tools_drill_spot_cutz":        app_option("tools_drill_spot_cutz"),
            "tools_drill_spot_feedrate_z": app_option("tools_drill_spot_feedrate_z"),

            "tools_drill_job_offset_xy":    app_option("tools_drill_job_offset_xy"),
            "tools_drill_mirror":           app_option("tools_drill_mirror"),
            "tools_drill_mirror_point":     app_option("tools_drill_mirror_point"),
            "tools_drill_array_columns":    app_option("tools_drill_array_columns"),
            "tools_drill_array_rows":       app_option("tools_drill_array_rows"),
            "tools_drill_array_pitch":      app_option("tools_drill_array_pitch"),

            "tools_drill_holes_selection":  app_option("tools_drill_holes_selection"),

            "tools_drill_min_power":        app_option("tools_drill_min_power"),
            "tools_drill_laser_on":         app_option("tools_drill_laser_on"),
        }

        # fill in self.default_data values from self.obj_options
        for opt_key, opt_val in self.app.options.items():
            if opt_key.find('excellon_') == 0:
                self.data_defaults[opt_key] = deepcopy(opt_val)

        # Normalize detached native values, never the source object, into editor units.
        # the editor units always follow the application units, never a value left by a previous session
        self.units = self.app.app_units.upper()
        factor = 1.0 if edited_obj.units.upper() == self.units else (25.4 if self.units == 'MM' else 1 / 25.4)
        native_tools = {}
        for tool, values in edited_obj.tools.items():
            native_tools[tool] = dict(values)
            native_tools[tool]['tooldia'] = values['tooldia'] * factor
            native_tools[tool]['drills'] = [scale(point, xfact=factor, yfact=factor, origin=(0, 0))
                                             for point in values.get('drills', [])]
            native_tools[tool]['slots'] = [tuple(scale(point, xfact=factor, yfact=factor, origin=(0, 0))
                                                  for point in slot) for slot in values.get('slots', [])]
        self.points_edit = {}
        # build the self.points_edit dict {dimaters: [point_list]}, with the ownership flags of each drill
        generated_edit = {}
        for tool, tool_dict in native_tools.items():
            tool_dia = self.dec_format(tool_dict['tooldia'])
            generated = set(tool_dict.get(GENERATED_DRILLS, ()))

            if 'drills' in tool_dict and tool_dict['drills']:
                for index, drill in enumerate(tool_dict['drills']):
                    try:
                        self.points_edit[tool_dia].append(drill)
                        generated_edit[tool_dia].append(index in generated)
                    except KeyError:
                        self.points_edit[tool_dia] = [drill]
                        generated_edit[tool_dia] = [index in generated]

        self.slot_points_edit = {}
        # build the self.slot_points_edit dict {dimaters: {"start": Point, "stop": Point}}
        for tool, tool_dict in native_tools.items():
            tool_dia = float('%.*f' % (self.decimals, tool_dict['tooldia']))

            if 'slots' in tool_dict and tool_dict['slots']:
                for slot in tool_dict['slots']:
                    try:
                        self.slot_points_edit[tool_dia].append({
                            "start": slot[0],
                            "stop": slot[1]
                        })
                    except KeyError:
                        self.slot_points_edit[tool_dia] = [{
                            "start": slot[0],
                            "stop": slot[1]
                        }]

        # Set selection tolerance
        # DrawToolShape.tolerance = fc_excellon.drawing_tolerance * 10

        self.select_tool("drill_select")

        # reset the tool table
        self.ui.tools_table_exc.clear()
        self.ui.tools_table_exc.setHorizontalHeaderLabels(['#', _('Diameter'), 'D', 'S'])
        self.last_tool_selected = None

        self.set_editor_ui()

        # now that we have data, create the appGUI interface and add it to the Tool Tab
        self.build_ui(first_run=True)

        # build_ui() connects itemChanged to on_tool_edit() exactly once, after populating the table

        # build the geometry for each tool-diameter, each drill will be represented by a '+' symbol
        # and then add it to the storage elements (each storage elements is a member of a list
        for tool_dia in self.points_edit:
            storage_elem = AppGeoEditor.make_storage()
            drill_shapes = []
            generated = generated_edit.get(tool_dia, ())
            for index, point in enumerate(self.points_edit[tool_dia]):
                # make a '+' sign, the line length is the tool diameter
                start_hor_line = ((point.x - (tool_dia / 2)), point.y)
                stop_hor_line = ((point.x + (tool_dia / 2)), point.y)
                start_vert_line = (point.x, (point.y - (tool_dia / 2)))
                stop_vert_line = (point.x, (point.y + (tool_dia / 2)))
                shape_geo = MultiLineString([(start_hor_line, stop_hor_line), (start_vert_line, stop_vert_line)])
                if shape_geo is not None:
                    shape = DrawToolShape(shape_geo)
                    if index < len(generated) and generated[index]:
                        setattr(shape, GENERATED_DRILLS, True)
                    drill_shapes.append(shape)
            # 1 batch for each tool
            self.add_exc_shape(drill_shapes, storage_elem)
            self.storage_dict[tool_dia] = storage_elem

        # slots
        for tool_dia in self.slot_points_edit:
            buf_value = float(tool_dia) / 2
            slot_shapes = []
            for elem_dict in self.slot_points_edit[tool_dia]:

                line_geo = LineString([elem_dict['start'], elem_dict['stop']])
                shape_geo = line_geo.buffer(buf_value, circle_quad_segs_of(self, buf_value))

                if tool_dia not in self.storage_dict:
                    storage_elem = AppGeoEditor.make_storage()
                    self.storage_dict[tool_dia] = storage_elem

                if shape_geo is not None:
                    shape = DrawToolShape(shape_geo)
                    shape.exc_slot = (tuple(elem_dict['start'].coords[0]), tuple(elem_dict['stop'].coords[0]))
                    shape.exc_slot_geometry = shape_geo
                    slot_shapes.append(shape)
            # 1 batch for each tool
            if slot_shapes:
                self.add_exc_shape(slot_shapes, self.storage_dict[tool_dia])

        self.replot()

        # add a first tool in the Tool Table but only if the Excellon Object is empty
        if not self.tool2tooldia:
            self.on_tool_add(self.dec_format(float(self.app.options.get(
                'excellon_editor_newdia',
                self.app.defaults.get(
                    'excellon_editor_newdia',
                    self.app.defaults.factory_defaults.get('excellon_editor_newdia')
                )
            ))))

    def update_fcexcellon(self, exc_obj, in_place=False, on_complete=None):
        """
        Save as New (in_place False): create a new Excellon object that contain the edited content of the source
        Excellon object; the source object is not changed.
        Save (in_place True): the edited content goes into the source object, see _save_excellon_in_place().

        :param exc_obj:     ExcellonObject
        :param in_place:    True to update the source object
        :param on_complete: in_place only: called on the GUI thread after the source is updated and the editor is
                            deactivated
        :return: None
        """

        if not self.history.ready() or self.history.pending is not None:
            if self.history.pending is not None:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("A save is already in progress."))
            return 'fail'

        # a second save (after a failed one) must not append to the tools of the first
        self.new_tools = {}

        # this dictionary will contain tooldia's as keys and a list of coordinates tuple as values
        # the values of this dict are coordinates of the holes (drills)
        edited_points = {}
        # per-diameter flags of the drills a plugin generated: a save must keep that ownership
        generated_points = {}

        """
         - this dictionary will contain tooldia's as keys and a list of another dicts as values
         - the dict element of the list has the structure
         ================  ====================================
        Key               Value
        ================  ====================================
        start             (Shapely.Point) Start point of the slot
        stop              (Shapely.Point) Stop point of the slot
        ================  ====================================
        """
        edited_slot_points = {}

        try:
            for storage_tooldia, storage in self.storage_dict.items():
                if not math.isfinite(storage_tooldia) or storage_tooldia <= 0:
                    raise ValueError('Invalid Excellon tool diameter.')
                for index, shape in enumerate(storage.get_objects()):
                    record = native_feature_properties(shape, storage_tooldia, index)
                    if record['type'] == 'drill':
                        edited_points.setdefault(storage_tooldia, []).append(record['center'])
                        generated_points.setdefault(storage_tooldia, []).append(
                            bool(getattr(record['shape'], GENERATED_DRILLS, False)))
                    else:
                        start, stop = record['endpoints']
                        edited_slot_points.setdefault(storage_tooldia, []).append({'start': start, 'stop': stop})
        except ValueError as error:
            self.app.log.error('Excellon editor save --> %s' % str(error))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                'The edited geometry cannot be represented by circular Excellon drills or slots.'))
            return 'fail'

        # recreate the drills and tools to be added to the new Excellon edited object
        # first, we look in the tool table if one of the tool diameters was changed then
        # append that a tuple formed by (old_dia, edited_dia) to a list
        changed_key = set()
        for initial_dia in self.olddia_newdia:
            edited_dia = self.olddia_newdia[initial_dia]
            if edited_dia != initial_dia:
                # for drills
                for old_dia in edited_points:
                    if old_dia == initial_dia:
                        changed_key.add((old_dia, edited_dia))
                # for slots
                for old_dia in edited_slot_points:
                    if old_dia == initial_dia:
                        changed_key.add((old_dia, edited_dia))
            # if the initial_dia is not in edited_points it means it is a new tool with no drill points
            # (and we have to add it)
            # because in case we have drill points it will have to be already added in edited_points
            # if initial_dia not in edited_points.keys():
            #     edited_points[initial_dia] = []

        for el in changed_key:
            if el[0] in edited_points:
                edited_points.setdefault(el[1], []).extend(edited_points.pop(el[0]))
                generated_points.setdefault(el[1], []).extend(generated_points.pop(el[0]))
            if el[0] in edited_slot_points:
                edited_slot_points.setdefault(el[1], []).extend(edited_slot_points.pop(el[0]))

        # Let's sort the edited_points dictionary by keys (diameters) and store the result in a zipped list
        # ordered_edited_points is a ordered list of tuples;
        # element[0] of the tuple is the diameter and
        # element[1] of the tuple is a list of coordinates (a tuple themselves)
        ordered_edited_points = sorted(zip(edited_points.keys(), edited_points.values()))

        current_tool = 0
        for tool_dia in ordered_edited_points:
            current_tool += 1

            # create the self.tools for the new Excellon object (the one with edited content)
            # each tool has the 'drills' and the 'slots' keys, even empty, like a tool of a parsed Excellon file
            if current_tool not in self.new_tools:
                self.new_tools[current_tool] = {'drills': [], 'slots': []}
            self.new_tools[current_tool]['tooldia'] = float(tool_dia[0])

            # add in self.tools the 'solid_geometry' key, the value (a list) is populated below
            self.new_tools[current_tool]['solid_geometry'] = []

            # create the self.drills for the new Excellon object (the one with edited content)
            generated = generated_points.get(tool_dia[0], ())
            for index, point in enumerate(tool_dia[1]):
                try:
                    self.new_tools[current_tool]['drills'].append(Point(point))
                except KeyError:
                    self.new_tools[current_tool]['drills'] = [Point(point)]
                if index < len(generated) and generated[index]:
                    self.new_tools[current_tool].setdefault(GENERATED_DRILLS, []).append(
                        len(self.new_tools[current_tool]['drills']) - 1)

                # repopulate the 'solid_geometry' for each tool
                poly = Point(point).buffer(float(tool_dia[0]) / 2.0,
                                           circle_quad_segs_of(self, float(tool_dia[0]) / 2.0, exc_obj))
                self.new_tools[current_tool]['solid_geometry'].append(poly)

        ordered_edited_slot_points = sorted(zip(edited_slot_points.keys(), edited_slot_points.values()))
        for tool_dia in ordered_edited_slot_points:

            tool_exist_flag = False
            for tool in self.new_tools:
                if tool_dia[0] == self.new_tools[tool]["tooldia"]:
                    current_tool = tool
                    tool_exist_flag = True
                    break

            if tool_exist_flag is False:
                # a new tool goes after the highest tool number; current_tool can be the number of an existing tool
                # found above for a previous slot diameter and current_tool + 1 would then overwrite the next tool
                current_tool = max(self.new_tools, default=0) + 1

                # create the self.tools for the new Excellon object (the one with edited content)
                if current_tool not in self.new_tools:
                    self.new_tools[current_tool] = {'drills': [], 'slots': []}
                self.new_tools[current_tool]['tooldia'] = float(tool_dia[0])

                # add in self.tools the 'solid_geometry' key, the value (a list) is populated below
                self.new_tools[current_tool]['solid_geometry'] = []

            # create the self.slots for the new Excellon object (the one with edited content)
            for coord_dict in tool_dia[1]:
                slot = (
                    Point(coord_dict['start']),
                    Point(coord_dict['stop'])
                )
                try:
                    self.new_tools[current_tool]['slots'].append(slot)
                except KeyError:
                    self.new_tools[current_tool]['slots'] = [slot]

                # repopulate the 'solid_geometry' for each tool
                poly = LineString([coord_dict['start'], coord_dict['stop']]).buffer(
                    float(tool_dia[0]) / 2.0, circle_quad_segs_of(self, float(tool_dia[0]) / 2.0, exc_obj)
                )
                self.new_tools[current_tool]['solid_geometry'].append(poly)

        for values in self.new_tools.values():
            values.setdefault('drills', [])
            values.setdefault('slots', [])
        if in_place:
            return self._save_excellon_in_place(exc_obj, on_complete)

        return AppExcEditor._queue_excellon_save(self, exc_obj, False, on_complete)

    def _queue_excellon_save(self, source, in_place, on_complete):
        """Snapshot on the GUI thread; workers never read mutable editor/session state."""
        outname = source.obj_options['name'] if in_place else edit_copy_name(self.edited_obj_name)
        tools = tools_with_data(source.tools, self.new_tools, self.olddia_newdia, self.data_defaults, self.decimals)
        units = getattr(self, 'units', source.units)
        factor = 1.0 if source.units.upper() == units else (25.4 if units == 'MM' else 1 / 25.4)
        # Splits inherit machining data. A merged diameter keeps its first owner's data.
        source_data = {}
        for values in source.tools.values():
            source_data.setdefault(round(values['tooldia'] * factor, self.decimals), values.get('data'))
        for values in tools.values():
            storage = self.storage_dict.get(values['tooldia'])
            first = next(iter(storage.get_objects()), None) if storage is not None else None
            diameter = getattr(first, 'exc_source_diameter', values['tooldia'])
            if source_data.get(round(diameter, self.decimals)):
                values['data'] = deepcopy(source_data[round(diameter, self.decimals)])
        options = {key: deepcopy(value) for key, value in source.obj_options.items()}
        attrs = {key: deepcopy(getattr(source, key)) for key in (
            'units', 'zeros', 'excellon_format_upper_mm', 'excellon_format_lower_mm',
            'excellon_format_upper_in', 'excellon_format_lower_in', 'excellon_units', '_fixed_steps'
        ) if hasattr(source, key)}
        attrs['units'] = units
        object_type, app = type(source), self.app

        def build():
            with app.proc_container.new('%s...' % _("Generating")):
                edited = object_type(outname, app=app)
                edited.tools = tools
                for key, value in options.items():
                    edited.obj_options[key] = value
                edited.obj_options['name'] = outname
                for key, value in attrs.items():
                    setattr(edited, key, value)
                if edited.create_geometry() == 'fail':
                    raise ValueError('create_geometry() failed')
                if not edited.regenerate_source(name=outname):
                    raise ValueError('regenerate_source() failed')
                edited.moveToThread(app.main_thread)
                return edited

        def commit(edited):
            if self.edited_obj is not source:
                # history.finish() clears the pending save after this returns, the editor is not blocked
                app.inform.emit('[WARNING_NOTCL] %s' % _("The edited object changed while saving. "
                                                         "The saved content was discarded."))
                return
            if in_place:
                AppExcEditor.apply_edited_excellon(source, edited)
            else:
                def initialize(new_obj, app_obj):
                    for key, value in attrs.items():
                        setattr(new_obj, key, value)
                    AppExcEditor.apply_edited_excellon(new_obj, edited)

                created = app.app_obj.new_object('excellon', outname, initialize)
                if created is None or created == 'fail':
                    raise ValueError('Excellon object creation failed')
            self.deactivate()
            self.edited_obj = None
            app.inform.emit('[success] %s' % _("Excellon editing finished."))
            if on_complete is not None:
                on_complete()

        return self.history.submit(build, commit)

    def _save_excellon_in_place(self, source, on_complete):
        """
        The edited content goes into the source Excellon object, which keeps its name, history uid, place in the
        project and options (the bounds are recomputed). The tools keep their 'data' (see tools_with_data()).

        The content is built by a worker task in a detached Excellon object, without touching the source. The source
        is changed on the GUI thread, in one step (apply_edited_excellon()). A failed build leaves the source
        unchanged and the editor open, with an error message.

        :param source:      the source ExcellonObject
        :param on_complete: called on the GUI thread after the source is updated and the editor is deactivated; it
                            calls refresh_edited_excellon() (after it cleans the Properties area)
        :return:            None
        """
        return AppExcEditor._queue_excellon_save(self, source, True, on_complete)

    @staticmethod
    def apply_edited_excellon(source, edited):
        """
        Put the content of a detached edited Excellon object into the source object. The name, history uid, tree
        position and obj_options of the source stay; the bounds in obj_options are recomputed. All the values are
        read before the first assignment, a failure leaves the source unchanged.

        :param source:  the source ExcellonObject
        :param edited:  the detached ExcellonObject made by _queue_excellon_save()
        :return:        None
        """
        bounds = get_finite_bounds(edited)
        tools = edited.tools
        solid_geometry = edited.solid_geometry
        source_file = edited.source_file
        keys = ('xmin', 'ymin', 'xmax', 'ymax')
        previous = (source.tools, source.solid_geometry, source.source_file, source.shape_indexes_dict)
        previous_units = source.units
        previous_bounds = {key: source.obj_options[key] for key in keys if key in source.obj_options}
        try:
            source.tools = tools
            source.units = edited.units
            source.solid_geometry = solid_geometry
            source.source_file = source_file
            source.shape_indexes_dict = {}
            for key, value in zip(keys, bounds):
                source.obj_options[key] = value
        except Exception:
            source.tools, source.solid_geometry, source.source_file, source.shape_indexes_dict = previous
            source.units = previous_units
            for key in keys:
                if key in previous_bounds:
                    source.obj_options[key] = previous_bounds[key]
                else:
                    source.obj_options.pop(key, None)
            raise

    @staticmethod
    def refresh_edited_excellon(source):
        """
        Refresh the GUI of a source object after apply_edited_excellon(): tools table, plot, Properties.
        """
        source.build_ui()
        source.plot()
        source.app.app_obj.object_changed.emit(source)

    def on_tool_select(self, tool):
        """
        Behavior of the toolbar. Tool initialization.

        :rtype : None
        """
        if isinstance(self.active_tool, SlotEndpointEditorExc):
            self.active_tool.clean_up()
        current_tool = tool

        self.app.log.debug("on_tool_select('%s')" % tool)

        if self.last_tool_selected is None and current_tool != 'drill_select':
            # self.draw_app.select_tool('drill_select')
            self.complete = True
            current_tool = 'drill_select'
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. There is no Tool/Drill selected"))

        # This is to make the group behave as radio group
        if current_tool in self.tools_exc:
            if self.tools_exc[current_tool]["button"].isChecked():
                self.app.log.debug("%s is checked." % current_tool)
                for t in self.tools_exc:
                    if t != current_tool:
                        self.tools_exc[t]["button"].setChecked(False)

                # this is where the Editor toolbar classes (button's) are instantiated
                self.active_tool = self.tools_exc[current_tool]["constructor"](self)
                # self.app.inform.emit(self.active_tool.start_msg)
            else:
                self.app.log.debug("%s is NOT checked." % current_tool)
                for t in self.tools_exc:
                    self.tools_exc[t]["button"].setChecked(False)

                self.select_tool('drill_select')
                self.active_tool = SelectEditorExc(self)

    def get_pick_index(self):
        """
        :return: (tree, refs): tree is a STRtree on the pick areas of the features of all the tools (None if there are
                 none), refs is the list of the shapes in the same order. The index is made again only when a tool
                 storage changed.
        """
        state = tuple((dia, id(storage), getattr(storage, 'revision', None))
                      for dia, storage in self.storage_dict.items())
        cache = getattr(self, '_pick_cache', None)
        if cache is not None and cache[0] == state:
            return cache[1], cache[2]

        refs, areas = [], []
        for storage in self.storage_dict.values():
            for shape in storage.get_objects():
                geo = shape.geo
                if not isinstance(geo, BaseGeometry) or geo.is_empty:
                    continue
                if isinstance(geo, MultiLineString):
                    # a drill is the cross of its circle: the whole circle is the pick area
                    minx, miny, maxx, maxy = geo.bounds
                    geo = Point((minx + maxx) / 2, (miny + maxy) / 2).buffer(max(maxx - minx, maxy - miny) / 2)
                refs.append(shape)
                areas.append(geo)
        tree = STRtree(areas) if refs else None
        self._pick_cache = (state, tree, refs, areas)
        self._pick_cycle = None
        return tree, refs

    def pick_candidates(self, point):
        """
        All the features under (or near, PICK_TOLERANCE_PX screen pixels) a point, the best first: the nearest, then
        the smaller area (a drill on a slot is picked before the slot), then the storage order.

        :param point:   (x, y) in plot units
        :return:        list of DrawToolShape
        """
        tree, refs = self.get_pick_index()
        if tree is None:
            return []
        areas = self._pick_cache[3]

        try:
            tol = PICK_TOLERANCE_PX * self.canvas.pixel_size()
        except (AttributeError, TypeError):
            tol = 0
        if not tol or not math.isfinite(tol):
            tol = 0
        pt = Point(point[0], point[1])
        found = []
        for index in tree.query(box(point[0] - tol, point[1] - tol, point[0] + tol, point[1] + tol)):
            distance = pt.distance(areas[int(index)])
            if distance <= tol:
                found.append((round(distance, 9), areas[int(index)].area, int(index)))
        found.sort()
        return [refs[index] for distance, area, index in found]

    def pick_cycle_at(self, point):
        """
        Pick a feature, and go to the next candidate when the click is repeated at the same place (less than
        CLICK_DRAG_THRESHOLD_PX screen pixels from the last pick, same candidates, same storages).

        :return: (hit, previous, position, count): hit is the picked shape or None; previous is the shape that the last
                 click picked when this click is a repeat, else None; position is the index of hit in the candidates;
                 count is the number of candidates
        """
        candidates = self.pick_candidates(point)
        if not candidates:
            self._pick_cycle = None
            return None, None, 0, 0
        ids = tuple(id(shape) for shape in candidates)
        state = self._pick_cache[0]
        last = getattr(self, '_pick_cycle', None)
        position, previous = 0, None
        if last is not None and len(candidates) > 1 and last['ids'] == ids and last['state'] == state:
            try:
                moved = math.hypot(point[0] - last['point'][0], point[1] - last['point'][1]) / self.canvas.pixel_size()
            except (AttributeError, TypeError, ZeroDivisionError):
                moved = float('inf')
            if moved < CLICK_DRAG_THRESHOLD_PX:
                position = (last['position'] + 1) % len(candidates)
                previous = candidates[last['position']]
        self._pick_cycle = {'point': tuple(point), 'ids': ids, 'state': state, 'position': position}
        return candidates[position], previous, position, len(candidates)

    def click_select(self, point):
        """
        Select the feature under a click. With the multiple selection key, the click adds the feature to the
        selection or removes it; without the key, the feature is the new selection. A click where there is no feature
        clears the selection. A repeated click at the same place selects the next feature under the cursor. The
        tool table follows the selection.

        :param point:   (x, y) in plot units
        """
        obj_to_add, previous, position, count = self.pick_cycle_at(point)
        if obj_to_add is None:
            self.selected = []
        else:
            key_modifier = QtWidgets.QApplication.keyboardModifiers()
            if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
                mod_key = 'Shift'
            elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
                mod_key = 'Control'
            else:
                mod_key = None

            if mod_key == self.app.options.get(
                    "global_mselect_key",
                    self.app.defaults.get(
                        "global_mselect_key",
                        self.app.defaults.factory_defaults.get("global_mselect_key")
                    )):
                if previous is not None:
                    self.selected = [shape for shape in self.selected if shape is not previous]
                    if not any(shape is obj_to_add for shape in self.selected):
                        self.selected.append(obj_to_add)
                # a single candidate still toggles with the multiple selection key
                elif any(shape is obj_to_add for shape in self.selected):
                    self.selected = [shape for shape in self.selected if shape is not obj_to_add]
                else:
                    self.selected.append(obj_to_add)
            else:
                self.selected = [obj_to_add]
            if count > 1:
                self.app.inform.emit('%s %d / %d. %s' % (
                    _("Feature"), position + 1, count, _("Click again at the same place to select the next one.")))
        self.sync_tool_table_selection()

    def sync_tool_table_selection(self):
        """Select in the tools table the rows of the tools that have selected features."""
        table = self.ui.tools_table_exc
        table.clearSelection()
        selected = set(self.selected)
        tools = {dia for dia, storage in self.storage_dict.items()
                 if any(id(shape) in storage.indexes and
                        storage.objects[storage.indexes[id(shape)]] is shape for shape in selected)}
        try:
            table.cellPressed.disconnect()
        except (TypeError, AttributeError):
            pass
        selection_mode = table.selectionMode()
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        try:
            for key, dia in self.tool2tooldia.items():
                if dia in tools:
                    row = next((row for row in range(table.rowCount() - 2)
                                if table_diameter(table, row) == dia), int(key) - 1)
                    table.selectRow(row)
                    self.last_tool_selected = int(key)
        finally:
            table.setSelectionMode(selection_mode)
            table.cellPressed.connect(self.on_row_selected)

    def on_row_selected(self, row, col):
        key_modifier =QtWidgets.QApplication.keyboardModifiers()
        if self.app.options.get(
                "global_mselect_key",
                self.app.defaults.get(
                    "global_mselect_key",
                    self.app.defaults.factory_defaults.get("global_mselect_key")
                )) == 'Control':
            modifier_to_use = Qt.KeyboardModifier.ControlModifier
        else:
            modifier_to_use = Qt.KeyboardModifier.ShiftModifier

        if key_modifier == modifier_to_use:
            pass
        else:
            self.selected.clear()

        try:
            self.last_tool_selected = int(self.ui.tools_table_exc.item(row, 0).text())

            selected_dia = self.tool2tooldia[self.last_tool_selected]
            for obj in self.storage_dict[selected_dia].get_objects():
                self.selected.append(obj)
        except Exception as e:
            self.app.log.error(str(e))

        self.replot()

    def on_table_selection(self):
        selected_rows = self.ui.tools_table_exc.selectionModel().selectedRows(0)

        if len(selected_rows) == self.ui.tools_table_exc.rowCount():
            for row in range(self.ui.tools_table_exc.rowCount() - 2):   # last 2 columns have no diameter
                sel_dia = table_diameter(self.ui.tools_table_exc, row)
                for obj in self.storage_dict[sel_dia].get_objects():
                    self.selected.append(obj)
            self.replot()
            return True
        return False

    def on_canvas_click(self, event):
        """
        event.pos has canvas screen coordinates.

        :param event:       Event object dispatched by VisPy
        :return:            None
        """
        event_pos = event.pos

        if event.button == 1:
            self.clicked_pos = self.canvas.translate_coords(event_pos)
            if self.app.grid_status() and self.active_tool.name not in ('drill_select', 'slot_endpoint'):
                self.clicked_pos = self.snap(self.clicked_pos[0], self.clicked_pos[1])
            else:
                self.clicked_pos = (self.clicked_pos[0], self.clicked_pos[1])

            self.on_canvas_click_left_handler()

    @excellon_operation(_('Draw features'), generic=True)
    def on_canvas_click_left_handler(self, custom_pos=None):
        click_position = custom_pos if custom_pos is not None else self.clicked_pos
        origin = self.snap(*click_position) if self.app.grid_status() else click_position
        self._delta_origin = tuple(origin)
        self.app.dx = self.app.dy = 0
        self.app.mouse_pos = list(origin)
        self.app.ui.update_location_labels(0, 0, *origin)
        self.app.plotcanvas.on_update_text_hud(0, 0, *origin)
        if self.active_tool is not None:
            # Dispatch event to active_tool
            self.active_tool.click(click_position)

            # If it is a shape generating tool
            if isinstance(self.active_tool, FCShapeTool) and self.active_tool.complete:
                if self.current_storage is not None:
                    self.on_exc_shape_complete(self.current_storage)
                    self.build_ui()

                # MS: always return to the Select Tool if modifier key is not pressed
                # else return to the current tool
                if self.app.options.get(
                        "global_mselect_key",
                        self.app.defaults.get(
                            "global_mselect_key",
                            self.app.defaults.factory_defaults.get("global_mselect_key")
                        )) == 'Control':
                    modifier_to_use = Qt.KeyboardModifier.ControlModifier
                else:
                    modifier_to_use = Qt.KeyboardModifier.ShiftModifier

                # if modifier key is pressed then we add to the selected list the current shape but if it's already
                # in the selected list, we removed it. Therefore, first click selects, second deselects.
                if QtWidgets.QApplication.keyboardModifiers() == modifier_to_use:
                    self.select_tool(self.active_tool.name)
                else:
                    # return to Select tool but not for FCDrillAdd or SlotAdd
                    if isinstance(self.active_tool, DrillAdd) or isinstance(self.active_tool, SlotAdd):
                        self.select_tool(self.active_tool.name)
                    else:
                        self.select_tool("drill_select")
                    return

            if isinstance(self.active_tool, SelectEditorExc):
                # self.app.log.debug("Replotting after click.")
                self.replot()
        else:
            self.app.log.debug("No active tool to respond to click!")

    def on_exc_click_release(self, event):
        """
        Handler of the "mouse_release" event.
        It will pop-up the context menu on right mouse click unless there was a panning move (decided in the
        "mouse_move" event handler) and only if the current tool is the Select tool.
        It will 'close' a Editor tool if it is the case.

        :param event:       Event object dispatched by VisPy SceneCavas
        :return:            None
        """

        event_pos = event.pos
        right_button = 2

        pos_canvas = self.canvas.translate_coords(event_pos)

        if self.app.grid_status() and self.active_tool.name != 'drill_select':
            pos = self.snap(pos_canvas[0], pos_canvas[1])
        else:
            pos = (pos_canvas[0], pos_canvas[1])

        # if the released mouse button was RMB then test if it was a panning motion or not, if not it was a context
        # canvas menu
        try:
            if event.button == right_button:  # right click
                if self.app.ui.popMenu.mouse_is_panning is False:
                    try:
                        QtGui.QGuiApplication.restoreOverrideCursor()
                    except Exception:
                        pass
                    if self.active_tool.complete is False and not isinstance(self.active_tool, SelectEditorExc):
                        self.active_tool.complete = True
                        self.in_action = False
                        self.delete_utility_geometry()
                        self.app.inform.emit('[success] %s' % _("Done."))
                        self.select_tool('drill_select')
                    else:
                        if isinstance(self.active_tool, DrillAdd):
                            self.active_tool.complete = True
                            self.in_action = False
                            self.delete_utility_geometry()
                            self.app.inform.emit('[success] %s' % _("Done."))
                            self.select_tool('drill_select')

                        self.app.cursor = QtGui.QCursor()
                        self.app.populate_cmenu_grids()
                        self.app.ui.popMenu.popup(self.app.cursor.pos())

        except Exception as e:
            self.app.log.error("AppExcEditor.on_exc_click_release() RMB click --> Error: %s" % str(e))
            raise

        # if the released mouse button was LMB then test if we had a right-to-left selection or a left-to-right
        # selection and then select a type of selection ("enclosing" or "touching")
        if event.button == 1:  # left click
            if isinstance(self.active_tool, SlotEndpointEditorExc):
                self.app.selection_type = None
                self.active_tool.click_release(pos)
                return
            if self.app.selection_type is not None:
                try:
                    self.draw_selection_area_handler(self.clicked_pos, pos, self.app.selection_type)
                except Exception as e:
                    self.app.log.error("AppExcEditor.on_exc_click_release() LMB click --> Error: %s" % str(e))
                    raise
                self.app.selection_type = None
            elif isinstance(self.active_tool, SelectEditorExc):
                self.active_tool.click_release((self.clicked_pos[0], self.clicked_pos[1]))

                # if there are selected objects then plot them
                if self.selected:
                    self.replot()

            click_position = pos if pos is not None else self.clicked_pos
            if self.active_tool is not None and self.active_tool.name != 'drill_select':
                # Dispatch event to active_tool
                try:
                    self.active_tool.click_release(click_position)
                except AttributeError:
                    pass

    def on_canvas_move(self, event):
        """
        Called on 'mouse_move' event.
        It updates the mouse cursor if the grid snapping is ON.
        It decides if we have a mouse drag and if it is done with the right mouse click. Then it passes this info to a
        class object which is used in the "mouse_release" handler to decide if to pop-up the context menu or not.
        It draws utility_geometry for the Editor tools.
        Update the position labels from status bar.
        Decide if we have a right to left or a left to right mouse drag with left mouse button and call a function
        that will draw a selection shape on canvas.

        event.pos have canvas screen coordinates

        :param event:       Event object dispatched by VisPy SceneCavas
        :return:            None
        """

        if not self.app.plotcanvas.native.hasFocus():
            self.app.plotcanvas.native.setFocus()

        event_pos = event.pos
        event_is_dragging = event.is_dragging
        right_button = 2

        pos = self.canvas.translate_coords(event_pos)
        self.x = pos[0]
        self.y = pos[1]

        self.app.ui.popMenu.mouse_is_panning = False

        # if the RMB is clicked and mouse is moving over plot then 'panning_action' is True
        if event.button == right_button and event_is_dragging == 1:
            self.app.ui.popMenu.mouse_is_panning = True
            return

        try:
            x = float(pos[0])
            y = float(pos[1])
        except TypeError:
            return

        if self.active_tool is None:
            return

        # ## Snap coordinates
        x, y = self.snap(x, y)
        # Update the cursor once, before constructing the preview geometry.
        self.app.app_cursor.set_data(np.asarray([(x, y)]), symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                     edge_width=self.app.options.get(
                                         "global_cursor_width",
                                         self.app.defaults.get(
                                             "global_cursor_width",
                                             self.app.defaults.factory_defaults.get("global_cursor_width")
                                         )
                                     ),
                                     size=self.app.options.get(
                                         "global_cursor_size",
                                         self.app.defaults.get(
                                             "global_cursor_size",
                                             self.app.defaults.factory_defaults.get("global_cursor_size")
                                         )
                                     ))

        self.snap_x = x
        self.snap_y = y

        if self.clicked_pos is None:
            self.clicked_pos = (0, 0)
        origin = getattr(self, '_delta_origin', (0, 0))
        self.app.dx = x - origin[0]
        self.app.dy = y - origin[1]

        # # update the position label in the infobar since the APP mouse event handlers are disconnected
        # self.app.ui.position_label.setText("&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
        #                                    "<b>Y</b>: %.4f&nbsp;" % (x, y))
        # # # update the reference position label in the infobar since the APP mouse event handlers are disconnected
        # self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
        #                                        "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (self.app.dx, self.app.dy))
        self.app.ui.update_location_labels(self.app.dx, self.app.dy, x, y)

        # units = self.app.app_units.lower()
        # self.app.plotcanvas.text_hud.text = \
        #     'Dx:\t{:<.4f} [{:s}]\nDy:\t{:<.4f} [{:s}]\n\nX:  \t{:<.4f} [{:s}]\nY:  \t{:<.4f} [{:s}]'.format(
        #         self.app.dx, units, self.app.dy, units, x, units, y, units)
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, x, y)

        # ## Utility geometry (animated)
        # self.update_utility_geometry(data=(x, y))

        self.update_utility_geometry(data=(x, y))
        if self.active_tool.name in [
            'drill_add', 'drill_array', 'slot_add', 'slot_array', 'drill_copy', 'drill_resize', 'drill_move'
        ]:
            try:
                self.active_tool.draw_cursor_data(pos=(x, y))
            except AttributeError:
                # this can happen if the method is not implemented yet for the active_tool
                pass

        # ## Selection area on canvas section # ##
        if event_is_dragging == 1 and event.button == 1:
            # I make an exception for FCDrillAdd and DrillArray because clicking and dragging while making regions
            # can create strange issues. Also for SlotAdd and SlotArray
            if isinstance(self.active_tool, SlotEndpointEditorExc) or \
                    isinstance(self.active_tool, DrillAdd) or isinstance(self.active_tool, DrillArray) or \
                    isinstance(self.active_tool, SlotAdd) or isinstance(self.active_tool, SlotArray):
                self.app.selection_type = None
            else:
                dx = pos[0] - self.clicked_pos[0]
                if dx < 0:
                    self.app.draw_moving_selection_shape((self.clicked_pos[0], self.clicked_pos[1]), (x, y),
                                                         color=self.app.options.get(
                                                             "global_alt_sel_line",
                                                             self.app.defaults.get(
                                                                 "global_alt_sel_line",
                                                                 self.app.defaults.factory_defaults.get("global_alt_sel_line")
                                                             )
                                                         ),
                                                         face_color=self.app.options.get(
                                                             'global_alt_sel_fill',
                                                             self.app.defaults.get(
                                                                 'global_alt_sel_fill',
                                                                 self.app.defaults.factory_defaults.get('global_alt_sel_fill')
                                                             )
                                                         ))
                    self.app.selection_type = False
                else:
                    self.app.draw_moving_selection_shape((self.clicked_pos[0], self.clicked_pos[1]), (x, y))
                    self.app.selection_type = True
        else:
            self.app.selection_type = None

    @excellon_operation(_('Add features'))
    def add_exc_shape(self, shp, storage):
        """
        Adds a shape (or a list of shapes) to a specified shape storage. The shapes go in the storage in 1 batch: an
        empty storage is bulk loaded.

        :param shp:       Shape to be added.
        :type shp:        DrawToolShape, list
        :param storage:     object where to store the shapes
        :return:            None
        """
        to_insert = []
        self._collect_exc_shape(shp, to_insert)
        if to_insert:
            storage.insert_many(to_insert)

    def _collect_exc_shape(self, shp, to_insert):
        """
        Checks a shape for add_exc_shape(). A utility shape goes to self.utility; a shape for the storage goes to
        to_insert.

        :param shp:         DrawToolShape or a list of them
        :param to_insert:   list that collects the shapes for the storage
        :return:            None
        """
        # List of DrawToolShape?
        if isinstance(shp, list):
            for subshape in shp:
                self._collect_exc_shape(subshape, to_insert)
            return

        assert isinstance(shp, DrawToolShape), \
            "Expected a DrawToolShape, got %s" % str(type(shp))

        assert shp.geo is not None, \
            "Shape object has empty geometry (None)"

        assert (isinstance(shp.geo, list) and len(shp.geo) > 0) or not isinstance(shp.geo, list), \
            "Shape objects has empty geometry ([])"

        if isinstance(shp, DrawToolUtilityShape):
            self.utility.append(shp)
        else:
            to_insert.append(shp)

    def add_shape(self, shp):
        """
        Adds a shape to the shape storage.

        :param shp: Shape to be added.
        :type shp:  DrawToolShape
        :return:    None
        """

        # List of DrawToolShape?
        if isinstance(shp, list):
            for subshape in shp:
                self.add_shape(subshape)
            return

        assert isinstance(shp, DrawToolShape), \
            "Expected a DrawToolShape, got %s" % type(shp)

        assert shp.geo is not None, \
            "Shape object has empty geometry (None)"

        assert (isinstance(shp.geo, list) and len(shp.geo) > 0) or not isinstance(shp.geo, list), \
            "Shape objects has empty geometry ([])"

        if isinstance(shp, DrawToolUtilityShape):
            self.utility.append(shp)
        # else:
        #     self.storage.insert(shape)

    @excellon_operation(_('Add features'))
    def on_exc_shape_complete(self, storage):
        self.app.log.debug("on_shape_complete()")

        # Add shape
        if type(storage) is list:
            for item_storage in storage:
                self.add_exc_shape(self.active_tool.geometry, item_storage)
        else:
            self.add_exc_shape(self.active_tool.geometry, storage)

        # Remove any utility shapes
        self.delete_utility_geometry()
        self.tool_shape.clear(update=True)

        # Replot and reset tool.
        self.replot()
        # self.active_tool = type(self.active_tool)(self)

    def draw_selection_area_handler(self, start, end, sel_type):
        """
        This function is called whenever we have a left mouse click release and only we have a left mouse click drag,
        be it from left to right or from right to left. The direction of the drag is decided in the "mouse_move"
        event handler.
        Pressing a modifier key (eg. Ctrl, Shift or Alt) will change the behavior of the selection.

        Depending on which tool belongs the selected shapes, the corresponding rows in the Tools Table are selected or
        deselected.

        :param start:       mouse position when the selection LMB click was done
        :param end:         mouse position when the left mouse button is released
        :param sel_type:    if True it's a left to right selection (enclosure), if False it's a 'touch' selection
        :return:
        """

        start_pos = (start[0], start[1])
        end_pos = (end[0], end[1])
        poly_selection = Polygon([start_pos, (end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])
        modifiers = None

        # delete the selection shape that was just drawn, we no longer need it
        self.app.delete_selection_shape()

        # detect if a modifier key was pressed while the left mouse button was released
        self.modifiers = QtWidgets.QApplication.keyboardModifiers()
        if self.modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
            modifiers = 'Shift'
        elif self.modifiers == QtCore.Qt.KeyboardModifier.ControlModifier:
            modifiers = 'Control'

        if modifiers == self.app.options.get(
                "global_mselect_key",
                self.app.defaults.get(
                    "global_mselect_key",
                    self.app.defaults.factory_defaults.get("global_mselect_key")
                )):
            for storage in self.storage_dict:
                for obj in self.storage_dict[storage].get_objects():
                    if (sel_type is True and poly_selection.contains(obj.geo)) or \
                            (sel_type is False and poly_selection.intersects(obj.geo)):

                        if obj in self.selected:
                            # remove the shape object from the selected shapes storage
                            self.selected.remove(obj)
                        else:
                            # add the shape object to the selected shapes storage
                            self.selected.append(obj)
        else:
            # clear the selection shapes storage
            self.selected.clear()
            # then add to the selection shapes storage the shapes that are included (touched) by the selection rectangle
            for storage in self.storage_dict:
                for obj in self.storage_dict[storage].get_objects():
                    if (sel_type is True and poly_selection.contains(obj.geo)) or \
                            (sel_type is False and poly_selection.intersects(obj.geo)):
                        self.selected.append(obj)

        self.sync_tool_table_selection()
        self.replot()

    def update_utility_geometry(self, data):
        # ### Utility geometry (animated) ###
        geo = self.active_tool.utility_geometry(data=data)
        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            # Remove any previous utility shape
            self.tool_shape.clear(update=False)
            self.draw_utility_geometry(geo=geo)

    # def on_canvas_key_release(self, event):
    #     self.key = None

    def draw_utility_geometry(self, geo):
        # Add the new utility shape

        if isinstance(geo.geo, (MultiLineString, MultiPolygon)):
            util_geo = geo.geo.geoms
        else:
            util_geo = geo.geo

        try:
            # this case is for the Font Parse
            for el in util_geo:
                if isinstance(el, (MultiLineString, MultiPolygon)):
                    for sub_geo in el.geoms:
                        self.tool_shape.add(
                            shape=sub_geo,
                            color=self.get_draw_color(),
                            update=False,
                            layer=0,
                            tolerance=None
                        )
                else:
                    self.tool_shape.add(
                        shape=el,
                        color=self.get_draw_color(),
                        update=False,
                        layer=0,
                        tolerance=None)
        except TypeError:
            self.tool_shape.add(
                shape=util_geo,
                color=self.get_draw_color(),
                update=False,
                layer=0,
                tolerance=None)
        # print(self.tool_shape.data)
        self.tool_shape.redraw()

    def get_draw_color(self):
        orig_color = self.app.options.get(
            "global_draw_color",
            self.app.defaults.get(
                "global_draw_color",
                self.app.defaults.factory_defaults.get("global_draw_color")
            )
        )

        if self.app.options.get(
                'global_theme',
                self.app.defaults.get(
                    'global_theme',
                    self.app.defaults.factory_defaults.get('global_theme')
                )) in ['default', 'light']:
            return orig_color

        # in the "dark" theme we invert the color
        lowered_color = orig_color.lower()
        group1 = "#0123456789abcdef"
        group2 = "#fedcba9876543210"
        # create color dict
        color_dict = {group1[i]: group2[i] for i in range(len(group1))}
        new_color = ''.join([color_dict[j] for j in lowered_color])
        return new_color

    def get_sel_color(self):
        return self.app.options.get(
            'global_sel_draw_color',
            self.app.defaults.get(
                'global_sel_draw_color',
                self.app.defaults.factory_defaults.get('global_sel_draw_color')
            )
        )

    def snap(self, x, y):
        """Acquire Excellon features from a local index; picking remains unsnapped."""
        self.snap_kind = None
        if not self.app.grid_status() or not all(math.isfinite(value) for value in (x, y)):
            if hasattr(self.ui, 'snap_result'):
                self.ui.snap_result.setText('')
            return x, y
        # Only share grid preferences. Never query the inactive Geometry editor's storage.
        options = self.app.geo_editor.editor_options
        radius = float(options.get('global_snap_max', 0.05))
        modes = {kind: control.isChecked() for kind, control in self.ui.snap_modes.items()}
        cached = getattr(self, '_exc_snap_index', None)
        signature = tuple((dia, id(storage), getattr(storage, 'revision', None))
                          for dia, storage in self.storage_dict.items())
        if getattr(self, '_exc_snap_revision', None) != signature:
            cached = None
        if cached is None:
            geometries, kinds = [], []
            for storage in self.storage_dict.values():
                for shape in storage.get_objects():
                    geo = shape.geo
                    if geo is None or geo.is_empty or not geo.is_valid:
                        continue
                    if isinstance(geo, MultiLineString):
                        geometries.append(geo.centroid)
                        kinds.append('center')
                    elif isinstance(geo, Polygon):
                        native = getattr(shape, 'exc_slot', None)
                        geometries.append(geo.centroid)
                        kinds.append('center')
                        if native is not None and getattr(shape, 'exc_slot_geometry', None) is geo:
                            line = LineString(native)
                            geometries.extend([Point(native[0]), Point(native[1]),
                                               line.interpolate(0.5, normalized=True), line])
                            kinds.extend(['endpoint', 'endpoint', 'midpoint', 'nearest'])
            cached = self._exc_snap_index = (STRtree(geometries), geometries, kinds)
            self._exc_snap_revision = signature
        tree, geometries, kinds = cached
        query = Point(x, y)
        points, nearest = [], []
        if math.isfinite(radius) and radius >= 0:
            for index in tree.query(box(x - radius, y - radius, x + radius, y + radius)):
                kind = kinds[index]
                if not modes.get(kind, False):
                    continue
                geometry = geometries[index]
                point = geometry.interpolate(geometry.project(query)) if kind == 'nearest' else geometry
                delta = query.distance(point)
                if delta <= radius:
                    candidate = (delta, {'endpoint': 0, 'midpoint': 1, 'center': 2, 'nearest': 3}[kind],
                                 (point.x, point.y), kind)
                    (nearest if kind == 'nearest' else points).append(candidate)
        candidate = min(points or nearest) if points or nearest else (math.inf, 4, (x, y), None)
        if options.get('grid_snap', False):
            gx = float(options.get('global_gridx', 0))
            gy = gx if self.app.ui.grid_gap_link_cb.isChecked() else float(options.get('global_gridy', 0))
            grid = (round(x / gx) * gx if gx else x, round(y / gy) * gy if gy else y)
            grid_distance = math.dist((x, y), grid)
            if grid_distance < candidate[0]:
                candidate = (grid_distance, 4, grid, 'grid')
        self.snap_kind = candidate[3]
        self.ui.snap_result.setText(_('Snap') + ': ' + self.snap_kind if self.snap_kind else '')
        return candidate[2]

    def replot(self):
        self.plot_all()
        self.refresh_feature_table()

    def undo(self):
        return self.history.replay()

    def redo(self):
        return self.history.replay(redo=True)

    def update_history_ui(self):
        history = self.history
        for action in getattr(self, 'transform_actions', {}).values():
            action.setEnabled(not history.suspended and history.pending is None)
        for button in getattr(self.ui, 'transform_buttons', {}).values():
            button.setEnabled(not history.suspended and history.pending is None)
        idle = not history.suspended and history.pending is None
        self.undo_action.setEnabled(bool(history.undo_stack) and idle)
        self.redo_action.setEnabled(bool(history.redo_stack) and idle)
        labels = [_('Oldest retained state') if history.baseline_evicted else _('Initial state')]
        labels.extend(step['label'] for step in history.undo_stack)
        labels.extend('↪ ' + step['label'] for step in reversed(history.redo_stack))
        with QtCore.QSignalBlocker(self.ui.history_list):
            self.ui.history_list.clear()
            self.ui.history_list.addItems(labels)
            self.ui.history_list.setCurrentIndex(len(history.undo_stack))
        self.ui.history_list.setEnabled(not history.suspended and history.pending is None)
        self.refresh_feature_table()

    def on_history_row(self, row):
        history = self.history
        if not isinstance(row, int) or row < 0 or row > len(history.undo_stack) + len(history.redo_stack):
            return False
        if history.pending is not None or history.suspended or history.depth or history.replaying:
            return False
        while len(history.undo_stack) != row:
            if not history.replay(redo=len(history.undo_stack) < row):
                return False
        return True

    def filter_features(self, selected_only=False, **filters):
        try:
            return filter_features(self.storage_dict, set(self.selected) if selected_only else None, **filters)
        except (ValueError, TypeError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(error))
            return 'fail'

    def select_filtered_features(self, **filters):
        if self.history.pending is not None or not self.history.ready():
            return 'fail'
        records = self.filter_features(**filters)
        if records == 'fail':
            return 'fail'
        self.selected = [record['shape'] for record in records]
        self.replot()
        return len(records)

    @excellon_operation(_('Delete filtered features'))
    def delete_filtered_features(self, **filters):
        records = self.filter_features(**filters)
        if records == 'fail':
            return 'fail'
        for record in records:
            self.delete_shape(record['shape'])
        self.build_ui()
        self.replot()
        return len(records)

    def feature_filter_values(self):
        filters = {'kind': self.ui.feature_kind.currentData(),
                   'selected_only': self.ui.feature_selected.isChecked()}
        for name, size in (('diameter', 2), ('length', 2), ('bounds', 4)):
            text = self.ui.feature_ranges[name].text().strip()
            if text:
                values = tuple(float(value.strip()) for value in text.split(','))
                if len(values) != size:
                    raise ValueError(_('Enter comma-separated range values.'))
                filters[name] = values
        return filters

    def on_feature_filter_action(self, delete=False):
        try:
            filters = self.feature_filter_values()
        except (ValueError, TypeError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(error))
            return 'fail'
        return self.delete_filtered_features(**filters) if delete else self.select_filtered_features(**filters)

    @excellon_operation(_('Edit slot endpoints'))
    def edit_slot_endpoints(self, shape, endpoints, expected=None):
        if self.history.suspended:
            return 'fail'
        if expected is not None and expected != (self.history.session, self.history.revision):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('The slot changed since the endpoints were loaded. '
                                                          'Load them again.'))
            return 'fail'
        try:
            endpoints = tuple(tuple(float(value) for value in point) for point in endpoints)
            if len(endpoints) != 2 or any(len(point) != 2 for point in endpoints) or \
                    not all(math.isfinite(value) for point in endpoints for value in point) or \
                    endpoints[0] == endpoints[1]:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Enter two distinct finite slot endpoints.'))
            return 'fail'
        native = getattr(shape, 'exc_slot', None)
        if not isinstance(shape.geo, Polygon) or native is None or \
                getattr(shape, 'exc_slot_geometry', None) is not shape.geo or shape not in self.selected:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('Select one native slot.'))
            return 'fail'
        for diameter, storage in self.storage_dict.items():
            objects = list(storage.get_objects())
            if shape not in objects:
                continue
            if not math.isfinite(diameter) or diameter <= 0:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('Please enter a finite positive tool diameter.'))
                return 'fail'
            if endpoints == native:
                return
            geometry = LineString(endpoints).buffer(diameter / 2, circle_quad_segs_of(self, diameter / 2))
            if geometry.is_empty or not geometry.is_valid or not all(math.isfinite(v) for v in geometry.bounds):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('Enter two distinct finite slot endpoints.'))
                return 'fail'
            replacement = DrawToolShape(geometry)
            replacement.exc_slot = endpoints
            replacement.exc_slot_geometry = geometry
            if hasattr(shape, 'exc_source_diameter'):
                replacement.exc_source_diameter = shape.exc_source_diameter
            # replace() appends its insertions. Replace the suffix once to retain feature order.
            index = objects.index(shape)
            storage.replace(removed=objects[index:], inserted=[replacement] + objects[index + 1:])
            self.selected = [replacement if selected is shape else selected for selected in self.selected]
            self._exc_snap_index = None
            self.replot()
            return replacement
        self.app.inform.emit('[WARNING_NOTCL] %s' % _('Select one native slot.'))
        return 'fail'

    def load_slot_endpoints(self):
        if len(self.selected) != 1:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('Select one native slot.'))
            return 'fail'
        shape = self.selected[0]
        native = getattr(shape, 'exc_slot', None)
        if native is None or getattr(shape, 'exc_slot_geometry', None) is not shape.geo:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('Select one native slot.'))
            return 'fail'
        self._slot_numeric_target = (shape, shape.geo, self.history.session, self.history.revision)
        for entry, value in zip(self.ui.slot_endpoint_entries, (v for point in native for v in point)):
            entry.set_value(value)

    def on_slot_endpoints_apply(self):
        target = getattr(self, '_slot_numeric_target', None)
        if target is None:
            return self.load_slot_endpoints()
        shape, geo, session, revision = target
        if shape.geo is not geo:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _('The slot changed since the endpoints were loaded. '
                                                          'Load them again.'))
            return 'fail'
        values = [entry.get_value() for entry in self.ui.slot_endpoint_entries]
        result = self.edit_slot_endpoints(shape, (values[:2], values[2:]), expected=(session, revision))
        if result != 'fail':
            self.load_slot_endpoints()
        return result

    def refresh_feature_table(self):
        if not hasattr(self.ui, 'feature_table'):
            return
        try:
            filters = self.feature_filter_values()
        except (ValueError, TypeError):
            return
        table = self.ui.feature_table
        signature = (id(table), tuple((dia, id(storage), getattr(storage, 'revision', None))
                                     for dia, storage in self.storage_dict.items()),
                     tuple(filters.items()), tuple(self.selected) if filters.get('selected_only') else None)
        cached = getattr(self, '_exc_feature_rows', None)
        reuse = cached is not None and cached[0] == signature and table.rowCount() == len(cached[1])
        records = cached[1] if reuse else self.filter_features(**filters)
        if records == 'fail':
            return
        self._exc_feature_rows = (signature, records)
        selected = set(self.selected)
        blocker = QtCore.QSignalBlocker(table)
        if not reuse:
            table.setRowCount(len(records))
        for row, record in enumerate(records):
            if not reuse:
                values = (record['index'] + 1, FEATURE_TYPE_LABELS.get(record['type'], record['type']),
                          record['diameter'], record['center'],
                          record.get('length', ''), record['bounds'])
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, record['shape'])
                    table.setItem(row, column, item)
            for column in range(table.columnCount()):
                table.item(row, column).setSelected(record['shape'] in selected)
        del blocker
        # the panel stays on the tool row (sync_tool_rows); the feature table scrolls inside itself
        show_selected_rows(table, panel=False)
        # Tool rows summarize the feature selection; never invoke row-click select-all handlers.
        self.sync_tool_rows()

    def on_feature_selection(self):
        if self.history.pending is not None:
            self.refresh_feature_table()
            return
        live = {shape for storage in self.storage_dict.values() for shape in storage.get_objects()}
        table = self.ui.feature_table
        self.selected = [table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                         for index in table.selectionModel().selectedRows()
                         if table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) in live]
        self.replot()

    def diagnose_features(self, selected_only=False, tolerance=0.001):
        try:
            tolerance = float(tolerance)
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError
        except (ValueError, TypeError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Tolerance must be a finite non-negative number.'))
            return 'fail'
        selected = set(self.selected) if selected_only else None
        return diagnose_features(self.storage_dict, selected, tolerance)

    def on_diagnostics(self):
        report = self.diagnose_features(self.ui.diagnostics_selected.isChecked(),
                                        self.ui.diagnostics_tolerance.get_value())
        if report == 'fail':
            return
        lines = [_('Read-only snapshot. Drill crosses are not open-contour errors.'),
                 '%s: %d; %s: %d' % (_('Drills'), report['drills'], _('Slots'), report['slots'])]
        names = {'empty': _('Empty'), 'invalid': _('Invalid'), 'degenerate': _('Degenerate'),
                 'duplicate': _('Duplicate'), 'coincident': _('Coincident'),
                 'invalid_tools': _('Invalid tool diameter')}
        for key, label in names.items():
            lines.append('%s: %d' % (label, len(report[key])))
        for record in report['features']:
            lines.append('%s %s / %s %d: %s; %s' %
                         (_('Diameter'), record['diameter'], _('Feature'), record['index'] + 1,
                          _('Drill') if record['type'] == 'drill' else _('Slot'),
                          record.get('center', _('Empty or invalid'))))
            if 'endpoints' in record:
                lines.append('  %s: %s; %s: %.*f; %s: %.*f°%s' %
                             (_('Endpoints'), record['endpoints'], _('Centerline length'), self.decimals,
                              record['length'], _('Orientation'), self.decimals, record['orientation'],
                              ' (' + _('estimated from capsule') + ')' if record['inferred'] else ''))
        for key in ('empty', 'invalid', 'degenerate'):
            for record in report[key]:
                lines.append('%s: %s / %d' % (names[key], record['diameter'], record['index'] + 1))
        for key in ('duplicate', 'coincident'):
            for first, second in report[key]:
                lines.append('%s: %s / %d ↔ %s / %d' %
                             (names[key], first['diameter'], first['index'] + 1, second['diameter'],
                              second['index'] + 1))
        self.ui.diagnostics_result.setPlainText('\n'.join(lines))
        return report

    def plot_all(self):
        """
        Plots all shapes in the editor.

        :return:    None
        :rtype:     None
        """

        # Cache belongs to this collection, not to the wrappers or the pick index. Collection
        # clears and style/tolerance changes invalidate it, including clears made by other tools.
        state = (id(self.shapes), getattr(self.shapes, 'last_key', None), len(self.shapes.data),
                 tuple((id(shape), id(shape.geo)) for shape in self.utility), self.tolerance)
        rendered = getattr(self, '_exc_rendered', {})
        changed = False
        owned = getattr(self, '_exc_render_owned', {})
        if getattr(self, '_exc_render_state', None) != state or \
                any(self.shapes.data.get(key) is not data for key, data in owned.items()):
            self.shapes.clear(update=False)
            rendered = {}
            changed = True
            for shape in self.utility:
                self.plot_shape(geometry=shape.geo, linewidth=1)
        selected = set(self.selected)
        draw_color = self.get_draw_color()[:-2] + 'FF'
        sel_color = self.get_sel_color()[:-2] + 'FF'
        seen, recolor = set(), {}
        for storage in self.storage_dict.values():
            for shape in storage.get_objects():
                seen.add(id(shape))
                color = sel_color if shape in selected else draw_color
                entry = rendered.get(id(shape))
                if entry is None or entry[0] is not shape or entry[1] is not shape.geo:
                    if entry is not None:
                        for key in entry[2]:
                            self.shapes.remove(key)
                    keys = self.plot_shape(geometry=shape.geo, color=color) if shape.geo is not None else []
                    rendered[id(shape)] = [shape, shape.geo, keys, color]
                    changed = True
                elif entry[3] != color:
                    recolor.setdefault(color, set()).update(entry[2])
                    entry[3] = color
        for shape_id in list(rendered):
            if shape_id not in seen:
                for key in rendered.pop(shape_id)[2]:
                    self.shapes.remove(key)
                changed = True
        for color, keys in recolor.items():
            if keys:
                self.shapes.update_color(new_line_color=color, indexes=keys)
                changed = True
        if changed:
            self.shapes.redraw()
        self._exc_rendered = rendered
        self._exc_render_owned = dict(self.shapes.data)
        self._exc_render_state = (id(self.shapes), getattr(self.shapes, 'last_key', None), len(self.shapes.data),
                                  tuple((id(shape), id(shape.geo)) for shape in self.utility), self.tolerance)

    def plot_shape(self, geometry=None, color='0x000000FF', linewidth=1):
        """
        Plots a geometric object or list of objects without rendering. Plotted objects
        are returned as a list. This allows for efficient/animated rendering.

        :param geometry:    Geometry to be plotted (Any Shapely.geom kind or list of such)
        :param color:       Shape color
        :param linewidth:   Width of lines in # of pixels.
        :return:            List of plotted elements.
        """
        plot_elements = []

        if geometry is None:
            geometry = self.active_tool.geometry

        if isinstance(geometry, (MultiLineString, MultiPolygon)):
            use_geometry = geometry.geoms
        else:
            use_geometry = geometry

        try:
            for geo in use_geometry:
                plot_elements += self.plot_shape(geometry=geo, color=color, linewidth=linewidth)

        # ## Non-iterable
        except TypeError:
            # ## DrawToolShape
            if isinstance(use_geometry, DrawToolShape):
                plot_elements += self.plot_shape(geometry=use_geometry.geo, color=color, linewidth=linewidth)

            # ## Polygon: Descend into exterior and each interior.
            if isinstance(use_geometry, Polygon):
                plot_elements += self.plot_shape(geometry=use_geometry.exterior, color=color, linewidth=linewidth)
                plot_elements += self.plot_shape(geometry=use_geometry.interiors, color=color, linewidth=linewidth)

            if isinstance(use_geometry, (LineString, LinearRing)):
                plot_elements.append(self.shapes.add(shape=use_geometry, color=color, layer=0,
                                                     tolerance=self.tolerance))

            if type(geometry) == Point:
                pass

        return plot_elements

    def on_shape_complete(self):
        # Add shape
        self.add_shape(self.active_tool.geometry)

        # Remove any utility shapes
        self.delete_utility_geometry()
        self.tool_shape.clear(update=True)

        # Replot and reset tool.
        self.replot()
        # self.active_tool = type(self.active_tool)(self)

    def get_selected(self):
        """
        Returns list of shapes that are selected in the editor.

        :return: List of shapes.
        """
        return self.selected

    @excellon_operation(_('Delete features'))
    def delete_selected(self):
        temp_ref = [s for s in self.selected]
        for shape_sel in temp_ref:
            self.delete_shape(shape_sel)

        self.selected.clear()
        self.build_ui()
        self.app.inform.emit('[success] %s' % _("Done."))

    @excellon_operation(_('Delete feature'))
    def delete_shape(self, del_shape):
        self.is_modified = True

        if del_shape in self.utility:
            self.utility.remove(del_shape)
            return

        for storage in self.storage_dict:
            # try:
            #     self.storage_dict[storage].remove(shape)
            # except:
            #     pass
            if del_shape in self.storage_dict[storage].get_objects():
                if isinstance(del_shape.geo, MultiLineString):
                    self.storage_dict[storage].remove(del_shape)
                    # a hack to make the plugin_table display less drills per diameter
                    # self.points_edit it's only useful first time when we load the data into the storage
                    # but is still used as referecen when building plugin_table in self.build_ui()
                    # the number of drills displayed in column 2 is just a len(self.points_edit) therefore
                    # deleting self.points_edit elements (doesn't matter who but just the number)
                    # solved the display issue.
                    del self.points_edit[storage][0]
                else:
                    self.storage_dict[storage].remove(del_shape)
                    del self.slot_points_edit[storage][0]

        if del_shape in self.selected:
            self.selected.remove(del_shape)

    def delete_utility_geometry(self):
        for_deletion = [util_shape for util_shape in self.utility]
        for util_shape in for_deletion:
            self.delete_shape(util_shape)

        self.tool_shape.clear(update=True)
        self.tool_shape.redraw()

    def on_delete_btn(self):
        self.delete_selected()
        self.replot()

    def select_tool(self, pluginName):
        """
        Selects a drawing tool. Impacts the object and appGUI.

        :param pluginName:    Name of the tool.
        :return:            None
        """
        self.tools_exc[pluginName]["button"].setChecked(True)
        self.on_tool_select(pluginName)

    def set_selected(self, sel_shape):

        # Remove and add to the end.
        if sel_shape in self.selected:
            self.selected.remove(sel_shape)

        self.selected.append(sel_shape)

    def set_unselected(self, unsel_shape):
        if unsel_shape in self.selected:
            self.selected.remove(unsel_shape)

    def exc_add_drill(self):
        self.select_tool('drill_add')
        return

    def exc_add_drill_array(self):
        self.select_tool('drill_array')
        return

    def exc_add_slot(self):
        self.select_tool('slot_add')
        return

    def exc_add_slot_array(self):
        self.select_tool('slot_array')
        return

    def exc_resize_drills(self):
        self.select_tool('drill_resize')
        return

    def exc_copy_drills(self):
        self.select_tool('drill_copy')
        return

    def exc_move_drills(self):
        self.select_tool('drill_move')
        return

    @excellon_operation(_('Convert slots to drills'))
    def on_slots_conversion(self):
        # selected rows
        selected_rows = set()
        for it in self.ui.tools_table_exc.selectedItems():
            selected_rows.add(it.row())

        # convert a Polygon (slot) to a MultiLineString (drill)
        def convert_slot2drill(geo_elem, tool_dia):
            point = geo_elem.centroid
            start_hor_line = ((point.x - (tool_dia / 2)), point.y)
            stop_hor_line = ((point.x + (tool_dia / 2)), point.y)
            start_vert_line = (point.x, (point.y - (tool_dia / 2)))
            stop_vert_line = (point.x, (point.y + (tool_dia / 2)))
            return MultiLineString([(start_hor_line, stop_hor_line), (start_vert_line, stop_vert_line)])

        # temporary new storage: a dist with keys the tool diameter and values Rtree storage
        new_storage_dict = {}
        replacements = {}

        for row in selected_rows:
            table_tooldia = table_diameter(self.ui.tools_table_exc, row)
            for dict_dia, geo_dict in self.storage_dict.items():
                if dict_dia == table_tooldia:
                    storage_elem = AppGeoEditor.make_storage()
                    new_shapes = []
                    for shp in geo_dict.get_objects():
                        if isinstance(shp.geo, MultiLineString):
                            # it's a drill just add it as it is to storage
                            new_shapes.append(shp)
                        if isinstance(shp.geo, Polygon):
                            # it's a slot, convert drill to slot and then add it to storage
                            new_shape = convert_slot2drill(shp.geo, table_tooldia)
                            replacement = DrawToolShape(new_shape)
                            new_shapes.append(replacement)
                            replacements[shp] = replacement
                    # 1 batch for each tool
                    self.add_exc_shape(new_shapes, storage_elem)

                    new_storage_dict[table_tooldia] = storage_elem
                    self.points_edit[table_tooldia] = [shape.geo.centroid for shape in new_shapes]
                    self.slot_points_edit[table_tooldia] = []

        self.storage_dict.update(new_storage_dict)
        self.selected[:] = [replacements.get(shape, shape) for shape in self.selected]
        self.build_ui()
        self.replot()


class AppExcEditorUI:
    def __init__(self, app):
        self.app = app

        # Number of decimals used by tools in this class
        self.decimals = self.app.decimals

        # ## Current application units in Upper Case
        self.units = self.app.app_units.upper()

        self.exc_edit_widget = QtWidgets.QWidget()
        # ## Box for custom widgets
        # This gets populated in offspring implementations.
        layout = QtWidgets.QVBoxLayout()
        self.exc_edit_widget.setLayout(layout)

        # add a frame and inside add a vertical box layout. Inside this vbox layout I add all the Drills widgets
        # this way I can hide/show the frame
        self.drills_frame = QtWidgets.QFrame()
        self.drills_frame.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.drills_frame)

        # #############################################################################################################
        # ######################## MAIN Grid ##########################################################################
        # #############################################################################################################
        self.ui_vertical_lay = QtWidgets.QVBoxLayout()
        self.ui_vertical_lay.setContentsMargins(0, 0, 0, 0)
        self.drills_frame.setLayout(self.ui_vertical_lay)

        # Page Title box (spacing between children)
        self.title_box = QtWidgets.QHBoxLayout()
        self.ui_vertical_lay.addLayout(self.title_box)

        # Page Title
        pixmap = QtGui.QPixmap(self.app.resource_location + '/app32.png')
        self.icon = FCLabel()
        self.icon.setPixmap(pixmap)

        self.title_label = FCLabel("<font size=5><b>%s</b></font>" % _('Excellon Editor'))
        self.title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.title_box.addWidget(self.icon, stretch=0)
        self.title_box.addWidget(self.title_label, stretch=1)

        # App Level label
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(
            _(
                "Beginner Mode - many parameters are hidden.\n"
                "Advanced Mode - full control.\n"
                "Permanent change is done in 'Preferences' menu."
            )
        )
        # self.level.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        # Object name box
        self.name_box = QtWidgets.QHBoxLayout()
        self.ui_vertical_lay.addLayout(self.name_box)

        # Object Name
        name_label = FCLabel(_("Name:"))
        self.name_entry = FCEntry()

        self.name_box.addWidget(name_label)
        self.name_box.addWidget(self.name_entry)

        # #############################################################################################################
        # ########################################## History ##########################################################
        # #############################################################################################################
        self.history_label = FCLabel(_('History'), color='blue', bold=True)
        self.ui_vertical_lay.addWidget(self.history_label)

        self.history_frame = FCFrame()
        self.ui_vertical_lay.addWidget(self.history_frame)

        history_grid = GLay(v_spacing=5, h_spacing=3)
        self.history_frame.setLayout(history_grid)

        self.history_list = QtWidgets.QComboBox()
        self.history_list.setToolTip(_('Editor-local operation history. Saving uses project history.'))
        history_grid.addWidget(self.history_list, 0, 0, 1, 2)

        # Tools Drills Table Title
        self.tools_table_label = FCLabel('%s' % _('Tools Table'), bold=True)
        self.tools_table_label.setToolTip(
            _("Tools in this Excellon object\n"
              "when are used for drilling.")
        )
        self.ui_vertical_lay.addWidget(self.tools_table_label)

        # #############################################################################################################
        # ########################################## Drills TABLE #####################################################
        # #############################################################################################################
        self.tools_table_exc = FCTable()
        self.tools_table_exc.setColumnCount(4)
        self.tools_table_exc.setHorizontalHeaderLabels(['#', _('Diameter'), 'D', 'S'])
        self.tools_table_exc.setSortingEnabled(False)
        self.tools_table_exc.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.ui_vertical_lay.addWidget(self.tools_table_exc)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.ui_vertical_lay.addWidget(separator_line)

        self.convert_slots_btn = FCButton('%s' % _("Convert Slots"))
        self.convert_slots_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/convert32.png'))

        self.convert_slots_btn.setToolTip(
            _("Convert the slots in the selected tools to drills.")
        )
        self.ui_vertical_lay.addWidget(self.convert_slots_btn)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.ui_vertical_lay.addWidget(separator_line)

        # Add a new Tool
        self.addtool_label = FCLabel('%s' % _('Add/Delete Tool'), bold=True)
        self.addtool_label.setToolTip(
            _("Add/Delete a tool to the tool list\n"
              "for this Excellon object.")
        )
        self.ui_vertical_lay.addWidget(self.addtool_label)

        # #############################################################################################################
        # ######################## ADD New Tool Grid ##################################################################
        # #############################################################################################################
        grid1 = GLay(v_spacing=5, h_spacing=3)
        self.ui_vertical_lay.addLayout(grid1)

        # Tool Diameter Label
        addtool_entry_lbl = FCLabel('%s:' % _('Tool Dia'))
        addtool_entry_lbl.setToolTip(
            _("Diameter for the new tool")
        )

        # Tool Diameter Entry
        self.addtool_entry = FCDoubleSpinner(policy=False)
        self.addtool_entry.set_precision(self.decimals)
        self.addtool_entry.set_range(0.0000, 10000.0000)

        grid1.addWidget(addtool_entry_lbl, 0, 0)
        grid1.addWidget(self.addtool_entry, 0, 1)
        grid1.addWidget(FCLabel(self.units), 0, 2)

        # Tool Diameter Button
        self.addtool_btn = FCButton(_('Add'))
        self.addtool_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/plus16.png'))
        self.addtool_btn.setToolTip(
            _("Add a new tool to the tool list\n"
              "with the diameter specified above.")
        )

        # Delete Tool
        self.deltool_btn = FCButton(_('Delete Tool'))
        self.deltool_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/trash32.png'))
        self.deltool_btn.setToolTip(
            _("Delete a tool in the tool list\n"
              "by selecting a row in the tool table.")
        )
        addtool_buttons = QtWidgets.QHBoxLayout()
        addtool_buttons.addWidget(self.addtool_btn)
        addtool_buttons.addWidget(self.deltool_btn)
        grid1.addLayout(addtool_buttons, 1, 0, 1, 3)

        # #############################################################################################################
        # ################################ Feature diagnostics / inspection ###########################################
        # #############################################################################################################
        self.diagnostics_label = FCLabel(_('Feature diagnostics / inspection'), color='teal', bold=True)
        self.ui_vertical_lay.addWidget(self.diagnostics_label)

        self.diagnostics_frame = FCFrame()
        self.ui_vertical_lay.addWidget(self.diagnostics_frame)

        diagnostics_grid = GLay(v_spacing=5, h_spacing=3)
        self.diagnostics_frame.setLayout(diagnostics_grid)

        self.diagnostics_selected = QtWidgets.QCheckBox(_('Selected features only'))
        diagnostics_grid.addWidget(self.diagnostics_selected, 0, 0, 1, 3)

        diagnostics_tol_lbl = FCLabel('%s:' % _('Tolerance'))
        self.diagnostics_tolerance = FCDoubleSpinner(policy=False)
        self.diagnostics_tolerance.set_precision(max(self.decimals, 6))
        self.diagnostics_tolerance.set_range(0.0, 10000.0)
        self.diagnostics_tolerance.set_value(0.001 if self.units == 'MM' else 0.00004)
        diagnostics_grid.addWidget(diagnostics_tol_lbl, 1, 0)
        diagnostics_grid.addWidget(self.diagnostics_tolerance, 1, 1)
        diagnostics_grid.addWidget(FCLabel(self.units), 1, 2)

        self.diagnostics_button = FCButton(_('Inspect'))
        diagnostics_grid.addWidget(self.diagnostics_button, 2, 0, 1, 3)

        self.diagnostics_result = QtWidgets.QPlainTextEdit()
        self.diagnostics_result.setReadOnly(True)
        self.diagnostics_result.setMaximumHeight(180)
        diagnostics_grid.addWidget(self.diagnostics_result, 3, 0, 1, 3)

        # #############################################################################################################
        # ################################ Feature selection / filters ################################################
        # #############################################################################################################
        self.feature_label = FCLabel(_('Feature selection / filters'), color='tomato', bold=True)
        self.ui_vertical_lay.addWidget(self.feature_label)

        self.feature_frame = FCFrame()
        self.ui_vertical_lay.addWidget(self.feature_frame)

        feature_grid = GLay(v_spacing=5, h_spacing=3)
        self.feature_frame.setLayout(feature_grid)

        feature_kind_lbl = FCLabel('%s:' % _('Type'))
        self.feature_kind = QtWidgets.QComboBox()
        for label, value in ((_('All features'), 'all'), (_('Drills'), 'drill'), (_('Slots'), 'slot')):
            self.feature_kind.addItem(label, value)
        feature_grid.addWidget(feature_kind_lbl, 0, 0)
        feature_grid.addWidget(self.feature_kind, 0, 1)

        self.feature_selected = QtWidgets.QCheckBox(_('Search selected features only'))
        feature_grid.addWidget(self.feature_selected, 1, 0, 1, 3)

        self.feature_ranges = {}
        row = 2
        for name, label, hint in (('diameter', _('Dia range'), 'min, max'),
                                  ('length', _('Slot range'), 'min, max'),
                                  ('bounds', _('Within'), 'xmin, ymin, xmax, ymax')):
            entry = QtWidgets.QLineEdit()
            entry.setPlaceholderText(hint)
            entry.setToolTip(_('Optional finite values, comma-separated. Blank disables this filter.'))
            self.feature_ranges[name] = entry
            feature_grid.addWidget(FCLabel('%s:' % label), row, 0)
            feature_grid.addWidget(entry, row, 1)
            feature_grid.addWidget(FCLabel(self.units), row, 2)
            row += 1

        feature_buttons = QtWidgets.QHBoxLayout()
        self.feature_apply = QtWidgets.QToolButton()
        self.feature_apply.setIcon(QtGui.QIcon(self.app.resource_location + '/recycle16.png'))
        self.feature_apply.setToolTip(_('Refresh'))
        self.feature_select = FCButton(_('Select matches'))
        self.feature_delete = FCButton(_('Delete matches'))
        for button in (self.feature_apply, self.feature_select, self.feature_delete):
            feature_buttons.addWidget(button)
        feature_grid.addLayout(feature_buttons, row, 0, 1, 3)

        self.feature_table = QtWidgets.QTableWidget(0, 6)
        self.feature_table.setHorizontalHeaderLabels([_('Feature'), _('Type'), _('Diameter'), _('Center'),
                                                      _('Slot length'), _('Bounds')])
        self.feature_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.feature_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.feature_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.feature_table.setMaximumHeight(200)
        feature_grid.addWidget(self.feature_table, row + 1, 0, 1, 3)

        # #############################################################################################################
        # ################################ Transform selected features ################################################
        # #############################################################################################################
        self.transform_label = FCLabel(_('Transform selected features'), color='green', bold=True)
        self.ui_vertical_lay.addWidget(self.transform_label)

        self.transform_frame = FCFrame()
        self.ui_vertical_lay.addWidget(self.transform_frame)

        transform_grid = GLay(v_spacing=5, h_spacing=3)
        self.transform_frame.setLayout(transform_grid)

        transform_ref_lbl = FCLabel('%s:' % _('Reference'))
        self.transform_reference = QtWidgets.QComboBox()
        for label, reference in ((_('Origin'), 'origin'), (_('Selection center'), 'selection'),
                                  (_('Minimum / lower left'), 'lower_left'),
                                  (_('Lower right'), 'lower_right'), (_('Upper right'), 'upper_right'),
                                  (_('Upper left'), 'upper_left'),
                                  (_('Point'), 'point')):
            self.transform_reference.addItem(label, reference)
        transform_grid.addWidget(transform_ref_lbl, 0, 0)
        transform_grid.addWidget(self.transform_reference, 0, 1)

        self.transform_point = []
        for row, label in enumerate((_('Reference X'), _('Reference Y')), start=1):
            entry = FCDoubleSpinner(policy=False)
            entry.set_precision(max(self.decimals, 6))
            entry.set_range(-1e9, 1e9)
            self.transform_point.append(entry)
            transform_grid.addWidget(FCLabel('%s:' % label), row, 0)
            transform_grid.addWidget(entry, row, 1)
            transform_grid.addWidget(FCLabel(self.units), row, 2)
        self.transform_angle = FCDoubleSpinner(policy=False)
        self.transform_angle.set_precision(max(self.decimals, 6))
        self.transform_angle.set_range(-360000, 360000)
        self.transform_angle.set_value(90)
        self.transform_scale = FCDoubleSpinner(policy=False)
        self.transform_scale.set_precision(max(self.decimals, 6))
        self.transform_scale.set_range(0.000001, 1e6)
        self.transform_scale.set_value(1)
        self.transform_scale.setToolTip(_('Scales positions and circular tool diameters.'))
        # the offset (in editor units) is a translation of the selected features
        self.transform_offset = []
        for label in (_('Offset X'), _('Offset Y')):
            entry = FCDoubleSpinner(policy=False)
            entry.set_precision(max(self.decimals, 6))
            entry.set_range(-1e9, 1e9)
            self.transform_offset.append(entry)
        self.transform_buttons = {}
        for kind, label in (('rotate', _('Rotate')), ('mirror_x', _('Mirror X')),
                            ('mirror_y', _('Mirror Y')), ('scale', _('Uniform scale')),
                            ('offset', _('Offset'))):
            button = FCButton(label)
            button.setToolTip(_('Transform selected features only. Mirror X reflects across the horizontal X axis '
                                'through the reference (reverses Y); Mirror Y reflects across the vertical Y axis '
                                'through the reference (reverses X). Scaling changes circular tool diameters too.'))
            self.transform_buttons[kind] = button

        # each action button follows its own inputs
        transform_grid.addWidget(FCLabel('%s:' % _('Angle (CCW)')), 3, 0)
        transform_grid.addWidget(self.transform_angle, 3, 1)
        transform_grid.addWidget(FCLabel('°'), 3, 2)
        transform_grid.addWidget(self.transform_buttons['rotate'], 4, 0, 1, 3)

        transform_grid.addWidget(FCLabel('%s:' % _('Uniform scale')), 5, 0)
        transform_grid.addWidget(self.transform_scale, 5, 1)
        transform_grid.addWidget(self.transform_buttons['scale'], 6, 0, 1, 3)

        for row, label, entry in ((7, _('Offset X'), self.transform_offset[0]),
                                  (8, _('Offset Y'), self.transform_offset[1])):
            transform_grid.addWidget(FCLabel('%s:' % label), row, 0)
            transform_grid.addWidget(entry, row, 1)
            transform_grid.addWidget(FCLabel(self.units), row, 2)
        transform_grid.addWidget(self.transform_buttons['offset'], 9, 0, 1, 3)

        mirror_buttons = QtWidgets.QHBoxLayout()
        mirror_buttons.addWidget(self.transform_buttons['mirror_x'])
        mirror_buttons.addWidget(self.transform_buttons['mirror_y'])
        transform_grid.addLayout(mirror_buttons, 10, 0, 1, 3)

        # #############################################################################################################
        # ################################ Native slot endpoints ######################################################
        # #############################################################################################################
        self.slot_endpoint_label = FCLabel(_('Native slot endpoints'), color='indigo', bold=True)
        self.ui_vertical_lay.addWidget(self.slot_endpoint_label)

        self.slot_endpoint_frame = FCFrame()
        self.ui_vertical_lay.addWidget(self.slot_endpoint_frame)

        endpoint_grid = GLay(v_spacing=5, h_spacing=3)
        self.slot_endpoint_frame.setLayout(endpoint_grid)

        self.slot_endpoint_entries = []
        for row, label in enumerate((_('Start X'), _('Start Y'), _('Stop X'), _('Stop Y'))):
            endpoint_grid.addWidget(FCLabel('%s:' % label), row, 0)
            entry = FCDoubleSpinner(policy=False)
            entry.set_precision(max(self.decimals, 6))
            entry.set_range(-1e9, 1e9)
            self.slot_endpoint_entries.append(entry)
            endpoint_grid.addWidget(entry, row, 1)
            endpoint_grid.addWidget(FCLabel(self.units), row, 2)
        endpoint_buttons = QtWidgets.QHBoxLayout()
        self.slot_endpoint_load = FCButton(_('Load slot'))
        self.slot_endpoint_load.setToolTip(
            _('Load a selected slot centerline endpoints.'))
        self.slot_endpoint_apply = FCButton(_('Apply endpoints'))
        self.slot_endpoint_apply.setToolTip(
            _('Apply loaded slot centerline endpoints.'))
        self.slot_endpoint_drag = FCButton(_('Drag endpoint'))
        self.slot_endpoint_drag.setIcon(QtGui.QIcon(self.app.resource_location + '/move32.png'))
        self.slot_endpoint_drag.setToolTip(
            _('Drag a selected slot centerline endpoint. Width and opposite endpoint stay fixed.'))
        for button in (self.slot_endpoint_load, self.slot_endpoint_apply, self.slot_endpoint_drag):
            endpoint_buttons.addWidget(button)
        endpoint_grid.addLayout(endpoint_buttons, 4, 0, 1, 3)
        endpoint_note = FCLabel(_('Overall slot length = endpoint distance + tool dia.'))
        endpoint_note.setWordWrap(True)
        endpoint_grid.addWidget(endpoint_note, 5, 0, 1, 3)

        # #############################################################################################################
        # ################################ Excellon snapping ##########################################################
        # #############################################################################################################
        self.snap_label = FCLabel(_('Excellon snapping'), color='brown', bold=True)
        self.ui_vertical_lay.addWidget(self.snap_label)

        self.snap_frame = FCFrame()
        self.ui_vertical_lay.addWidget(self.snap_frame)

        snap_grid = GLay(v_spacing=5, h_spacing=3)
        self.snap_frame.setLayout(snap_grid)

        self.snap_modes = {}
        for row, (kind, label) in enumerate((('center', _('Centers')), ('endpoint', _('Native slot endpoints')),
                                              ('midpoint', _('Native slot midpoints')),
                                              ('nearest', _('Nearest slot centerline')))):
            control = QtWidgets.QCheckBox(label)
            control.setChecked(kind in ('center', 'endpoint'))
            self.snap_modes[kind] = control
            snap_grid.addWidget(control, row, 0, 1, 3)
        self.snap_result = FCLabel('')
        self.snap_result.setToolTip(_('Feature snap uses the configured maximum snap distance in project units.'))
        snap_grid.addWidget(self.snap_result, 4, 0, 1, 3)

        # align the label column across the sections
        GLay.set_common_column_size([grid1, diagnostics_grid, feature_grid, transform_grid, endpoint_grid,
                                     snap_grid], 0)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # grid1.addWidget(separator_line, 4, 0, 1, 2)

        self.ui_vertical_lay.addStretch()
        layout.addStretch(1)

        # Editor
        self.exit_editor_button = FCButton(_('Exit Editor'), bold=True)
        self.exit_editor_button.setIcon(QtGui.QIcon(self.app.resource_location + '/power16.png'))
        self.exit_editor_button.setToolTip(
            _("Exit from Editor.")
        )
        layout.addWidget(self.exit_editor_button)

        # #############################################################################################################
        # ###################### INIT Excellon Editor UI ##############################################################
        # #############################################################################################################
        pass

        # ############################ FINSIHED GUI ###################################
        # #############################################################################

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)


def get_shapely_list_bounds(geometry_list):
    xmin = np.inf
    ymin = np.inf
    xmax = -np.inf
    ymax = -np.inf

    for gs in geometry_list:
        try:
            gxmin, gymin, gxmax, gymax = gs.bounds
            xmin = min([xmin, gxmin])
            ymin = min([ymin, gymin])
            xmax = max([xmax, gxmax])
            ymax = max([ymax, gymax])
        except Exception as e:
            log.error("Tried to get bounds of empty geometry. --> %s" % str(e))

    return [xmin, ymin, xmax, ymax]

# EOF
