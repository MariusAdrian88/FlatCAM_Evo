# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 8/17/2019                                          #
# MIT Licence                                              #
# ##########################################################

from appHandlers.appHistory import history_of
from appEditors.grb_plugins.GrbCommon import (gerber_steps, gerber_quad, DrawToolUtilityShape, DrawToolShape, DrawTool, ShapeToolEditorGrb,
                                                   numeric_aperture_codes)

from camlib import distance, arc, three_point_circle, flatten_shapely_geometry, translate_geometry
from appGUI.GUIElements import *

from appEditors.grb_plugins.GrbPadPlugin import PadEditorTool

from appEditors.grb_plugins.GrbBufferPlugin import BufferEditorTool
from appEditors.grb_plugins.GrbTransformationPlugin import TransformEditorTool, scale_selection_about_centers
from appEditors.grb_plugins.GrbPadArrayPlugin import GrbPadArrayEditorTool
from appEditors.grb_plugins.GrbTrackPlugin import GrbTrackEditorTool
from appEditors.grb_plugins.GrbTrackBend import TrackBendEditorGrb
from appEditors.grb_plugins.GrbFollowPlugin import (FollowSplitEditorGrb, apply_path_split, apply_path_join,
                                                 apply_resize_circles, circular_aperture)
from appEditors.grb_plugins.GrbHistory import GerberHistory, gerber_operation, metadata
from appEditors.grb_plugins.GrbSimplificationPlugin import SimplificationTool
from appEditors.grb_plugins.GrberRegionPlugin import GrbRegionEditorTool
from appEditors.grb_plugins.GrbVertices import (grip_geometry, grip_state, apply_vertex_edit, edit_vertices_dialog,
                                              apply_selection_drag)
from appEditors.vertex_grips import VertexGrips
from appEditors.cursor_text import show_cursor_text, hide_cursor_text
from appEditors.editor_corner import corner_point, next_bend_mode, bend_mode_message
from appEditors.grb_plugins.GrbDrawingPlugin import RegionDrawingPanel, copper_pour
from appParsers.ParseGerber import Gerber

# import inspect

# from vispy.io import read_png
from vispy.geometry.rect import Rect
# import pngcanvas
import traceback
import numpy as np
from numpy.linalg import norm as numpy_norm
import math
from copy import deepcopy
from types import SimpleNamespace

from shapely import Point, Polygon, MultiPolygon, LineString, LinearRing, box, STRtree
import shapely
from shapely.ops import unary_union
from shapely.affinity import translate, rotate

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

# pick tolerance and click versus drag threshold, in screen pixels
PICK_TOLERANCE_PX = 5
CLICK_DRAG_THRESHOLD_PX = 5
# the property of the actions that the Gerber editor adds to the app menu, to remove them when the editor is rebuilt
EDITOR_ACTION_PROPERTY = 'grb_editor_action'


def aperture_rank(key):
    """
    Total order for aperture keys that can be int or str: numeric keys first (by value), the rest by their text.
    """
    if isinstance(key, int) and not isinstance(key, bool):
        return 0, key
    if isinstance(key, str) and key.isascii() and key.isdigit():
        return 0, int(key)
    return 1, str(key)


class PadEditorGrb(ShapeToolEditorGrb):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'pad'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.dont_execute = False

        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_circle.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        try:
            self.radius = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size']) / 2
        except KeyError:
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' %
                                          _("You need to preselect a aperture in the Aperture Table that has a size."))
            try:
                QtGui.QGuiApplication.restoreOverrideCursor()
            except Exception:
                pass
            self.dont_execute = True
            self.draw_app.in_action = False
            self.complete = True
            self.draw_app.select_tool('select')
            return

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.pad_tool = PadEditorTool(self.app, self.draw_app, plugin_name=_("Pad"))
        self.pad_tool.run()
        self.pad_tool.length = self.draw_app.last_length
        self.ui = self.pad_tool.ui

        if self.radius == 0:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Aperture size is zero. It needs to be greater than zero."))
            self.dont_execute = True
            return
        else:
            self.dont_execute = False

        self.storage_obj = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['geometry']

        # if those cause KeyError exception it means that the aperture type is not 'R'. Only 'R' type has those keys
        try:
            self.half_width = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['width']) / 2
        except KeyError:
            pass
        try:
            self.half_height = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['height']) / 2
        except KeyError:
            pass

        geo = self.utility_geometry(data=self.draw_app.app.mouse_pos)
        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            self.draw_app.draw_utility_geometry(geo_shape=geo)

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.app.ui.notebook.setTabText(2, _("Pad"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_pad)

        self.draw_app.app.inform.emit(_("Click to place ..."))
        self.start_msg = _("Click to place ...")

    def set_plugin_ui(self):
        dia = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size'])
        self.ui.dia_entry.set_value(dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

    def click(self, point):
        self.points = point
        self.draw_app.last_length = self.pad_tool.length
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))
        self.make()
        return "Done."

    def on_add_pad(self):
        self.draw_app.last_length = self.pad_tool.length
        self.points = self.ui.x_entry.get_value(), self.ui.y_entry.get_value()
        self.make()
        self.draw_app.on_grb_shape_complete(self.draw_app.current_storage)
        self.draw_app.build_ui()
        self.draw_app.select_tool("pad")

    def utility_geometry(self, data=None):
        if self.dont_execute is True:
            self.draw_app.select_tool('select')
            return

        pos = data if data else self.points
        geo_data = self.util_shape(pos)
        if geo_data:
            return DrawToolUtilityShape(geo_data)
        else:
            return None

    def util_shape(self, point):
        # updating values here allows us to change the aperture on the fly, after the Tool has been started
        self.storage_obj = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['geometry']
        self.radius = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size']) / 2

        # if those cause KeyError exception it means that the aperture type is not 'R'. Only 'R' type has those keys
        try:
            self.half_width = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['width']) / 2
        except KeyError:
            pass
        try:
            self.half_height = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['height']) / 2
        except KeyError:
            pass
        if point[0] is None and point[1] is None:
            point_x = self.draw_app.x
            point_y = self.draw_app.y
        else:
            point_x = point[0]
            point_y = point[1]

        ap_type = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['type']
        if ap_type == 'C':
            new_geo_el = {}

            center = Point([point_x, point_y])
            new_geo_el['solid'] = center.buffer(self.radius, quad_segs=gerber_quad(self.draw_app.app, self.radius))
            new_geo_el['follow'] = center
            return new_geo_el
        elif ap_type == 'R':
            new_geo_el = {}

            p1 = (point_x - self.half_width, point_y - self.half_height)
            p2 = (point_x + self.half_width, point_y - self.half_height)
            p3 = (point_x + self.half_width, point_y + self.half_height)
            p4 = (point_x - self.half_width, point_y + self.half_height)
            center = Point([point_x, point_y])
            new_geo_el['solid'] = Polygon([p1, p2, p3, p4, p1])
            new_geo_el['follow'] = center
            return new_geo_el
        elif ap_type == 'O':
            # the same obround as a flash of the Gerber parser
            center = Point([point_x, point_y])
            aperture = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]
            steps = gerber_steps(self.draw_app.app, min(self.half_width, self.half_height))
            return {'solid': Gerber.create_flash_geometry(self.draw_app, center, aperture, steps), 'follow': center}
        else:
            self.draw_app.app.inform.emit(_(
                "Incompatible aperture type. Select an aperture with type 'C', 'R' or 'O'."))
            return None

    def make(self):
        self.draw_app.current_storage = self.storage_obj
        try:
            self.geometry = DrawToolShape(self.util_shape(self.points))
        except Exception as e:
            log.error("AppGerberEditor.PadEditorGrb.make() --> %s" % str(e))

        self.draw_app.in_action = False
        self.complete = True

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def draw_cursor_data(self, pos=None, delete=False):
        if not self.cursor_data_control:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=(self.ui.x_entry.get_value(), self.ui.y_entry.get_value()))

    def on_key(self, key):
        # Jump to coords
        if key in [QtCore.Qt.Key.Key_J, 'J']:
            self.draw_app.app.on_jump_to()

        valid_keys = [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*']
        valid_keys += [getattr(QtCore.Qt.Key, "Key_%d" % i) for i in range(10)]
        valid_keys += [QtCore.Qt.Key.Key_Minus, QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma,
                       QtCore.Qt.Key.Key_Period, QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]
        if key in valid_keys:
            try:
                # VisPy keys
                if self.pad_tool.length == self.draw_app.last_length:
                    self.pad_tool.length = str(key.name)
                else:
                    self.pad_tool.length = str(self.pad_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.pad_tool.length == self.draw_app.last_length:
                    self.pad_tool.length = chr(key)
                else:
                    self.pad_tool.length = str(self.pad_tool.length) + chr(key)

        if key in ['Enter', QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter]:
            if self.pad_tool.length != 0:
                target_length = self.pad_tool.length
                if target_length is None:
                    self.pad_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.ui.x_entry.get_value(), self.ui.y_entry.get_value()
                last_pt = self.draw_app.app.mouse_pos

                seg_length = math.sqrt((last_pt[0] - first_pt[0])**2 + (last_pt[1] - first_pt[1])**2)
                if seg_length == 0.0:
                    self.draw_app.app.log.debug("PadEditorGrb.on_key() --> 'ENTER'. Segment is zero.")
                    return
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if first_pt != (new_x, new_y):
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    if len(self.points) > 0:
                        msg = '%s: %s. %s' % (
                            _("Projected"), str(self.pad_tool.length),
                            _("Click on next Point or click right mouse button to complete ..."))
                        self.draw_app.app.inform.emit(msg)
                        # self.interpolate_length = ''
                        # return "Click on next point or hit ENTER to complete ..."
        if key in ['C', QtCore.Qt.Key.Key_C]:
            self.cursor_data_control = not self.cursor_data_control

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class PadArrayEditorGrb(ShapeToolEditorGrb):
    """
    Resulting type: MultiPolygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'array'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.dont_execute = False

        self.cursor_data_control = True

        try:
            self.radius = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size']) / 2
        except KeyError:
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' %
                                          _("You need to preselect a aperture in the Aperture Table that has a size."))
            self.complete = True
            self.dont_execute = True
            self.draw_app.in_action = False
            self.draw_app.ui.array_frame.hide()
            self.draw_app.select_tool('select')
            return

        if self.radius == 0:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Aperture size is zero. It needs to be greater than zero."))
            self.dont_execute = True
            return
        else:
            self.dont_execute = False

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_array.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.storage_obj = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['geometry']

        # if those cause KeyError exception it means that the aperture type is not 'R'. Only 'R' type has those keys
        try:
            self.half_width = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['width']) / 2
        except KeyError:
            pass
        try:
            self.half_height = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['height']) / 2
        except KeyError:
            pass

        self.selected_size = None
        self.pad_array_dir = 'X'
        self.array_type = 'linear'  # 'linear'
        self.pad_array_size = None
        self.pad_pitch = None
        self.pad_linear_angle = None

        self.pad_circular_angle = None
        self.pad_direction = None

        self.origin = None
        self.destination = None
        self.flag_for_circ_array = None

        self.last_dx = 0
        self.last_dy = 0

        self.pt = []

        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.parray_tool = GrbPadArrayEditorTool(self.app, self.draw_app, plugin_name=_("Pad Array"))
        self.ui = self.parray_tool.ui
        self.parray_tool.run()

        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y), static=True)

        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            self.draw_app.draw_utility_geometry(geo_shape=geo)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Pad Array"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_pad_array)

        if self.ui.array_type_radio.get_value() == 'linear':
            self.draw_app.app.inform.emit(_("Click on target location ..."))
        else:
            self.draw_app.app.inform.emit(_("Click on the circular array Center position"))

    def set_plugin_ui(self):
        dia = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size'])
        self.ui.dia_entry.set_value(dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

        self.ui.array_type_radio.set_value(self.draw_app.last_parray_type)
        self.ui.on_array_type_radio(val=self.ui.array_type_radio.get_value())

        self.ui.array_size_entry.set_value(self.draw_app.last_parray_size)
        self.ui.axis_radio.set_value(self.draw_app.last_parray_lin_dir)
        self.ui.pitch_entry.set_value(self.draw_app.last_parray_pitch)
        self.ui.linear_angle_entry.set_value(self.draw_app.last_parray_lin_angle)
        self.ui.array_dir_radio.set_value(self.draw_app.last_parray_circ_dir)
        self.ui.circular_angle_entry.set_value(self.draw_app.last_parray_circ_angle)
        self.ui.radius_entry.set_value(self.draw_app.last_parray_radius)

    def click(self, point):
        if self.ui.array_type_radio.get_value() == 'linear':  # 'Linear'
            self.make()
            self.ui.x_entry.set_value(float(self.draw_app.snap_x))
            self.ui.y_entry.set_value(float(self.draw_app.snap_y))
            return

        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))
        if self.flag_for_circ_array is None:
            self.draw_app.in_action = True
            self.pt.append(point)

            self.flag_for_circ_array = True
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click on the circular array Center position"))
        else:
            self.destination = point
            self.make()
            self.flag_for_circ_array = None

    def set_origin(self, origin):
        self.origin = origin

    def utility_geometry(self, data=None, static=None):
        """

        :param data:    a tuple of coordinates (x, y)
        :type data:     tuple
        :param static:  if to draw a static temp geometry
        :type static:   bool
        :return:
        """
        if self.dont_execute is True:
            self.draw_app.select_tool('select')
            return

        self.pad_array_dir = self.ui.axis_radio.get_value()
        self.pad_direction = self.ui.array_dir_radio.get_value()
        self.array_type = self.ui.array_type_radio.get_value()

        try:
            self.pad_array_size = self.ui.array_size_entry.get_value()
        except Exception:
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % _("The value is mistyped. Check the value."))
            return

        try:
            self.pad_pitch = self.ui.pitch_entry.get_value()
            self.pad_linear_angle = self.ui.linear_angle_entry.get_value()
            self.pad_circular_angle = self.ui.circular_angle_entry.get_value()
        except TypeError:
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' %
                                          _("The value is not Float. Check for comma instead of dot separator."))
            return

        if self.array_type == 'linear':  # 'Linear'
            if data[0] is None and data[1] is None:
                dx = self.draw_app.x
                dy = self.draw_app.y
            else:
                dx = data[0]
                dy = data[1]

            geo_el_list = []
            geo_el = {}
            self.points = [dx, dy]

            for item in range(self.pad_array_size):
                if self.pad_array_dir == 'X':
                    geo_el = self.util_shape(((dx + (self.pad_pitch * item)), dy))
                if self.pad_array_dir == 'Y':
                    geo_el = self.util_shape((dx, (dy + (self.pad_pitch * item))))
                if self.pad_array_dir == 'A':
                    x_adj = self.pad_pitch * math.cos(math.radians(self.pad_linear_angle))
                    y_adj = self.pad_pitch * math.sin(math.radians(self.pad_linear_angle))
                    geo_el = self.util_shape(
                        ((dx + (x_adj * item)), (dy + (y_adj * item)))
                    )

                if static is None or static is False:
                    new_geo_el = {}

                    if 'solid' in geo_el:
                        new_geo_el['solid'] = translate(
                            geo_el['solid'], xoff=(dx - self.last_dx), yoff=(dy - self.last_dy)
                        )
                    if 'follow' in geo_el:
                        new_geo_el['follow'] = translate(
                            geo_el['follow'], xoff=(dx - self.last_dx), yoff=(dy - self.last_dy)
                        )
                    geo_el_list.append(new_geo_el)

                else:
                    geo_el_list.append(geo_el)
            # self.origin = data

            self.last_dx = dx
            self.last_dy = dy
            return DrawToolUtilityShape(geo_el_list)
        elif self.array_type == 'circular':  # 'Circular'
            if data[0] is None and data[1] is None:
                cdx = self.draw_app.x
                cdy = self.draw_app.y
            else:
                cdx = data[0]
                cdy = data[1]

            utility_list = []

            try:
                radius = distance((cdx, cdy), self.origin)
            except Exception:
                radius = 0
            if radius == 0:
                self.draw_app.delete_utility_geometry()
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

                    temp_line_el = {
                        'solid': LineString(temp_points)
                    }

                    for geo_shape in temp_circular_geo:
                        utility_list.append(geo_shape.geo)
                    utility_list.append(temp_line_el)

                    return DrawToolUtilityShape(utility_list)
                except Exception as e:
                    log.error("AppGerberEditor.PadArrayEditorGrb.utility_geometry() -> %s" % str(e))

    def util_shape(self, point):
        # updating values here allows us to change the aperture on the fly, after the Tool has been started
        self.storage_obj = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['geometry']
        self.radius = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size']) / 2

        # if those cause KeyError exception it means that the aperture type is not 'R'. Only 'R' type has those keys
        try:
            self.half_width = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['width']) / 2
        except KeyError:
            pass
        try:
            self.half_height = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['height']) / 2
        except KeyError:
            pass

        if point[0] is None and point[1] is None:
            point_x = self.draw_app.x
            point_y = self.draw_app.y
        else:
            point_x = point[0]
            point_y = point[1]

        ap_type = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['type']
        if ap_type == 'C':
            new_geo_el = {}

            center = Point([point_x, point_y])
            new_geo_el['solid'] = center.buffer(self.radius, quad_segs=gerber_quad(self.draw_app.app, self.radius))
            new_geo_el['follow'] = center
            return new_geo_el
        elif ap_type == 'R':
            new_geo_el = {}

            p1 = (point_x - self.half_width, point_y - self.half_height)
            p2 = (point_x + self.half_width, point_y - self.half_height)
            p3 = (point_x + self.half_width, point_y + self.half_height)
            p4 = (point_x - self.half_width, point_y + self.half_height)
            new_geo_el['solid'] = Polygon([p1, p2, p3, p4, p1])
            new_geo_el['follow'] = Point([point_x, point_y])
            return new_geo_el
        elif ap_type == 'O':
            # the same obround as a flash of the Gerber parser
            center = Point([point_x, point_y])
            aperture = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]
            steps = gerber_steps(self.draw_app.app, min(self.half_width, self.half_height))
            return {'solid': Gerber.create_flash_geometry(self.draw_app, center, aperture, steps), 'follow': center}
        else:
            self.draw_app.app.inform.emit(_(
                "Incompatible aperture type. Select an aperture with type 'C', 'R' or 'O'."))
            return None

    def circular_util_shape(self, radius, angle):
        self.pad_direction = self.ui.array_dir_radio.get_value()
        self.pad_circular_angle = self.ui.circular_angle_entry.get_value()

        circular_geo = []
        if self.pad_direction == 'CW':
            for i in range(self.pad_array_size):
                angle_radians = math.radians(self.pad_circular_angle * i)
                x = self.origin[0] + radius * math.cos(-angle_radians + angle)
                y = self.origin[1] + radius * math.sin(-angle_radians + angle)

                geo = self.util_shape((x, y))
                geo_sol = rotate(geo['solid'], angle=(math.pi - angle_radians + angle), use_radians=True)
                geo_fol = rotate(geo['follow'], angle=(math.pi - angle_radians + angle), use_radians=True)
                geo_el = {
                    'solid': geo_sol,
                    'follow': geo_fol
                }
                circular_geo.append(DrawToolShape(geo_el))
        else:
            for i in range(self.pad_array_size):
                angle_radians = math.radians(self.pad_circular_angle * i)
                x = self.origin[0] + radius * math.cos(angle_radians + angle)
                y = self.origin[1] + radius * math.sin(angle_radians + angle)

                geo = self.util_shape((x, y))
                geo_sol = rotate(geo['solid'], angle=(angle_radians + angle - math.pi), use_radians=True)
                geo_fol = rotate(geo['follow'], angle=(angle_radians + angle - math.pi), use_radians=True)
                geo_el = {
                    'solid': geo_sol,
                    'follow': geo_fol
                }
                circular_geo.append(DrawToolShape(geo_el))

        return circular_geo

    def make(self):
        self.geometry = []
        geo = None

        self.draw_app.current_storage = self.storage_obj

        if self.array_type == 'linear':  # 'Linear'
            for item in range(self.pad_array_size):
                if self.pad_array_dir == 'X':
                    geo = self.util_shape(((self.points[0] + (self.pad_pitch * item)), self.points[1]))
                if self.pad_array_dir == 'Y':
                    geo = self.util_shape((self.points[0], (self.points[1] + (self.pad_pitch * item))))
                if self.pad_array_dir == 'A':
                    x_adj = self.pad_pitch * math.cos(math.radians(self.pad_linear_angle))
                    y_adj = self.pad_pitch * math.sin(math.radians(self.pad_linear_angle))
                    geo = self.util_shape(
                        ((self.points[0] + (x_adj * item)), (self.points[1] + (y_adj * item)))
                    )

                self.geometry.append(DrawToolShape(geo))
        else:  # 'Circular'
            if (self.pad_circular_angle * self.pad_array_size) > 360:
                self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                              _("Too many items for the selected spacing angle."))
                return

            radius = distance(self.destination, self.origin)
            if radius == 0:
                self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                self.draw_app.delete_utility_geometry()
                self.draw_app.select_tool('select')
                return

            if self.destination[0] < self.origin[0]:
                radius = -radius
            initial_angle = math.asin((self.destination[1] - self.origin[1]) / radius)

            circular_geo = self.circular_util_shape(radius, initial_angle)
            self.geometry += circular_geo

        self.complete = True
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        self.draw_app.in_action = False

        self.draw_app.last_parray_type = self.ui.array_type_radio.get_value()
        self.draw_app.last_parray_size = self.ui.array_size_entry.get_value()
        self.draw_app.last_parray_lin_dir = self.ui.axis_radio.get_value()
        self.draw_app.last_parray_circ_dir = self.ui.array_dir_radio.get_value()
        self.draw_app.last_parray_pitch = self.ui.pitch_entry.get_value()
        self.draw_app.last_parray_lin_angle = self.ui.linear_angle_entry.get_value()
        self.draw_app.last_parray_circ_angle = self.ui.circular_angle_entry.get_value()
        self.draw_app.last_parray_radius = self.ui.radius_entry.get_value()

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
            pass
        elif mod_key is None:
            # Toggle Drill Array Direction
            if key == QtCore.Qt.Key.Key_Space:
                if self.ui.pad_dir_radio.get_value() == 'X':
                    self.ui.pad_dir_radio.set_value('Y')
                elif self.ui.pad_axis_radio.get_value() == 'Y':
                    self.ui.pad_axis_radio.set_value('A')
                elif self.ui.pad_axis_radio.get_value() == 'A':
                    self.ui.pad_axis_radio.set_value('X')

                # ## Utility geometry (animated)
                self.draw_app.update_utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))

            elif key == 'C' or key == QtCore.Qt.Key.Key_C:
                self.cursor_data_control = not self.cursor_data_control

    def add_pad_array(self, array_pos):
        self.radius = self.ui.radius_entry.get_value()
        self.array_type = self.ui.array_type_radio.get_value()

        curr_pos = self.draw_app.app.geo_editor.snap(array_pos[0], array_pos[1])
        self.draw_app.snap_x = curr_pos[0]
        self.draw_app.snap_y = curr_pos[1]

        self.points = [self.draw_app.snap_x, self.draw_app.snap_y]
        self.origin = [self.draw_app.snap_x, self.draw_app.snap_y]
        self.destination = ((self.origin[0] + self.radius), self.origin[1])
        self.flag_for_circ_array = True
        self.make()

        if self.draw_app.current_storage is not None:
            self.draw_app.on_grb_shape_complete(self.draw_app.current_storage)
            self.draw_app.build_ui()

        if self.draw_app.active_tool.complete:
            self.draw_app.on_shape_complete()

        self.draw_app.select_tool("select")

        self.draw_app.clicked_pos = curr_pos

    def on_add_pad_array(self):
        x = self.ui.x_entry.get_value()
        y = self.ui.y_entry.get_value()
        self.add_pad_array(array_pos=(x, y))

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class PoligonizeEditorGrb(ShapeToolEditorGrb):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'poligonize'
        self.draw_app = draw_app

        self.draw_app.app.inform.emit(_("Select shape(s) and then click ..."))
        self.draw_app.in_action = True
        self.make()

    def click(self, point):
        return ""

    @gerber_operation(_('Polygonize'))
    def make(self):
        if self.complete:
            return
        if not self.draw_app.selected:
            self.draw_app.in_action = False
            self.complete = True
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' %
                                          _("Failed. Nothing selected."))
            self.draw_app.select_tool("select")
            return

        if self.draw_app.apply_region_operation('union') == 'fail':
            self.draw_app.in_action = False
            self.complete = True
            self.draw_app.select_tool('select')
            return 'fail'

        self.draw_app.in_action = False
        self.complete = True
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

        # MS: always return to the Select Tool if modifier key is not pressed
        # else return to the current tool
        key_modifier = QtWidgets.QApplication.keyboardModifiers()
        if self.draw_app.app.options.get(
                "global_mselect_key",
                self.draw_app.app.defaults.get(
                    "global_mselect_key",
                    self.draw_app.app.defaults.factory_defaults.get("global_mselect_key")
                )) == 'Control':
            modifier_to_use = Qt.KeyboardModifier.ControlModifier
        else:
            modifier_to_use = Qt.KeyboardModifier.ShiftModifier
        # if modifier key is pressed then we add to the selected list the current shape but if it's already
        # in the selected list, we removed it. Therefore, first click selects, second deselects.
        if key_modifier == modifier_to_use:
            self.draw_app.select_tool(self.draw_app.active_tool.name)
        else:
            self.draw_app.select_tool("select")
            return

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()


class RegionEditorGrb(ShapeToolEditorGrb):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'region'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.dont_execute = False


        # Aperture 0 is created only when the region is committed, not by opening a drawing tool.

        self.buf_val = 0.0000001    # so we can see something on the screen
        self.quad_segs = gerber_quad(self.app, self.buf_val)

        self.gridx_size = float(self.draw_app.app.ui.grid_gap_x_entry.get_value())
        self.gridy_size = float(self.draw_app.app.ui.grid_gap_y_entry.get_value())

        self.temp_points = []
        # this will store the inflexion point in the geometry
        self.inter_point = None
        # how many points each click added (corner included), to backtrack exactly that many
        self.click_sizes = []

        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.RegionEditorGrb --> %s" % str(e))

        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.reg_tool = GrbRegionEditorTool(self.app, self.draw_app, plugin_name=_("Region"))
        self.reg_tool.run()
        self.reg_tool.length = self.draw_app.last_length
        self.ui = self.reg_tool.ui

        self.set_plugin_ui()

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.draw_app.app.inform.emit(bend_mode_message(self.draw_app.bend_mode))
        self.start_msg = _("Click on 1st point ...")

    def set_plugin_ui(self):
        dia = 0.0
        self.ui.dia_entry.set_value(dia)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

    def click(self, point):
        self.draw_app.in_action = True

        # the corner is computed from the clicked point, so it can not be stale
        if self.points:
            self.inter_point = corner_point(self.draw_app.bend_mode, self.points[-1], point)
        else:
            self.inter_point = None
        added = [self.inter_point, point] if self.inter_point is not None else [point]
        self.points.extend(added)
        self.__dict__.setdefault('click_sizes', []).append(len(added))
        self.draw_app.last_length = self.reg_tool.length
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

        if len(self.points) > 0:
            self.draw_app.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))
            return "Click on next point or hit ENTER to complete ..."

        return ""

    def update_grid_info(self):
        self.gridx_size = float(self.draw_app.app.ui.grid_gap_x_entry.get_value())
        self.gridy_size = float(self.draw_app.app.ui.grid_gap_y_entry.get_value())

    def utility_geometry(self, data=None):
        if self.dont_execute is True:
            self.draw_app.select_tool('select')
            return

        new_geo_el = {}
        x = data[0]
        y = data[1]

        if len(self.points) == 0:
            new_geo_el['solid'] = Point((x, y)).buffer(self.buf_val, quad_segs=self.quad_segs)
            return DrawToolUtilityShape(new_geo_el)

        else:
            # previous point coordinates
            old_x = self.points[-1][0]
            old_y = self.points[-1][1]

            # the intermediary point (None if none is needed); it is recomputed on every call
            self.inter_point = corner_point(self.draw_app.bend_mode, (old_x, old_y), (x, y))

            self.temp_points = [pt for pt in self.points]
            if self.inter_point is not None:
                self.temp_points.append(self.inter_point)
            # add click point to the points storage
            self.temp_points.append((x, y))

            if len(self.points) == 1:
                if len(self.temp_points) > 1:
                    try:
                        geo_sol = LineString(self.temp_points)
                        geo_sol = geo_sol.buffer(self.buf_val, self.quad_segs, join_style="round")
                        new_geo_el = {
                            'solid': geo_sol
                        }
                        return DrawToolUtilityShape(new_geo_el)
                    except Exception as e:
                        log.error("AppGerberEditor.RegionEditorGrb.utility_geometry() --> %s" % str(e))
                else:
                    geo_sol = Point(self.temp_points).buffer(self.buf_val, quad_segs=self.quad_segs)
                    new_geo_el = {
                        'solid': geo_sol
                    }
                    return DrawToolUtilityShape(new_geo_el)
            else:
                # create the geometry
                geo_line = LinearRing(self.temp_points)
                geo_sol = geo_line.buffer(self.buf_val, self.quad_segs, join_style="round")
                new_geo_el = {
                    'solid': geo_sol,
                    'follow': geo_line
                }

                return DrawToolUtilityShape(new_geo_el)

        return None

    def make(self):
        # self.geometry = LinearRing(self.points)
        if len(self.points) > 2:

            new_geo_el = {
                'solid': Polygon(self.points).buffer(self.buf_val, self.quad_segs, join_style="mitre"),
                'follow': Polygon(self.points).exterior
            }

            self.geometry = DrawToolShape(new_geo_el)
        self.draw_app.in_action = False
        self.complete = True

        self.draw_app.app.jump_signal.disconnect()

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

        show_cursor_text(self.draw_app, pos, ref=(self.ui.x_entry.get_value(), self.ui.y_entry.get_value()))

    def cycle_bend_mode(self, forward):
        """
        Change the corner mode (T = forward, R = backward) and refresh the utility shape.
        """
        self.draw_app.bend_mode = next_bend_mode(self.draw_app.bend_mode, forward)

        # Remove any previous utility shape
        self.draw_app.tool_shape.clear(update=False)
        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
        self.draw_app.draw_utility_geometry(geo_shape=geo)

        return bend_mode_message(self.draw_app.bend_mode)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key == 'Backspace' or key == QtCore.Qt.Key.Key_Backspace:
            if len(self.points) > 0:
                # remove the points added by the last click (the corner included)
                sizes = self.__dict__.setdefault('click_sizes', [])
                count = sizes.pop() if sizes else 1
                self.points = self.points[0:-count] if count else self.points[:]
                # Remove any previous utility shape
                self.draw_app.tool_shape.clear(update=False)
                geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
                self.draw_app.draw_utility_geometry(geo_shape=geo)
                return _("Backtracked one point ...")

        if key == 'T' or key == QtCore.Qt.Key.Key_T:
            return self.cycle_bend_mode(True)

        if key == 'R' or key == QtCore.Qt.Key.Key_R:
            return self.cycle_bend_mode(False)

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.reg_tool.length == self.draw_app.last_length:
                    self.reg_tool.length = str(key.name)
                else:
                    self.reg_tool.length = str(self.reg_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.reg_tool.length == self.draw_app.last_length:
                    self.reg_tool.length = chr(key)
                else:
                    self.reg_tool.length = str(self.reg_tool.length) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if self.reg_tool.length != 0:
                target_length = self.reg_tool.length
                if target_length is None:
                    self.reg_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.ui.x_entry.get_value(), self.ui.y_entry.get_value()
                last_pt = self.draw_app.app.mouse_pos

                seg_length = math.sqrt((last_pt[0] - first_pt[0])**2 + (last_pt[1] - first_pt[1])**2)
                if seg_length == 0.0:
                    self.draw_app.app.log.debug("GrbRegionEditorGrb.on_key() --> 'ENTER'. Segment is zero.")
                    return
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if first_pt != (new_x, new_y):
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    if len(self.points) > 0:
                        msg = '%s: %s. %s' % (
                            _("Projected"), str(self.reg_tool.length),
                            _("Click on next Point or click right mouse button to complete ..."))
                        self.draw_app.app.inform.emit(msg)
                        # self.interpolate_length = ''
                        # return "Click on next point or hit ENTER to complete ..."

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class CopperPourEditorGrb(RegionEditorGrb):
    """Region vertex interaction with a clearance-only Properties panel."""

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.draw_app = draw_app
        self.app = draw_app.app
        self.name = 'copper_pour'
        self.dont_execute = False
        self.buf_val = 0.0000001
        self.quad_segs = gerber_quad(self.app, self.buf_val)
        self.temp_points = []
        self.inter_point = None
        self.click_sizes = []
        self.cursor_data_control = True
        self.session = draw_app.history.session
        self.units = self.app.app_units
        self.update_grid_info()
        self.panel = QtWidgets.QWidget(draw_app.ui.grb_edit_widget)
        layout = QtWidgets.QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(FCLabel(_("Copper Pour"), bold=True, color='tomato'))
        clearance_frame = FCFrame()
        layout.addWidget(clearance_frame)
        clearance_grid = GLay(v_spacing=5, h_spacing=3)
        clearance_frame.setLayout(clearance_grid)
        self.clearance_entry = FCDoubleSpinner()
        self.clearance_entry.set_precision(self.app.decimals)
        self.clearance_entry.set_range(0, 10000)
        self.clearance_entry.set_value(0)
        clearance_grid.addWidget(FCLabel(_("Clearance")), 0, 0)
        clearance_grid.addWidget(self.clearance_entry, 0, 1)
        # Region's coordinate controls support vertex keys but aren't exposed as pour properties.
        self.ui = SimpleNamespace(x_entry=FCDoubleSpinner(), y_entry=FCDoubleSpinner())
        for entry in (self.ui.x_entry, self.ui.y_entry):
            entry.set_precision(self.app.decimals)
            entry.set_range(-10000, 10000)
        self.ui.x_entry.set_value(float(draw_app.snap_x))
        self.ui.y_entry.set_value(float(draw_app.snap_y))
        self.reg_tool = SimpleNamespace(length=draw_app.last_length)
        draw_app.ui.custom_box.insertWidget(draw_app.ui.custom_box.indexOf(draw_app.ui.shape_frame), self.panel)
        if self.app.ui.properties_scroll_area.widget() is not draw_app.ui.grb_edit_widget:
            self.app.ui.properties_scroll_area.takeWidget()
            self.app.ui.properties_scroll_area.setWidget(draw_app.ui.grb_edit_widget)
        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
        self.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.app.jump_signal.connect(lambda pos: draw_app.update_utility_geometry(data=pos))
        self.start_msg = _("Click on 1st point ...")

    def make(self):
        try:
            if self.session != self.draw_app.history.session or self.units != self.app.app_units:
                raise ValueError(_("Editor session or units changed. Reopen Copper Pour."))
            if len(self.points) < 3:
                raise ValueError(_("A polygon needs at least three vertices."))
            clearance = self.clearance_entry.get_value()
            result = copper_pour(self.draw_app, Polygon(self.points), clearance,
                                 gerber_quad(self.app, clearance))
            if result.is_empty:
                raise ValueError(_("No copper remains inside the outline."))
            self.geometry = result
            self.complete = True
            self.draw_app.in_action = False
        except Exception as error:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(error))

    def clean_up(self):
        if self.panel is None:
            return
        self.draw_app.ui.custom_box.removeWidget(self.panel)
        self.panel.hide()
        self.panel.deleteLater()
        self.panel = None
        self.ui.x_entry.deleteLater()
        self.ui.y_entry.deleteLater()
        self.draw_app.in_action = False
        self.draw_app.delete_utility_geometry()
        self.app.plotcanvas.view.camera.zoom_callback = None
        hide_cursor_text(self.app)
        super().clean_up()


class TrackEditorGrb(ShapeToolEditorGrb):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'track'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.dont_execute = False

        try:
            self.size_ap = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size'])
        except KeyError:
            self.draw_app.app.inform.emit('[ERROR_NOTCL] %s' %
                                          _("You need to preselect a aperture in the Aperture Table that has a size."))
            try:
                QtGui.QGuiApplication.restoreOverrideCursor()
            except Exception:
                pass
            self.dont_execute = True
            self.draw_app.in_action = False
            self.complete = True
            self.draw_app.select_tool('select')
            return

        self.buf_val = (self.size_ap / 2) if self.size_ap > 0 else 0.0000001
        self.quad_segs = gerber_quad(self.app, self.buf_val)

        self.gridx_size = float(self.draw_app.app.ui.grid_gap_x_entry.get_value())
        self.gridy_size = float(self.draw_app.app.ui.grid_gap_y_entry.get_value())

        self.temp_points = []

        self.current_point = None

        self.final_click = False

        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.TrackEditorGrb.__init__() --> %s" % str(e))

        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location +
                                                  '/aero_path%s.png' % self.draw_app.bend_mode))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        # #############################################################################################################
        # Plugin UI
        # #############################################################################################################
        self.track_tool = GrbTrackEditorTool(self.app, self.draw_app, plugin_name=_("Track"))
        self.ui = self.track_tool.ui
        previous_tool = self.draw_app.active_tool
        continuing = isinstance(previous_tool, TrackEditorGrb)
        self.ui.snap_to_pad_cb.setChecked(continuing and previous_tool.ui.snap_to_pad_cb.isChecked())
        self.fine_grid = continuing and getattr(previous_tool, 'fine_grid', False)
        self.track_tool.run()

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.track_tool.length = self.draw_app.last_length
        if not self.draw_app.snap_x:
            self.draw_app.snap_x = 0.0
        if not self.draw_app.snap_y:
            self.draw_app.snap_y = 0.0

        self.app.ui.notebook.setTabText(2, _("Track"))
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        self.set_plugin_ui()

        # Signals
        try:
            self.ui.add_btn.clicked.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.add_btn.clicked.connect(self.on_add_track)

        # self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        # the segment shape added to the storage by each click, so Backspace can remove it: (shape, storage)
        self.click_shapes = []

        self.draw_app.app.inform.emit(bend_mode_message(self.draw_app.bend_mode))

    def set_plugin_ui(self):
        self.ui.dia_entry.set_value(self.size_ap)
        self.ui.x_entry.set_value(float(self.draw_app.snap_x))
        self.ui.y_entry.set_value(float(self.draw_app.snap_y))

    @gerber_operation(_('Draw track'))
    def click(self, point):
        self.utility_geometry(data=point)
        if not self.points:
            self.temp_points = [point]
        self.draw_app.in_action = True
        self.current_point = point

        if not self.points or point != self.points[-1]:
            self.points.append(point)
            self.draw_app.last_length = self.track_tool.length
            self.ui.x_entry.set_value(float(self.draw_app.snap_x))
            self.ui.y_entry.set_value(float(self.draw_app.snap_y))
        else:
            return

        if len(self.temp_points) == 1:
            point_geo = Point(self.temp_points[0])
            new_geo_el = {
                'solid': point_geo.buffer(self.buf_val, self.quad_segs),
                'follow': point_geo
            }
        else:
            line_geo = LineString(self.temp_points)
            new_geo_el = {
                'solid': line_geo.buffer(self.buf_val, self.quad_segs),
                'follow': line_geo
            }

        new_shape = DrawToolShape(new_geo_el)
        storage = self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['geometry']
        self.draw_app.add_gerber_shape(new_shape, storage)
        self.click_shapes.append((new_shape, storage))

        self.draw_app.plot_all()
        if len(self.points) > 0:
            self.draw_app.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))
            return "Click on next point or hit ENTER to complete ..."

        return ""

    def update_grid_info(self):
        self.gridx_size = float(self.draw_app.app.ui.grid_gap_x_entry.get_value())
        self.gridy_size = float(self.draw_app.app.ui.grid_gap_y_entry.get_value())

    def utility_geometry(self, data=None):
        if self.dont_execute is True:
            self.draw_app.select_tool('select')
            return

        self.update_grid_info()

        if not self.points:
            new_geo_el = {
                'solid': Point(data).buffer(self.buf_val, self.quad_segs)
            }
            return DrawToolUtilityShape(new_geo_el)
        else:
            old_x = self.points[-1][0]
            old_y = self.points[-1][1]
            x = data[0]
            y = data[1]

            self.temp_points = [self.points[-1]]

            corner = corner_point(self.draw_app.bend_mode, (old_x, old_y), (x, y))
            if corner is not None:
                self.temp_points.append(corner)

            self.temp_points.append(data)

            if len(self.temp_points) == 1:
                new_geo_el = {
                    'solid': Point(self.temp_points[0]).buffer(self.buf_val, self.quad_segs)
                }
            else:
                new_geo_el = {
                    'solid': LineString(self.temp_points).buffer(self.buf_val, self.quad_segs)
                }

            return DrawToolUtilityShape(new_geo_el)

    def make(self):
        # Click records are provisional: finish the whole route, not another copy of its last segment.
        coords = []
        for shape, storage in self.click_shapes:
            follow = shape.geo['follow']
            if not follow.is_empty:
                points = list(follow.coords)
                coords.extend(points[1:] if coords and coords[-1] == points[0] else points)
        if self.temp_points and (not coords or coords[-1] != tuple(self.temp_points[-1])):
            points = [tuple(point) for point in self.temp_points]
            coords.extend(points[1:] if coords and coords[-1] == points[0] else points)
        if not coords:
            coords = list(self.points[:1])
        follow_geo = Point(coords[0]) if len(coords) == 1 else LineString(coords)
        solid_geo = follow_geo.buffer(self.buf_val, self.quad_segs)

        new_geo_el = {
            'solid': solid_geo,
            'follow': follow_geo
        }
        self.geometry = DrawToolShape(new_geo_el)

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

        ref = self.points[-1] if self.points else (self.draw_app.snap_x, self.draw_app.snap_y)
        show_cursor_text(self.draw_app, pos, ref=ref)

    def cycle_bend_mode(self, forward):
        """
        Change the corner mode (T = forward, R = backward), update the cursor and refresh the utility shape.
        """
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.TrackEditorGrb.cycle_bend_mode() --> %s" % str(e))

        self.draw_app.bend_mode = next_bend_mode(self.draw_app.bend_mode, forward)
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location +
                                                  '/aero_path%s.png' % self.draw_app.bend_mode))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        # Remove any previous utility shape
        self.draw_app.tool_shape.clear(update=False)
        geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
        self.draw_app.draw_utility_geometry(geo_shape=geo)

        return bend_mode_message(self.draw_app.bend_mode)

    @gerber_operation(_('Edit track'))
    def on_key(self, key):
        if key == 'Space' or key == QtCore.Qt.Key.Key_Space:
            self.set_fine_grid(True)
            return

        if key == 'Backspace' or key == QtCore.Qt.Key.Key_Backspace:
            if len(self.points) > 0:
                # remove the last point and the segment shape that its click added to the storage
                self.points = self.points[0:-1]
                if self.click_shapes:
                    shape, storage = self.click_shapes.pop()
                    if shape in storage:
                        storage.remove(shape)
                    self.draw_app.plot_all()
                if not self.points:
                    self.temp_points = []
                # Remove any previous utility shape
                self.draw_app.tool_shape.clear(update=False)
                geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
                self.draw_app.draw_utility_geometry(geo_shape=geo)
                return _("Backtracked one point ...")

        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_G or key == 'G':
            self.draw_app.app.ui.grid_snap_btn.trigger()

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key == 'T' or key == QtCore.Qt.Key.Key_T:
            return self.cycle_bend_mode(True)

        if key == 'R' or key == QtCore.Qt.Key.Key_R:
            return self.cycle_bend_mode(False)

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.track_tool.length == self.draw_app.last_length:
                    self.track_tool.length = str(key.name)
                else:
                    self.track_tool.length = str(self.track_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.track_tool.length == self.draw_app.last_length:
                    self.track_tool.length = chr(key)
                else:
                    self.track_tool.length = str(self.track_tool.length) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if self.track_tool.length != 0:
                target_length = self.track_tool.length
                if target_length is None:
                    self.track_tool.length = 0.0
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
                    self.add_track(track_pos=(new_x, new_y))

    def set_fine_grid(self, enabled):
        self.fine_grid = enabled
        if self.draw_app.x is not None and self.draw_app.y is not None:
            self.draw_app.on_canvas_move(SimpleNamespace(
                pos=self.draw_app.canvas.translate_coords_2((self.draw_app.x, self.draw_app.y)),
                button=None, is_dragging=False))

    def add_track(self, track_pos):
        self.draw_app.last_length = self.track_tool.length
        self.click(track_pos)
        self.ui.x_entry.set_value(track_pos[0])
        self.ui.y_entry.set_value(track_pos[1])
        self.draw_app.clicked_pos = track_pos

    def on_add_track(self):
        x = self.ui.x_entry.get_value()
        y = self.ui.y_entry.get_value()
        self.add_track(track_pos=(x, y))

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class DiscEditorGrb(ShapeToolEditorGrb):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'disc'
        self.dont_execute = False

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_disc.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        try:
            size_ap = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size'])
        except KeyError:
            size_ap = 0.0
            # self.draw_app.app.inform.emit(
            #     '[ERROR_NOTCL] %s' % _("You need to preselect an aperture in the Aperture Table that has a size."))
            # try:
            #     QtGui.QGuiApplication.restoreOverrideCursor()
            # except Exception:
            #     pass
            # self.dont_execute = True
            # self.draw_app.in_action = False
            # self.complete = True
            # self.draw_app.select_tool('select')
            # return

        self.buf_val = (size_ap / 2) if size_ap > 0 else 0.0000001

        self.storage_obj = self.draw_app.storage_dict.get(0, {}).get('geometry', [])

        self.draw_app.app.inform.emit(_("Click on Center point ..."))

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))


    def click(self, point):
        self.points.append(point)

        if len(self.points) == 1:
            self.draw_app.app.inform.emit(_("Click on Perimeter point to complete ..."))
            return "Click on Perimeter to complete ..."

        if len(self.points) == 2:
            self.make()
            return "Done."

        return ""

    def utility_geometry(self, data=None):
        if self.dont_execute is True:
            self.draw_app.select_tool('select')
            return

        new_geo_el = {}
        if len(self.points) == 1:
            p1 = self.points[0]
            p2 = data
            radius = math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
            disc_radius = radius + self.buf_val / 2
            new_geo_el['solid'] = Point(p1).buffer(disc_radius, gerber_quad(self.draw_app.app, disc_radius))
            return DrawToolUtilityShape(new_geo_el)

        return None

    def make(self):
        new_geo_el = {}

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.DiscEditorGrb --> %s" % str(e))

        self.draw_app.current_storage = self.storage_obj

        p1 = self.points[0]
        p2 = self.points[1]
        radius = distance(p1, p2)
        disc_radius = radius + self.buf_val / 2

        new_geo_el['solid'] = Point(p1).buffer(disc_radius, gerber_quad(self.draw_app.app, disc_radius))
        new_geo_el['follow'] = Point(p1).buffer(disc_radius, gerber_quad(self.draw_app.app, disc_radius)).exterior
        self.geometry = DrawToolShape(new_geo_el)

        self.draw_app.in_action = False
        self.complete = True

        self.draw_app.app.jump_signal.disconnect()

        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class DiscSemiEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'semidisc'
        self.dont_execute = False

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.DiscSemiEditorGrb --> %s" % str(e))

        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_semidisc.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.draw_app.app.inform.emit(_("Click on Center point ..."))

        # Direction of rotation between point 1 and 2.
        # 'cw' or 'ccw'. Switch direction by hitting the
        # 'o' key.
        self.direction = "cw"

        # Mode
        # C12 = Center, p1, p2
        # 12C = p1, p2, Center
        # 132 = p1, p3, p2
        self.mode = "c12"  # Center, p1, p2

        try:
            size_ap = float(self.draw_app.storage_dict[self.draw_app.last_aperture_selected]['size'])
        except KeyError:
            size_ap = 0.0
            # self.draw_app.app.inform.emit(
            #     '[ERROR_NOTCL] %s' % _("You need to preselect an aperture in the Aperture Table that has a size."))
            # try:
            #     QtGui.QGuiApplication.restoreOverrideCursor()
            # except Exception:
            #     pass
            # self.dont_execute = True
            # self.draw_app.in_action = False
            # self.complete = True
            # self.draw_app.select_tool('select')
            # return

        self.buf_val = (size_ap / 2) if size_ap > 0 else 0.0000001

        self.storage_obj = self.draw_app.storage_dict.get(0, {}).get('geometry', [])

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

    def click(self, point):
        self.points.append(point)

        if len(self.points) == 1:
            if self.mode == 'c12':
                self.draw_app.app.inform.emit(_("Click on Start point ..."))
            elif self.mode == '132':
                self.draw_app.app.inform.emit(_("Click on Point3 ..."))
            else:
                self.draw_app.app.inform.emit(_("Click on Stop point ..."))
            return "Click on 1st point ..."

        if len(self.points) == 2:
            if self.mode == 'c12':
                self.draw_app.app.inform.emit(_("Click on Stop point to complete ..."))
            elif self.mode == '132':
                self.draw_app.app.inform.emit(_("Click on Point2 to complete ..."))
            else:
                self.draw_app.app.inform.emit(_("Click on Center point to complete ..."))
            return "Click on 2nd point to complete ..."

        if len(self.points) == 3:
            self.make()
            return "Done."

        return ""

    def on_key(self, key):
        if key == 'D' or key == QtCore.Qt.Key.Key_D:
            self.direction = 'cw' if self.direction == 'ccw' else 'ccw'
            return '%s: %s' % (_('Direction'), self.direction.upper())

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key == 'M' or key == QtCore.Qt.Key.Key_M:
            # delete the possible points made before this action; we want to start anew
            self.points = []
            # and delete the utility geometry made up until this point
            self.draw_app.delete_utility_geometry()

            if self.mode == 'c12':
                self.mode = '12c'
                return _('Mode: Start -> Stop -> Center. Click on Start point ...')
            elif self.mode == '12c':
                self.mode = '132'
                return _('Mode: Point1 -> Point3 -> Point2. Click on Point1 ...')
            else:
                self.mode = 'c12'
                return _('Mode: Center -> Start -> Stop. Click on Center point ...')

    def utility_geometry(self, data=None):
        if self.dont_execute is True:
            self.draw_app.select_tool('select')
            return

        new_geo_el = {}
        new_geo_el_pt1 = {}
        new_geo_el_pt2 = {}
        new_geo_el_pt3 = {}

        if len(self.points) == 1:  # Show the radius
            center = self.points[0]
            p1 = data
            new_geo_el['solid'] = LineString([center, p1])
            return DrawToolUtilityShape(new_geo_el)

        if len(self.points) == 2:  # Show the arc

            if self.mode == 'c12':
                center = self.points[0]
                p1 = self.points[1]
                p2 = data

                radius = np.sqrt((center[0] - p1[0]) ** 2 + (center[1] - p1[1]) ** 2) + (self.buf_val / 2)
                startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
                stopangle = np.arctan2(p2[1] - center[1], p2[0] - center[0])

                new_geo_el['solid'] = LineString(
                    arc(center, radius, startangle, stopangle, self.direction, gerber_steps(self.draw_app.app, radius)))
                new_geo_el_pt1['solid'] = Point(center)
                return DrawToolUtilityShape([new_geo_el, new_geo_el_pt1])

            elif self.mode == '132':
                p1 = np.array(self.points[0])
                p3 = np.array(self.points[1])
                p2 = np.array(data)

                try:
                    center, radius, t = three_point_circle(p1, p2, p3)
                except TypeError:
                    return

                direction = 'cw' if np.sign(t) > 0 else 'ccw'
                radius += (self.buf_val / 2)

                startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
                stopangle = np.arctan2(p3[1] - center[1], p3[0] - center[0])

                new_geo_el['solid'] = LineString(
                    arc(center, radius, startangle, stopangle, direction, gerber_steps(self.draw_app.app, radius)))
                new_geo_el_pt2['solid'] = Point(center)
                new_geo_el_pt1['solid'] = Point(p1)
                new_geo_el_pt3['solid'] = Point(p3)

                return DrawToolUtilityShape([new_geo_el, new_geo_el_pt2, new_geo_el_pt1, new_geo_el_pt3])

            else:  # '12c'
                p1 = np.array(self.points[0])
                p2 = np.array(self.points[1])
                # Midpoint
                a = (p1 + p2) / 2.0

                # Parallel vector
                c = p2 - p1

                # Perpendicular vector
                b = np.dot(c, np.array([[0, -1], [1, 0]], dtype=np.float32))
                b /= numpy_norm(b)

                # Distance
                t = distance(data, a)

                # Which side? Cross product with c.
                # cross(M-A, B-A), where line is AB and M is a test point.
                side = (data[0] - p1[0]) * c[1] - (data[1] - p1[1]) * c[0]
                t *= np.sign(side)

                # Center = a + bt
                center = a + b * t

                radius = numpy_norm(center - p1) + (self.buf_val / 2)
                startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
                stopangle = np.arctan2(p2[1] - center[1], p2[0] - center[0])

                new_geo_el['solid'] = LineString(
                    arc(center, radius, startangle, stopangle, self.direction, gerber_steps(self.draw_app.app, radius)))
                new_geo_el_pt2['solid'] = Point(center)

                return DrawToolUtilityShape([new_geo_el, new_geo_el_pt2])

        return None

    def make(self):
        self.draw_app.current_storage = self.storage_obj
        new_geo_el = {}

        if self.mode == 'c12':
            center = self.points[0]
            p1 = self.points[1]
            p2 = self.points[2]

            radius = distance(center, p1) + (self.buf_val / 2)
            start_angle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
            stop_angle = np.arctan2(p2[1] - center[1], p2[0] - center[0])
            new_geo_el['solid'] = Polygon(
                arc(center, radius, start_angle, stop_angle, self.direction, gerber_steps(self.draw_app.app, radius)))
            new_geo_el['follow'] = Polygon(
                arc(center, radius, start_angle, stop_angle, self.direction, gerber_steps(self.draw_app.app, radius))).exterior
            self.geometry = DrawToolShape(new_geo_el)

        elif self.mode == '132':
            p1 = np.array(self.points[0])
            p3 = np.array(self.points[1])
            p2 = np.array(self.points[2])

            center, radius, t = three_point_circle(p1, p2, p3)
            direction = 'cw' if np.sign(t) > 0 else 'ccw'
            radius += (self.buf_val / 2)

            start_angle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
            stop_angle = np.arctan2(p3[1] - center[1], p3[0] - center[0])

            new_geo_el['solid'] = Polygon(arc(center, radius, start_angle, stop_angle, direction, gerber_steps(self.draw_app.app, radius)))
            new_geo_el['follow'] = Polygon(
                arc(center, radius, start_angle, stop_angle, direction, gerber_steps(self.draw_app.app, radius))).exterior
            self.geometry = DrawToolShape(new_geo_el)

        else:  # self.mode == '12c'
            p1 = np.array(self.points[0])
            p2 = np.array(self.points[1])
            pc = np.array(self.points[2])

            # Midpoint
            a = (p1 + p2) / 2.0

            # Parallel vector
            c = p2 - p1

            # Perpendicular vector
            b = np.dot(c, np.array([[0, -1], [1, 0]], dtype=np.float32))
            b /= numpy_norm(b)

            # Distance
            t = distance(pc, a)

            # Which side? Cross product with c.
            # cross(M-A, B-A), where line is AB and M is a test point.
            side = (pc[0] - p1[0]) * c[1] - (pc[1] - p1[1]) * c[0]
            t *= np.sign(side)

            # Center = a + bt
            center = a + b * t

            radius = numpy_norm(center - p1) + (self.buf_val / 2)
            start_angle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
            stop_angle = np.arctan2(p2[1] - center[1], p2[0] - center[0])

            new_geo_el['solid'] = Polygon(
                arc(center, radius, start_angle, stop_angle, self.direction, gerber_steps(self.draw_app.app, radius)))
            new_geo_el['follow'] = Polygon(
                arc(center, radius, start_angle, stop_angle, self.direction, gerber_steps(self.draw_app.app, radius))).exterior
            self.geometry = DrawToolShape(new_geo_el)

        self.draw_app.in_action = False
        self.complete = True

        self.draw_app.app.jump_signal.disconnect()

        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class ArcEditorGrb(DiscSemiEditorGrb):
    """The existing three arc interactions, swept by a plain circular aperture."""

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'arc'
        self.direction, self.mode = 'cw', 'c12'
        self.buf_val = 0.0  # FOLLOW is the centerline, not the semidisc's material boundary.
        self.dont_execute = False
        self.aperture_id = draw_app.last_aperture_selected
        self.session = draw_app.history.session
        self.revision = draw_app.geometry_revision
        self.units = draw_app.app.app_units
        self._jump_slot = None
        try:
            aperture = draw_app.storage_dict[self.aperture_id]
            self.width = circular_aperture(aperture)
            self.aperture_metadata = metadata(aperture)
        except (KeyError, ValueError, TypeError, OverflowError) as error:
            self.dont_execute = self.complete = True
            draw_app.app.inform.emit('[WARNING_NOTCL] %s' % str(error))
            return
        QtGui.QGuiApplication.restoreOverrideCursor()
        self.cursor = QtGui.QCursor(QtGui.QPixmap(draw_app.app.resource_location + '/aero_arc.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)
        self._jump_slot = lambda *args: draw_app.update_utility_geometry(data=args[0]) if args else None
        draw_app.app.jump_signal.connect(self._jump_slot)
        draw_app.app.inform.emit(_("Click on Center point ..."))

    def utility_geometry(self, data=None):
        if self.dont_execute or data is None:
            return
        points = self.points + [data]
        if not all(len(point) == 2 and all(math.isfinite(v) for v in point) for point in points):
            return
        if len(self.points) == 2:
            p0, p1, p2 = points
            if p0 == p1 or (self.mode == 'c12' and p0 == p2):
                return
            if self.mode == '132' and abs((p1[0] - p0[0]) * (p2[1] - p0[1]) -
                                           (p1[1] - p0[1]) * (p2[0] - p0[0])) <= 1e-12:
                return
        try:
            preview = super().utility_geometry(data)
            if len(self.points) == 2 and preview is not None:
                follow = preview.geo[0]['solid']
                # Match the native Gerber parser: the radius is half the width, the circle steps follow the preferences.
                solid = follow.buffer(self.width / 2.0, quad_segs=gerber_quad(self.draw_app.app, self.width / 2.0))
                if follow.length <= 0 or not follow.is_valid or solid.is_empty or not solid.is_valid:
                    return
                return DrawToolUtilityShape({'solid': solid, 'follow': follow})
            return preview
        except (ValueError, TypeError, ZeroDivisionError, OverflowError):
            return

    def make(self):
        editor = self.draw_app
        if self.dont_execute or len(self.points) != 3:
            return
        if (self.session != editor.history.session or self.revision != editor.geometry_revision or
                self.units != editor.app.app_units or
                self.aperture_id != editor.last_aperture_selected or
                self.aperture_id not in editor.storage_dict or
                metadata(editor.storage_dict[self.aperture_id]) != self.aperture_metadata or
                not editor.history.ready()):
            self.dont_execute = self.complete = True
            self.clean_up()
            editor.app.inform.emit('[WARNING_NOTCL] %s' % _("Editor state changed. Reopen the arc tool."))
            return
        last = self.points.pop()
        try:
            preview = self.utility_geometry(last)
        finally:
            self.points.append(last)
        if preview is None:
            self.points.pop()
            editor.app.inform.emit('[WARNING_NOTCL] %s' % _("Choose distinct, non-collinear arc points."))
            return
        self.geometry = DrawToolShape(preview.geo)
        editor.current_storage = editor.storage_dict[self.aperture_id]['geometry']
        editor.in_action = False
        self.complete = True
        self.clean_up()
        editor.app.inform.emit('[success] %s' % _("Done."))

    def clean_up(self):
        if self._jump_slot is not None:
            try:
                self.draw_app.app.jump_signal.disconnect(self._jump_slot)
            except (TypeError, AttributeError):
                pass
            self._jump_slot = None
        self.draw_app.delete_utility_geometry()


class ScaleEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        ShapeToolEditorGrb.__init__(self, draw_app)
        self.name = 'scale'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Scale the selected Gerber apertures ..."))
        self.origin = (0, 0)

        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])
        self.activate_scale()

    def activate_scale(self):
        self.draw_app.hide_tool('all')
        self.draw_app.ui.scale_tool_frame.show()

        # a previous Scale tool left active must not keep its connection
        previous = self.draw_app.active_tool
        if isinstance(previous, ScaleEditorGrb) and previous is not self:
            previous.disconnect_scale()
        self.disconnect_scale()
        self.draw_app.ui.scale_button.clicked.connect(self.on_scale_click)

    def disconnect_scale(self):
        try:
            self.draw_app.ui.scale_button.clicked.disconnect(self.on_scale_click)
        except (TypeError, RuntimeError, AttributeError):
            pass

    def deactivate_scale(self):
        self.disconnect_scale()
        self.complete = True
        self.draw_app.select_tool("select")
        self.draw_app.hide_tool(self.name)

    def on_scale_click(self, *signal_args):
        self.draw_app.on_scale(on_commit=self.deactivate_scale)

    def clean_up(self):
        self.disconnect_scale()
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()


class BufferEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        ShapeToolEditorGrb.__init__(self, draw_app)
        self.name = 'buffer'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Buffer the selected apertures ..."))
        self.origin = (0, 0)

        self.buff_tool = BufferEditorTool(self.app, self.draw_app)
        self.buff_tool.run()
        self.app.ui.notebook.setTabText(2, _("Buffer"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])
        self.activate()

    def activate(self):
        try:
            self.buff_tool.ui.buffer_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        self.buff_tool.ui.buffer_button.clicked.connect(self.on_buffer_clicked)

    def on_buffer_clicked(self):
        self.buff_tool.on_buffer(on_commit=self.deactivate)

    def deactivate(self):
        try:
            self.buff_tool.ui.buffer_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        self.complete = True
        self.draw_app.select_tool("select")
        self.draw_app.hide_tool(self.name)

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()


class SimplifyEditorGrb(ShapeToolEditorGrb):
    # the click picks a shape: it uses the raw (not grid snapped) position
    uses_raw_pick = True

    def __init__(self, draw_app):
        ShapeToolEditorGrb.__init__(self, draw_app)
        self.name = 'simplify'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Simplify the selected apertures ..."))
        self.origin = (0, 0)

        self.simp_tool = SimplificationTool(self.app, self.draw_app)
        self.simp_tool.run()
        self.ui = self.simp_tool.ui

        self.app.ui.notebook.setTabText(2, _("Simplification"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])
        self.activate()

    def activate(self):
        try:
            self.draw_app.ui.simplification_btn.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        self.ui.simplification_btn.clicked.connect(self.on_simplify_click)

    def deactivate(self):
        self.draw_app.ui.simplification_btn.clicked.disconnect()
        self.complete = True
        self.draw_app.select_tool("select")
        self.draw_app.hide_tool(self.name)

    def on_simplify_click(self):
        self.on_simplification_click()
        # self.deactivate()

    def set_origin(self, origin):
        self.origin = origin

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
                )):
            pass
        else:
            self.draw_app.selected = []

    def click_release(self, point):
        self.draw_app.sync_aperture_rows(())
        key_modifier = QtWidgets.QApplication.keyboardModifiers()

        if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
            mod_key = 'Shift'
        elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
            mod_key = 'Control'
        else:
            mod_key = None

        if mod_key != self.draw_app.app.options.get(
                "global_mselect_key",
                self.draw_app.app.defaults.get(
                    "global_mselect_key",
                    self.draw_app.app.defaults.factory_defaults.get("global_mselect_key")
                )):
            self.draw_app.selected.clear()

        hit = self.draw_app.pick_at(point)
        if hit is not None:
            shape_stored = hit[0]
            if shape_stored in self.draw_app.selected:
                self.draw_app.selected.remove(shape_stored)
            else:
                self.draw_app.selected.append(shape_stored)

        self.draw_app.plot_all()
        self.simp_tool.calculate_coords_vertex()

    def on_simplification_click(self):
        self.app.log.debug("SimplifyEditorGrb.on_simplification_click()")
        if not self.draw_app.history.ready():
            return 'fail'

        try:
            tol = float(self.ui.geo_tol_entry.get_value())
            if not math.isfinite(tol) or tol < 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Tolerance must be finite and non-negative."))
            return

        selected = set(self.draw_app.selected)
        storage_dict = self.draw_app.storage_dict
        revision = self.draw_app.geometry_revision
        snapshots = []
        geometries = {}
        for apid, aperture in storage_dict.items():
            storage = aperture.get('geometry', [])
            shapes = tuple(storage)
            owned = [shape for shape in shapes if shape in selected]
            if owned:
                snapshots.append((apid, storage, shapes))
                for shape in owned:
                    geometries[shape] = dict(shape.geo)
        if not geometries:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No aperture geometry is selected."))
            return

        pending = self.draw_app.history.reserve()
        if pending is None:
            return 'fail'

        def task_job():
            with self.app.proc_container.new('%s...' % _("Simplify")):
                result = {'revision': revision, 'storage': storage_dict, 'updates': [], 'replacements': {},
                          'pending': pending}
                try:
                    replacements = result['replacements']
                    for shape, geometry in geometries.items():
                        new_geo = dict(geometry)
                        for key in ('solid', 'follow', 'clear'):
                            if geometry.get(key) is not None:
                                new_geo[key] = geometry[key].simplify(tolerance=tol)
                        replacements[shape] = DrawToolShape(new_geo)
                    for apid, storage, shapes in snapshots:
                        result['updates'].append((apid, storage, shapes,
                                                  [replacements.get(shape, shape) for shape in shapes]))
                except Exception as err:
                    # No storage has changed: a failed component must never delete its source shape.
                    result['error'] = str(err)
                self.draw_app.simplification_finished.emit(result)

        self.app.worker_task.emit({'fcn': task_job, 'params': []})

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()


class MarkEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        ShapeToolEditorGrb.__init__(self, draw_app)
        self.name = 'markarea'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Mark polygon areas in the edited Gerber ..."))
        self.origin = (0, 0)

        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])
        self.activate_markarea()

    def activate_markarea(self):
        self.draw_app.ui.ma_tool_frame.show()

        # clear previous marking (the labels and the shapes that Delete would remove)
        self.draw_app.ma_annotation.clear(update=True)
        self.draw_app.geo_to_delete = []

        try:
            self.draw_app.ui.ma_threshold_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.ui.ma_threshold_button.clicked.connect(self.on_markarea_click)

        try:
            self.draw_app.ui.ma_delete_button.clicked.disconnect()
        except TypeError:
            pass
        self.draw_app.ui.ma_delete_button.clicked.connect(self.on_markarea_delete)

        try:
            self.draw_app.ui.ma_clear_button.clicked.disconnect()
        except TypeError:
            pass
        self.draw_app.ui.ma_clear_button.clicked.connect(self.on_markarea_clear)

    def deactivate_markarea(self):
        self.draw_app.ui.ma_threshold_button.clicked.disconnect()
        self.complete = True
        self.draw_app.select_tool("select")
        self.draw_app.hide_tool(self.name)

    def on_markarea_click(self):
        self.draw_app.on_markarea()

    def on_markarea_clear(self):
        self.draw_app.ma_annotation.clear(update=True)
        # the shapes are no longer marked, so a later Delete must not remove them
        self.draw_app.geo_to_delete = []
        self.deactivate_markarea()

    def on_markarea_delete(self):
        self.draw_app.delete_marked_polygons()
        self.on_markarea_clear()

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()


class MoveEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'move'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.origin = None
        self.destination = None
        self.selected_apertures = []

        if len(self.draw_app.get_selected()) == 0:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s...' %
                                          _("Nothing selected to move"))
            self.complete = True
            self.draw_app.select_tool("select")
            return

        if self.draw_app.launched_from_shortcuts is True:
            self.draw_app.launched_from_shortcuts = False
            self.draw_app.app.inform.emit(_("Click on target location ..."))
        else:
            self.draw_app.app.inform.emit(_("Click on reference location ..."))

        self.current_storage = None
        self.geometry = []

        for index in self.draw_app.ui.apertures_table.selectedIndexes():
            row = index.row()
            # on column 1 in tool tables we hold the aperture codes, and we retrieve them as strings
            aperture_on_row = int(self.draw_app.ui.apertures_table.item(row, 1).text())
            self.selected_apertures.append(aperture_on_row)

        # Switch notebook to Properties page
        self.draw_app.app.ui.notebook.setCurrentWidget(self.draw_app.app.ui.properties_tab)

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.sel_limit = self.draw_app.app.options.get(
            "gerber_editor_sel_limit",
            self.draw_app.app.defaults.get(
                "gerber_editor_sel_limit",
                self.draw_app.app.defaults.factory_defaults.get("gerber_editor_sel_limit")
            )
        )
        self.selection_shape = self.selection_bbox()

    def set_origin(self, origin):
        self.origin = origin

    def click(self, point):
        if len(self.draw_app.get_selected()) == 0:
            return "Nothing to move."

        if self.origin is None:
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click on target location ..."))
            return
        else:
            self.destination = point
            self.make()

            # MS: always return to the Select Tool
            self.draw_app.select_tool("select")
            return

    # def create_png(self):
    #     """
    #     Create a PNG file out of a list of Shapely polygons
    #     :return:
    #     """
    #     if len(self.draw_app.get_selected()) == 0:
    #         return None
    #
    #     geo_list = [geoms.geo for geoms in self.draw_app.get_selected()]
    #     xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_list)
    #
    #     iwidth = (xmax - xmin)
    #     iwidth = int(round(iwidth))
    #     iheight = (ymax - ymin)
    #     iheight = int(round(iheight))
    #     c = pngcanvas.PNGCanvas(iwidth, iheight)
    #
    #     pixels = []
    #     for geom in self.draw_app.get_selected():
    #         m = mapping(geom.geo.exterior)
    #         pixels += [[coord[0], coord[1]] for coord in m['coordinates']]
    #         for g in geom.geo.interiors:
    #             m = mapping(g)
    #             pixels += [[coord[0], coord[1]] for coord in m['coordinates']]
    #         c.polyline(pixels)
    #         pixels = []
    #
    #     f = open("%s.png" % 'D:\\shapely_image', "wb")
    #     f.write(c.dump())
    #     f.close()

    def selection_bbox(self):
        geo_list = []

        for select_shape in self.draw_app.get_selected():
            geometric_data = select_shape.geo
            geo_list.append(geometric_data['solid'])

        xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_list)

        pt1 = (xmin, ymin)
        pt2 = (xmax, ymin)
        pt3 = (xmax, ymax)
        pt4 = (xmin, ymax)

        return Polygon([pt1, pt2, pt3, pt4])

    def make(self):
        self.transfer_selection(remove_source=True)

    @staticmethod
    def translated_elements(shapes, dx, dy):
        """
        Returns the geometry dicts of the shapes translated by (dx, dy). Each of the 'solid', 'follow' and 'clear'
        keys is translated for all the shapes in 1 vectorized batch.

        :param shapes:  list of DrawToolShape
        :param dx:      offset on X
        :param dy:      offset on Y
        :return:        list of dicts, in the order of the shapes
        """
        new_elements = [{} for __ in shapes]
        for key in ('solid', 'follow', 'clear'):
            positions = [pos for pos, shape in enumerate(shapes) if key in shape.geo]
            if not positions:
                continue
            translated = translate_geometry([shapes[pos].geo[key] for pos in positions], dx, dy)
            for pos, geo in zip(positions, translated):
                new_elements[pos][key] = geo
        return new_elements

    @gerber_operation(_('Move / Copy'))
    def transfer_selection(self, remove_source):
        """
        Adds a translated copy of each selected shape to its aperture storage. Each storage is changed in 1 pass and
        the shapes are plotted 1 time at the end.

        :param remove_source:   True: the selected shapes are removed (Move); False: they stay (Copy)
        :return:                None
        """
        dx = self.destination[0] - self.origin[0]
        dy = self.destination[1] - self.origin[1]

        if dx != 0 or dy != 0 or not remove_source:
            selected = self.draw_app.get_selected()
            done_ids = set()
            for sel_dia in self.selected_apertures:
                storage = self.draw_app.storage_dict[sel_dia]['geometry']
                storage_ids = {id(shape) for shape in storage}
                shapes = [shape for shape in selected if id(shape) in storage_ids and id(shape) not in done_ids]
                if not shapes:
                    continue
                shape_ids = {id(shape) for shape in shapes}

                if remove_source:
                    # slice assignment: storage_dict keeps a reference to this list
                    storage[:] = [shape for shape in storage if id(shape) not in shape_ids]
                moved = [DrawToolShape(geo_el) for geo_el in self.translated_elements(shapes, dx, dy)]
                storage.extend(moved)
                # a pure translation keeps the flash semantics of the records: no export re-validation
                history = getattr(self.draw_app, 'history', None)
                if isinstance(history, GerberHistory):
                    history.translated_shapes.update(moved)
                done_ids |= shape_ids

            if done_ids:
                self.draw_app.selected[:] = [shape for shape in self.draw_app.selected if id(shape) not in done_ids]
                self.draw_app.touch_geometry()

        self.draw_app.delete_utility_geometry()
        self.draw_app.tool_shape.clear(update=True)
        self.draw_app.plot_all()
        self.draw_app.build_ui()
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        self.draw_app.app.jump_signal.disconnect()

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """
        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        if len(self.draw_app.get_selected()) <= self.sel_limit:
            # the translated geometry is new, so it needs no deepcopy
            return DrawToolUtilityShape(self.translated_elements(self.draw_app.get_selected(), dx, dy))
        else:
            ss_el = {'solid': translate(self.selection_shape, xoff=dx, yoff=dy)}
            return DrawToolUtilityShape(ss_el)


class CopyEditorGrb(MoveEditorGrb):
    def __init__(self, draw_app):
        MoveEditorGrb.__init__(self, draw_app)
        self.name = 'copy'

    def make(self):
        self.transfer_selection(remove_source=False)


class EraserEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'eraser'

        self.origin = None
        self.destination = None
        self.selected_apertures = []

        if len(self.draw_app.get_selected()) == 0:
            if self.draw_app.launched_from_shortcuts is True:
                self.draw_app.launched_from_shortcuts = False
                self.draw_app.app.inform.emit(_("Select a shape to act as deletion area ..."))
        else:
            self.draw_app.app.inform.emit(_("Click to pick-up the erase shape..."))

        self.current_storage = None
        self.geometry = []

        for index in self.draw_app.ui.apertures_table.selectedIndexes():
            row = index.row()
            # on column 1 in tool tables we hold the aperture codes, and we retrieve them as strings
            aperture_on_row = self.draw_app.ui.apertures_table.item(row, 1).text()
            self.selected_apertures.append(aperture_on_row)

        # Switch notebook to Properties page
        self.draw_app.app.ui.notebook.setCurrentWidget(self.draw_app.app.ui.properties_tab)

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.sel_limit = self.draw_app.app.options.get(
            "gerber_editor_sel_limit",
            self.draw_app.app.defaults.get(
                "gerber_editor_sel_limit",
                self.draw_app.app.defaults.factory_defaults.get("gerber_editor_sel_limit")
            )
        )

    def set_origin(self, origin):
        self.origin = origin

    def wants_raw_pick(self):
        # the first click picks the eraser shape: raw position; the click that places the eraser is snapped
        return len(self.draw_app.get_selected()) == 0

    def click(self, point):
        if len(self.draw_app.get_selected()) == 0:
            hit = self.draw_app.pick_at(point)
            if hit is not None:
                shape, aperture = hit
                self.draw_app.selected = [shape]
                self.draw_app.sync_aperture_rows({aperture})
            else:
                self.draw_app.sync_aperture_rows(())

        if len(self.draw_app.get_selected()) == 0:
            return _("Nothing to erase.")

        if self.origin is None:
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click to erase ..."))
            return
        else:
            self.destination = point
            self.make()

            # self.draw_app.select_tool("select")
            return

    @gerber_operation(_('Erase'), all_apertures=True)
    def make(self):
        eraser_sel_shapes = []

        # create the eraser shape from selection
        for eraser_shape in self.utility_geometry(data=self.destination).geo:
            temp_shape = eraser_shape['solid'].buffer(0.0000001)
            temp_shape = Polygon(temp_shape.exterior)
            eraser_sel_shapes.append(temp_shape)
        eraser_sel_shapes = unary_union(eraser_sel_shapes)

        # all shapes that are `cut` will be stored in the 0 aperture, after the loop (so the loop never sees them)
        intersection_geo_list = []
        # if at least one of the apertures have zero geometry left then we delete it, so we need to rebuild UI
        should_build = False
        # populate intersection list
        for storage in list(self.draw_app.storage_dict.keys()):
            to_delete = []
            try:
                for idx, geo_el in enumerate(self.draw_app.storage_dict[storage]['geometry']):
                    if 'solid' not in geo_el.geo:
                        continue
                    solid_data = geo_el.geo['solid']
                    if not eraser_sel_shapes.intersects(solid_data):
                        continue

                    # only the shapes touched by the eraser are cut
                    to_delete.append(idx)
                    geos = solid_data.difference(eraser_sel_shapes).buffer(0)
                    if geos.is_empty:
                        continue

                    new_geo_el = {'solid': deepcopy(geos)}
                    follow_data = geo_el.geo.get('follow')
                    if follow_data is not None:
                        if eraser_sel_shapes.intersects(follow_data):
                            follow_data = follow_data.difference(eraser_sel_shapes)
                        new_geo_el['follow'] = deepcopy(follow_data)
                    intersection_geo_list.append(DrawToolShape(new_geo_el))
            except KeyError:
                pass

            if len(to_delete) == len(self.draw_app.storage_dict[storage]['geometry']):
                self.draw_app.storage_dict.pop(storage, None)
                should_build = True
            else:
                # delete empty geometries
                for td in to_delete[::-1]:
                    self.draw_app.storage_dict[storage]['geometry'].pop(td)

        if intersection_geo_list:
            if 0 not in self.draw_app.storage_dict.keys():
                self.draw_app.storage_dict[0] = {
                    'type': 'REG',
                    'size': 0.0,
                    'geometry': intersection_geo_list
                }
            else:
                self.draw_app.storage_dict[0]['geometry'] += intersection_geo_list
        self.draw_app.touch_geometry()

        if should_build:
            self.draw_app.build_ui()
        self.draw_app.delete_utility_geometry()
        self.draw_app.plot_all()
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        try:
            self.draw_app.app.jump_signal.disconnect()
        except TypeError:
            pass

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.ui.apertures_table.clearSelection()
        self.draw_app.plot_all()
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

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

        for geom in self.draw_app.get_selected():
            new_geo_el = {}
            if 'solid' in geom.geo:
                new_geo_el['solid'] = translate(geom.geo['solid'], xoff=dx, yoff=dy)
            if 'follow' in geom.geo:
                new_geo_el['follow'] = translate(geom.geo['follow'], xoff=dx, yoff=dy)
            if 'clear' in geom.geo:
                new_geo_el['clear'] = translate(geom.geo['clear'], xoff=dx, yoff=dy)
            geo_list.append(deepcopy(new_geo_el))
        return DrawToolUtilityShape(geo_list)


class SelectEditorGrb(QtCore.QObject, DrawTool):
    # the click picks a shape: it uses the raw (not grid snapped) position
    uses_raw_pick = True

    def __init__(self, draw_app):
        super().__init__(draw_app=draw_app)
        # DrawTool.__init__(self, draw_app)
        self.name = 'select'
        self.origin = None

        self.draw_app = draw_app
        self.storage = self.draw_app.storage_dict
        # self.selected = self.draw_app.selected

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.SelectEditorGrb --> %s" % str(e))

        # here we store all shapes that were selected
        self.sel_storage = []

        # since SelectEditorGrb tool is activated whenever a tool is exited I place here the reinitialization of the
        # bending modes using in RegionEditorGrb and TrackEditorGrb
        self.draw_app.bend_mode = 1

        # here store the selected apertures
        self.sel_aperture = set()

        # multiprocessing results
        self.results = []

        try:
            self.draw_app.ui.apertures_table.clearSelection()
        except Exception as e:
            log.error("FlatCAMGerbEditor.SelectEditorGrb.__init__() --> %s" % str(e))

        try:
            self.draw_app.plot_object.disconnect()
        except (TypeError, AttributeError):
            pass

        # if the shapes are not visible make them visible
        if self.draw_app.visible is False:
            self.draw_app.visible = True

        # make sure that the cursor text from the FCPath is deleted
        if self.draw_app.app.plotcanvas.text_cursor.parent:
            self.draw_app.app.plotcanvas.text_cursor.parent = None
            self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        # make sure that the Tools tab is removed
        try:
            self.draw_app.app.ui.notebook.removeTab(2)
        except Exception:
            pass

        self.complete = True

    def set_origin(self, origin):
        self.origin = origin

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
                )):
            pass
        else:
            self.draw_app.selected = []

    def click_release(self, point):
        key_modifier = QtWidgets.QApplication.keyboardModifiers()

        if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
            mod_key = 'Shift'
        elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
            mod_key = 'Control'
        else:
            mod_key = None

        if mod_key != self.draw_app.app.options.get(
                "global_mselect_key",
                self.draw_app.app.defaults.get(
                    "global_mselect_key",
                    self.draw_app.app.defaults.factory_defaults.get("global_mselect_key")
                )):
            self.draw_app.selected.clear()
            self.sel_aperture.clear()

        hit, previous, position, count = self.draw_app.pick_cycle_at(point)
        if hit is not None:
            shape_stored = hit[0]
            if previous is not None:
                # a repeated click at the same place: the next shape under the cursor replaces the last pick
                if previous in self.draw_app.selected:
                    self.draw_app.selected.remove(previous)
                if shape_stored not in self.draw_app.selected:
                    self.draw_app.selected.append(shape_stored)
            elif shape_stored in self.draw_app.selected:
                self.draw_app.selected.remove(shape_stored)
            else:
                # add the object to the selected shapes
                self.draw_app.selected.append(shape_stored)

            if count > 1:
                self.draw_app.app.inform.emit('%s %d / %d. %s' % (
                    _("Shape"), position + 1, count, _("Click again at the same place to select the next one.")))

        self.draw_app.update_ui()
        self.after_selection()

    def after_selection(self):
        # ######################################################################################################
        # select the aperture in the Apertures Table that is associated with the selected shape
        # ######################################################################################################
        self.sel_aperture.clear()

        self.draw_app.get_pick_index()
        for shape_s in self.draw_app.selected:
            self.sel_aperture.update(self.draw_app._shape_apertures.get(shape_s, ()))

        self.draw_app.sync_aperture_rows(self.sel_aperture)

        self.draw_app.plot_selection()

    def clean_up(self):
        self.draw_app.plot_all()


class ImportEditorGrb(QtCore.QObject, DrawTool):
    import_signal = QtCore.pyqtSignal()

    def __init__(self, draw_app):
        super().__init__(draw_app=draw_app)
        # DrawTool.__init__(self, draw_app)
        self.name = 'import'
        self.origin = None
        self._canvas_connected = False

        self.draw_app = draw_app
        self.storage = self.draw_app.storage_dict
        # self.selected = self.draw_app.selected

        self.event_is_dragging = False

        # here we store all shapes that were selected; each item in the list is a dict
        '''
        {
            apid: {
                'type': '',
                'size': 0.0,
                'width': 0.0,
                'height': 0.0,
                'geometry': [],
                'shape_id': 0
            }
        }
        '''
        self.sel_storage = []

        # since SelectEditorGrb tool is activated whenever a tool is exited I place here the reinitialization of the
        # bending modes using in RegionEditorGrb and TrackEditorGrb
        self.draw_app.bend_mode = 1

        # here store the selected apertures
        self.sel_aperture = set()

        # multiprocessing results
        self.results = []

        self.app = self.draw_app.app
        self.canvas = self.draw_app.canvas

        self.x = None
        self.y = None
        self.pos = None
        self.snap_x = None
        self.snap_y = None

        try:
            self.draw_app.ui.apertures_table.clearSelection()
        except Exception as e:
            log.error("FlatCAMGerbEditor.ImportEditorGrb.__init__() --> %s" % str(e))

        self.draw_app.hide_tool('all')
        self.draw_app.ui.array_frame.hide()

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            log.error("AppGerberEditor.ImportEditorGrb --> %s" % str(e))

        try:
            self.import_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.import_signal.connect(self.import_shapes)

        try:
            self.draw_app.plot_object.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.plot_object.connect(self.plot_import)

        self.import_connect()

        self.draw_app.visible = False

        # disengage the grid snapping since it may be hard to click on polygons with grid snapping on
        if self.app.ui.grid_snap_btn.isChecked():
            self.grid_status_memory = True
            self.app.ui.grid_snap_btn.trigger()
        else:
            self.grid_status_memory = False

        self.draw_app.app.inform.emit('%s' % _("Select shapes to import them into the edited object."))

    def import_connect(self):
        if self._canvas_connected:
            return
        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.canvas.graph_event_connect('mouse_press', self.on_mouse_click)
        self.canvas.graph_event_connect('mouse_move', self.on_mouse_move)
        self.canvas.graph_event_connect('mouse_release', self.on_mouse_click_release)

        # disconnect old
        self.canvas.graph_event_disconnect('mouse_press', self.draw_app.on_canvas_click)
        self.canvas.graph_event_disconnect('mouse_move', self.draw_app.on_canvas_move)
        self.canvas.graph_event_disconnect('mouse_release', self.draw_app.on_canvas_click_release)
        self._canvas_connected = True

    def import_disconnect(self):
        if not self._canvas_connected:
            return
        self.canvas.graph_event_connect('mouse_press', self.draw_app.on_canvas_click)
        self.canvas.graph_event_connect('mouse_move', self.draw_app.on_canvas_move)
        self.canvas.graph_event_connect('mouse_release', self.draw_app.on_canvas_click_release)

        self.canvas.graph_event_disconnect('mouse_press', self.on_mouse_click)
        self.canvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
        self.canvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)
        self._canvas_connected = False

    def on_mouse_click(self, event):
        event_pos = event.pos

        # update click position (used also in self.on_mouse_move() )
        self.pos = self.canvas.translate_coords(event_pos)
        if self.app.grid_status():
            self.pos = self.app.geo_editor.snap(self.pos[0], self.pos[1])
        else:
            self.pos = (self.pos[0], self.pos[1])

        if event.button == 1:
            self.app.ui.update_location_labels(0, 0, *self.pos)

    def on_mouse_move(self, event):
        if not self.app.plotcanvas.native.hasFocus():
            self.app.plotcanvas.native.setFocus()

        event_pos = event.pos
        self.event_is_dragging = event.is_dragging
        right_button = 2

        pos_canvas = self.canvas.translate_coords(event_pos)
        self.x, self.y = pos_canvas[0], pos_canvas[1]

        self.app.ui.popMenu.mouse_is_panning = False

        # if the RMB is clicked and mouse is moving over plot then 'panning_action' is True
        if event.button == right_button and self.event_is_dragging == 1:
            self.app.ui.popMenu.mouse_is_panning = True
            return

        try:
            x = float(self.x)
            y = float(self.y)
        except TypeError:
            return

        # Snap coordinates
        if self.app.grid_status():
            x, y = self.app.geo_editor.snap(x, y)

            # Update cursor
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

        self.app.mouse_pos = [x, y]

        if self.pos is None:
            self.pos = (0, 0)
        self.app.dx = x - self.pos[0]
        self.app.dy = y - self.pos[1]

        # # update the position label in the infobar since the APP mouse event handlers are disconnected
        # self.app.ui.position_label.setText("&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
        #                                    "<b>Y</b>: %.4f&nbsp;" % (x, y))
        #
        # # update the reference position label in the infobar since the APP mouse event handlers are disconnected
        # self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
        #                                        "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (self.app.dx, self.app.dy))
        self.app.ui.update_location_labels(self.app.dx, self.app.dy, x, y)

        # Selection area on canvas section # ##
        if self.event_is_dragging == 1 and event.button == 1:
            dx = pos_canvas[0] - self.pos[0]
            if dx < 0:
                self.app.draw_moving_selection_shape((self.pos[0], self.pos[1]), (x, y),
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
                self.app.draw_moving_selection_shape((self.pos[0], self.pos[1]), (x, y))
                self.app.selection_type = True
        else:
            self.app.selection_type = None

    def on_mouse_click_release(self, event):
        left_button = 1
        right_button = 2

        event_pos = event.pos
        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        event_pos = (x, y)
        curr_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])
        else:
            curr_pos = (curr_pos[0], curr_pos[1])

        try:
            if event.button == left_button:
                if self.app.selection_type is not None:
                    self.draw_app.app.delete_selection_shape()
                    self.selection_area_handler(self.pos, curr_pos, self.app.selection_type)
                    self.app.selection_type = None
                else:
                    self.select_handler(curr_pos)
            elif event.button == right_button:  # right click
                if self.event_is_dragging is False:
                    # restore the Grid snapping if it was active before
                    if self.grid_status_memory is True:
                        self.app.ui.grid_snap_btn.trigger()
                        self.grid_status_memory = False

                    self.import_disconnect()

                    # disconnect flags
                    self.app.tool_shapes.clear(update=True)

                    self.import_signal.emit()

        except Exception as e:
            self.app.log.error("ImportEditorGrb.on_mouse_click_release() RMB click --> Error: %s" % str(e))
            raise

    def get_selected_geos(self):
        sel_geos = []
        for ap_dict in self.sel_storage:
            for geo_el in ap_dict['geometry']:
                if 'solid' in geo_el.geo:
                    sel_geos.append(geo_el.geo['solid'])

        return sel_geos

    def get_selected_shape_id(self, geo):
        idx = 0
        for ap_dict in self.sel_storage:
            for geo_el in ap_dict['geometry']:
                if 'solid' in geo_el.geo:
                    if geo_el.geo['solid'].equals(geo):
                        return ap_dict['shape_id'], idx
            idx += 1

    def import_candidates(self, query):
        """Index composed donor material for this tool; immutable geometry identities detect source edits."""
        cache = getattr(self, '_source_candidates', None)
        if not isinstance(cache, dict):
            cache = self._source_candidates = {}
        sources = [obj for obj in self.app.collection.get_list()
                   if obj.kind in ('geometry', 'gerber', 'excellon') and obj.obj_options.get('plot') is True
                   and obj is not self.draw_app.gerber_obj]
        live = {id(obj) for obj in sources}
        for key in set(cache) - live:
            del cache[key]
        for obj in sources:
            geometry = obj.solid_geometry
            if obj.kind == 'geometry' and getattr(obj, 'multigeo', False):
                geometry = [tool.get('solid_geometry', []) for tool in obj.tools.values()]
            raw = tuple(flatten_shapely_geometry(geometry if geometry is not None else []))
            token = tuple(id(geo) for geo in raw)
            entry = cache.get(id(obj))
            if entry is None or entry[1] != token:
                polygons = []
                for geo in raw:
                    if isinstance(geo, (LineString, LinearRing)) and geo.is_ring:
                        geo = Polygon(geo)
                    if isinstance(geo, Polygon) and not geo.is_empty and geo.is_valid:
                        polygons.append(geo)
                entry = (obj, token, raw, polygons, STRtree(polygons))
                cache[id(obj)] = entry
            polygons, tree = entry[3:]
            records = [{'solid': polygons[int(index)], 'follow': polygons[int(index)].exterior}
                       for index in sorted(tree.query(query, predicate='intersects'))]
            if records:
                yield obj, {0: {'type': 'REG', 'size': 0.0, 'geometry': records}}

    def select_handler(self, pos):
        """

        :param pos: mouse click position
        :type pos:  tuple
        :return:    None
        :rtype:     None
        """
        added_poly_count = 0

        for obj, edit_tools in ImportEditorGrb.import_candidates(self, Point(pos)):
            if edit_tools:
                for apid in edit_tools:
                    if 'geometry' in edit_tools[apid]:
                        for geo_el in edit_tools[apid]['geometry']:
                            if 'solid' in geo_el:
                                solid_geo = geo_el['solid']
                                if Point(pos).within(solid_geo):
                                    if solid_geo not in self.get_selected_geos():
                                        o_color = self.draw_app.get_sel_color() + 'AF'
                                        f_color = self.draw_app.get_sel_color() + 'AF'
                                        shape_id = self.app.tool_shapes.add(tolerance=obj.drawing_tolerance, layer=0,
                                                                            shape=solid_geo,
                                                                            color=o_color,
                                                                            face_color=f_color,
                                                                            visible=True)
                                        new_ap_dict = {k: v for k, v in edit_tools[apid].items() if k != 'geometry'}
                                        new_ap_dict['geometry'] = [DrawToolShape(deepcopy(geo_el))]
                                        new_ap_dict['shape_id'] = shape_id
                                        self.sel_storage.append(new_ap_dict)
                                        added_poly_count += 1
                                    else:
                                        solid_geo_shape_id, solid_geo_apdict_idx = self.get_selected_shape_id(solid_geo)
                                        self.app.tool_shapes.remove(solid_geo_shape_id)
                                        self.sel_storage.pop(solid_geo_apdict_idx)

        self.app.tool_shapes.redraw()

        if added_poly_count > 0:
            self.app.inform.emit(
                '%s: %d. %s' % (_("Added polygon"),
                                int(len(self.sel_storage)),
                                _("Click to add next polygon or right click to start."))
            )
        else:
            self.app.inform.emit(_("No polygon in selection."))

    def selection_area_handler(self, start_pos, end_pos, selection_type):
        """

        :param start_pos:       mouse selection start position
        :type start_pos:        tuple
        :param end_pos:         mouse selection end position
        :type end_pos:          tuple
        :param selection_type:  True if selection is left-to-tight mouse drag, False if right-to-left mouse drag
        :type selection_type:
        :return:                None
        """

        poly_selection = Polygon([start_pos, (end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])

        added_poly_count = 0

        color = self.draw_app.get_sel_color() + 'AF'
        face_color = self.draw_app.get_sel_color() + 'AF'

        for obj, edit_tools in ImportEditorGrb.import_candidates(self, poly_selection):
            if edit_tools:
                for apid in edit_tools:
                    if 'geometry' in edit_tools[apid]:
                        for geo_el in edit_tools[apid]['geometry']:
                            if 'solid' in geo_el:
                                solid_geo = geo_el['solid']
                                if selection_type is True:
                                    if solid_geo.within(poly_selection):
                                        if solid_geo not in self.get_selected_geos():
                                            shape_id = self.app.tool_shapes.add(tolerance=obj.drawing_tolerance,
                                                                                layer=0,
                                                                                shape=solid_geo,
                                                                                color=color,
                                                                                face_color=face_color,
                                                                                visible=True)
                                            new_ap_dict = {
                                                k: v for k, v in edit_tools[apid].items() if k != 'geometry'
                                            }
                                            new_ap_dict['geometry'] = [DrawToolShape(deepcopy(geo_el))]
                                            new_ap_dict['shape_id'] = shape_id
                                            self.sel_storage.append(new_ap_dict)
                                            added_poly_count += 1
                                        else:
                                            solid_geo_shape_id, solid_geo_idx = self.get_selected_shape_id(solid_geo)
                                            self.app.tool_shapes.remove(solid_geo_shape_id)
                                            self.sel_storage.pop(solid_geo_idx)

                                if selection_type is False:
                                    if solid_geo.intersects(poly_selection):
                                        if solid_geo not in self.get_selected_geos():
                                            shape_id = self.app.tool_shapes.add(tolerance=obj.drawing_tolerance,
                                                                                layer=0,
                                                                                shape=solid_geo,
                                                                                color=color,
                                                                                face_color=face_color,
                                                                                visible=True)
                                            new_ap_dict = {
                                                k: v for k, v in edit_tools[apid].items() if k != 'geometry'
                                            }
                                            new_ap_dict['geometry'] = [DrawToolShape(deepcopy(geo_el))]
                                            new_ap_dict['shape_id'] = shape_id
                                            self.sel_storage.append(new_ap_dict)
                                            added_poly_count += 1
                                        else:
                                            solid_geo_shape_id, solid_geo_idx = self.get_selected_shape_id(solid_geo)
                                            self.app.tool_shapes.remove(solid_geo_shape_id)
                                            self.sel_storage.pop(solid_geo_idx)

        self.app.tool_shapes.redraw()

        if added_poly_count > 0:
            self.app.inform.emit(
                '%s: %d. %s' % (_("Added polygon"),
                                int(len(self.sel_storage)),
                                _("Click to add next polygon or right click to start."))
            )
        else:
            self.app.inform.emit(_("No polygon in selection."))

    @gerber_operation(_('Import'), all_apertures=True)
    def import_shapes(self):
        if any(not isinstance(shape.geo.get('solid'), (Polygon, MultiPolygon)) or
               shape.geo['solid'].is_empty or not shape.geo['solid'].is_valid
               for aperture in self.sel_storage for shape in aperture.get('geometry', ())):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Import requires valid polygon material."))
            # leave the import mode: restore the drawing visibility and the Select tool
            self.draw_app.visible = True
            self.draw_app.select_tool('select')
            return 'fail'
        st_dict = self.draw_app.storage_dict

        st_dict_keys = numeric_aperture_codes(st_dict.keys())
        if st_dict_keys:
            max_apid = max(st_dict_keys)
        else:
            max_apid = 0

        new_apid = 10 if max_apid == 0 else max_apid + 1

        for ap_dict in self.sel_storage:
            if ap_dict['type'] == 'REG':
                # Imported islands may precede their enclosing regions in the selection.
                self.draw_app.loaded_as_regions = True
                added_flag = False
                for storage in list(st_dict.keys()):
                    if st_dict[storage]['type'] == 'REG':
                        st_dict[storage]['geometry'] += ap_dict['geometry']
                        added_flag = True
                        break

                if added_flag is False:
                    st_dict[0] = {
                        'type': 'REG',
                        'size': 0.0,
                        'geometry': ap_dict['geometry']
                    }
            elif ap_dict['type'] == 'C':
                added_flag = False
                for storage in list(st_dict.keys()):
                    if st_dict[storage]['type'] == 'C':
                        if st_dict[storage]['size'] == ap_dict['size']:
                            st_dict[storage]['geometry'] += ap_dict['geometry']
                            added_flag = True
                            break

                if added_flag is False:
                    st_dict[new_apid] = {
                        'type': ap_dict['type'],
                        'size': ap_dict['size'],
                        'geometry': ap_dict['geometry']
                    }
                    new_apid += 1
            elif ap_dict['type'] in ['R', 'O']:
                added_flag = False
                for storage in list(st_dict.keys()):
                    if st_dict[storage]['type'] == ap_dict['type']:
                        if st_dict[storage]['width'] == ap_dict['width'] and \
                                st_dict[storage]['height'] == ap_dict['height']:
                            st_dict[storage]['geometry'] += ap_dict['geometry']
                            added_flag = True
                            break

                if added_flag is False:
                    st_dict[new_apid] = {
                        'type': ap_dict['type'],
                        'size': ap_dict['size'],
                        'width': ap_dict['width'],
                        'height': ap_dict['height'],
                        'geometry': ap_dict['geometry']
                    }
                    new_apid += 1
            else:
                st_dict[new_apid] = {}
                for k in ap_dict:
                    if k in ['geometry', 'shape_id']:
                        continue
                    st_dict[new_apid][k] = ap_dict[k]
                st_dict[new_apid]['geometry'] = ap_dict['geometry']
                new_apid += 1

        self.draw_app.touch_geometry()
        self.draw_app.plot_object.emit(None)
        self.draw_app.build_ui_sig.emit()
        self.app.inform.emit('[success] %s' % _("Done."))
        self.draw_app.select_tool('select')

    def plot_import(self):
        self.draw_app.plot_all()

    def clean_up(self):
        self.import_disconnect()
        self._source_candidates = {}
        if self.grid_status_memory:
            self.app.ui.grid_snap_btn.trigger()
            self.grid_status_memory = False
        if self.app.tool_shapes is not None:
            self.app.tool_shapes.clear(update=True)
        self.draw_app.plot_all()


class TransformEditorGrb(ShapeToolEditorGrb):
    def __init__(self, draw_app):
        ShapeToolEditorGrb.__init__(self, draw_app)
        self.name = 'transformation'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.draw_app = draw_app
        self.app = draw_app.app

        self.start_msg = _("Shape transformations ...")
        self.origin = (0, 0)
        self.draw_app.transform_tool.run()

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.apertures_table.clearSelection()
        self.draw_app.plot_all()


def edit_copy_name(name):
    """
    The name of the object that "Save as New" of an editor creates: <name>_edit, then <name>_edit_1, _edit_2...

    :param name:    name of the source object
    :return:        name of the new object
    """
    if "_edit" in name:
        try:
            return name[:-1] + str(int(name[-1]) + 1)
        except ValueError:
            return name + "_1"
    return name + "_edit"


def get_finite_bounds(obj):
    """
    The bounds (xmin, ymin, xmax, ymax) of an object; zeros for an empty object or one without usable bounds.

    :param obj: Gerber or Excellon object
    :return:    tuple of 4 floats
    """
    try:
        bounds = tuple(float(val) for val in obj.bounds())
    except Exception:
        return 0.0, 0.0, 0.0, 0.0
    if len(bounds) != 4 or not all(math.isfinite(val) for val in bounds):
        return 0.0, 0.0, 0.0, 0.0
    return bounds


class AppGerberEditor(QtCore.QObject):
    draw_shape_idx = -1
    # plot_finished = QtCore.pyqtSignal()
    mp_finished = QtCore.pyqtSignal(list)
    build_ui_sig = QtCore.pyqtSignal()
    update_ui_sig = QtCore.pyqtSignal()
    simplification_finished = QtCore.pyqtSignal(object)
    history_changed = QtCore.pyqtSignal()
    geometry_result = QtCore.pyqtSignal(object)
    load_result = QtCore.pyqtSignal(object)
    plot_object = QtCore.pyqtSignal(object)

    def __init__(self, app):
        # assert isinstance(app, FlatCAMApp.App), \
        #     "Expected the app to be a FlatCAMApp.App, got %s" % type(app)

        super(AppGerberEditor, self).__init__()

        self.app = app
        self.canvas = self.app.plotcanvas
        self.decimals = self.app.decimals

        # Current application units in Upper Case
        self.units = self.app.app_units.upper()

        self.ui = AppGerberEditorUI(self.app)

        # Toolbar events and properties
        self.tools_gerber = {}

        # # ## Data
        self.active_tool = None
        # numeric rectangle/text drawing panel hosted in the Properties tab
        self.region_drawing_panel = None

        self.storage_dict = {}
        self.current_storage = []

        # the pick index: the revision counts the changes of the shapes, see touch_geometry()
        self.geometry_revision = 0
        self._pick_cache = None
        self._shape_apertures = {}
        self._rendered_shapes = {}
        self._rendered_selection = set()
        self._render_state = None
        # the last click pick, so a repeated click at the same place picks the next shape (pick_cycle_at())
        self.pick_cycle = None
        # the raw (not snapped) position of the last mouse press, in plot units
        self.press_pos_raw = None

        self.sorted_apcode = []

        self.new_apertures = {}
        self.new_aperture_macros = {}

        # store here the plot promises, if empty the delayed plot will be activated
        self.grb_plot_promises = []

        # dictionary to store the tool_row and aperture codes in plugin_table
        # it will be updated everytime self.build_ui() is called
        self.oldapcode_newapcode = {}

        self.tid2apcode = {}

        # this will store the value for the last selected tool, for use after clicking on canvas when the selection
        # is cleared but as a side effect also the selected tool is cleared
        self.last_aperture_selected = None
        self.utility = []

        # this will store the polygons marked by mark are to be perhaps deleted
        self.geo_to_delete = []

        # set when the stored geometry can no longer be saved as native apertures (the save writes regions)
        self.loaded_as_regions = False

        # this will flag if the Editor "tools" are launched from key shortcuts (True) or from menu toolbar (False)
        self.launched_from_shortcuts = False

        # this var will store the state of the toolbar before starting the editor
        self.toolbar_old_state = False

        # #############################################################################################################
        # ######################### Init appGUI #######################################################################
        # #############################################################################################################
        self.ui.apdim_lbl.setDisabled(True)
        self.ui.apdim_entry.setDisabled(True)
        self.ui.apheight_entry.setDisabled(True)

        self.gerber_obj = None
        self.gerber_obj_options = {}

        # VisPy Visuals
        self.shapes = self.canvas.new_shape_collection(layers=1)
        self.tool_shape = self.canvas.new_shape_collection(layers=1)
        self.ma_annotation = self.canvas.new_text_group()
        # grips to drag the vertices of 1 selected region or track in the Select tool
        self.vertex_grips = VertexGrips(self, geometry=lambda shape: grip_geometry(self, shape),
                                        steps_option=lambda radius: gerber_steps(self.app, radius), snapshot=lambda: grip_state(self))

        # Remove from scene
        self.shapes.enabled = False
        self.tool_shape.enabled = False

        # List of selected geometric elements.
        self.selected = []

        self.key = None  # Currently pressed key
        self.modifiers = None
        self.x = None  # Current mouse cursor pos
        self.y = None
        # Current snapped mouse pos
        self.snap_x = None
        self.snap_y = None
        self.pos = None

        # #############################################################################################################
        # Plugin Attributes
        # #############################################################################################################
        self.last_length = 0.0

        self.last_parray_type = None
        self.last_parray_size = None
        self.last_parray_lin_dir = None
        self.last_parray_circ_dir = None
        self.last_parray_pitch = None
        self.last_parray_lin_angle = None
        self.last_parray_circ_angle = None
        self.last_parray_radius = None

        # used in RegionEditorGrb and TrackEditorGrb. Will store the bending mode
        self.bend_mode = 1

        # signal that there is an action active like polygon or path
        self.in_action = False
        # this will flag if the Editor "tools" are launched from key shortcuts (True) or from menu toolbar (False)
        self.launched_from_shortcuts = False

        def_tol_val = float(self.app.options.get(
            "global_tolerance",
            self.app.defaults.get(
                "global_tolerance",
                self.app.defaults.factory_defaults.get("global_tolerance")
            )
        ))
        self.tolerance = def_tol_val if self.units == 'MM' else def_tol_val / 25.4

        # options of this widget (AppGerberEditor class is a widget)
        self.editor_options = {
            "global_gridx": 0.1,
            "global_gridy": 0.1,
            "snap_max": 0.05,
            "grid_snap": True,
            "corner_snap": False,
            "grid_gap_link": True
        }
        # fill it with the application options (application preferences)
        self.editor_options.update(self.app.options)

        for option in self.editor_options:
            if option in self.app.options:
                self.editor_options[option] = self.app.options.get(
                    option,
                    self.app.defaults.get(option, self.app.defaults.factory_defaults.get(option))
                )

        # flag to show if the object was modified
        self.is_modified = False
        self.history = GerberHistory(self)
        self.edited_obj_name = ""
        self.tool_row = 0

        # Multiprocessing pool
        self.pool = self.app.pool

        # Multiprocessing results
        self.results = []

        # A QTimer
        self.plot_thread = None

        # a QThread for the edit process
        # self.thread = QtCore.QThread()

        # def entry2option(option, entry):
        #     self.editor_options[option] = float(entry.text())

        self.transform_tool = TransformEditorTool(self.app, self)

        # #############################################################################################################
        # ######################### Gerber Editor Signals #############################################################
        # #############################################################################################################
        self.mp_finished.connect(self.on_multiprocessing_finished)
        self.build_ui_sig.connect(self.build_ui)
        self.update_ui_sig.connect(self.update_ui)
        self.simplification_finished.connect(self.apply_simplification)
        self.history_changed.connect(self.refresh_history)
        self.geometry_result.connect(self.apply_geometry_result)
        self.load_result.connect(self.apply_load_result)

        self.ui.level.toggled.connect(self.on_level_changed)

        # connect the toolbar signals
        self.connect_grb_toolbar_signals()

        self.app.ui.grb_add_arc_menuitem.triggered.connect(lambda checked=False: self.select_tool('arc'))
        self.app.ui.grb_add_copper_pour_menuitem.triggered.connect(
            lambda checked=False: self.select_tool('copper_pour'))
        for kind in ('rectangle', 'text'):
            getattr(self.app.ui, 'grb_add_%s_menuitem' % kind).triggered.connect(
                lambda checked=False, tool=kind: self.on_region_drawing(tool))
        self.app.ui.grb_difference_keep_menuitem.triggered.connect(
            lambda checked=False: self.apply_region_operation('difference_keep'))

        self.app.ui.grb_add_pad_menuitem.triggered.connect(self.on_pad_add)
        self.app.ui.grb_add_pad_array_menuitem.triggered.connect(self.on_pad_add_array)

        self.app.ui.grb_add_track_menuitem.triggered.connect(self.on_track_add)
        self.app.ui.grb_add_region_menuitem.triggered.connect(self.on_region_add)

        self.app.ui.grb_convert_poly_menuitem.triggered.connect(self.on_poligonize)
        self.app.ui.grb_add_semidisc_menuitem.triggered.connect(self.on_add_semidisc)
        self.app.ui.grb_add_disc_menuitem.triggered.connect(self.on_disc_add)
        self.app.ui.grb_add_buffer_menuitem.triggered.connect(lambda: self.select_tool('buffer'))
        self.app.ui.grb_add_scale_menuitem.triggered.connect(lambda checked=False: self.select_tool('scale'))
        self.app.ui.grb_add_eraser_menuitem.triggered.connect(self.on_eraser)
        self.app.ui.grb_add_markarea_menuitem.triggered.connect(self.on_markarea)

        self.app.ui.grb_transform_menuitem.triggered.connect(lambda: self.transform_tool.run())

        self.app.ui.grb_copy_menuitem.triggered.connect(self.on_copy_button)
        self.app.ui.grb_delete_menuitem.triggered.connect(self.on_delete_btn)

        self.app.ui.grb_move_menuitem.triggered.connect(self.on_move_button)

        # the scale button is owned by ScaleEditorGrb, which connects it while the tool is active
        self.ui.size_select_btn.clicked.connect(self.on_select_by_size)
        self.ui.size_delete_btn.clicked.connect(self.on_delete_by_size)
        self.ui.split_action.triggered.connect(lambda: self.select_tool('split'))
        self.ui.join_btn.clicked.connect(self.on_join_paths)
        self.ui.resize_circles_btn.clicked.connect(self.on_resize_circles)
        self.ui.vertex_edit_btn.clicked.connect(self.on_edit_vertices)
        # the app rebuilds the editors (New Project) while the UI stays: drop the action of the previous editor
        self.remove_editor_actions()
        self.vertex_edit_action = self.app.ui.grb_editor_menu.addAction(_('Edit vertices'), self.on_edit_vertices)
        self.vertex_edit_action.setProperty(EDITOR_ACTION_PROPERTY, True)
        self.ui.features_table.itemSelectionChanged.connect(self.on_feature_selection)
        self.ui.history_positions.activated.connect(self.history_go_to)
        self.ui.diagnostics_btn.clicked.connect(self.on_diagnostics)
        self.ui.diagnostics_component.currentIndexChanged.connect(self.update_component_properties)

        self.app.ui.aperture_delete_btn.triggered.connect(self.on_delete_btn)
        self.ui.name_entry.returnPressed.connect(self.on_name_activate)

        self.ui.aptype_cb.currentIndexChanged.connect(self.on_aptype_changed)

        self.ui.addaperture_btn.clicked.connect(lambda: self.on_aperture_add())

        self.ui.delaperture_btn.clicked.connect(lambda: self.on_aperture_delete())
        self.ui.apertures_table.cellPressed.connect(self.on_row_selected)
        self.ui.apertures_table.selectionModel().selectionChanged.connect(self.on_table_selection)  # noqa

        self.ui.exit_editor_button.clicked.connect(lambda: self.app.on_editing_finished())

        self.conversion_factor = 1

        self.apertures_row = 0

        self.complete = True

        self.set_editor_ui()

        # #############################################################################################################
        # ############################### TOOLS TABLE context menu ####################################################
        # #############################################################################################################
        self.ui.apertures_table.setupContextMenu()
        self.ui.apertures_table.addContextMenu(
            _("Delete"), lambda: self.on_aperture_delete(),
            icon=QtGui.QIcon(self.app.resource_location + "/trash16.png"))
        self.app.log.debug("Initialization of the Gerber Editor is finished ...")

    def make_callback(self, the_tool):
        def f():
            self.on_tool_select(the_tool)

        return f

    def connect_grb_toolbar_signals(self):
        self.tools_gerber.update({
            "select": {"button": self.app.ui.grb_select_btn, "constructor": SelectEditorGrb},
            "pad": {"button": self.app.ui.grb_add_pad_btn, "constructor": PadEditorGrb},
            "array": {"button": self.app.ui.add_pad_ar_btn, "constructor": PadArrayEditorGrb},
            "track": {"button": self.app.ui.grb_add_track_btn, "constructor": TrackEditorGrb},
            "region": {"button": self.app.ui.grb_add_region_btn, "constructor": RegionEditorGrb},
            "copper_pour": {"button": self.app.ui.grb_add_copper_pour_btn, "constructor": CopperPourEditorGrb},
            "poligonize": {"button": self.app.ui.grb_convert_poly_btn, "constructor": PoligonizeEditorGrb},
            "semidisc": {"button": self.app.ui.grb_add_semidisc_btn, "constructor": DiscSemiEditorGrb},
            "arc": {"button": self.app.ui.grb_add_arc_btn, "constructor": ArcEditorGrb},
            "disc": {"button": self.app.ui.grb_add_disc_btn, "constructor": DiscEditorGrb},
            "buffer": {"button": self.app.ui.aperture_buffer_btn, "constructor": BufferEditorGrb},
            "simplify": {"button": self.app.ui.aperture_simplify_btn, "constructor": SimplifyEditorGrb},
            "scale": {"button": self.app.ui.aperture_scale_btn, "constructor": ScaleEditorGrb},
            "markarea": {"button": self.app.ui.aperture_markarea_btn, "constructor": MarkEditorGrb},
            "import": {"button": self.app.ui.grb_import_btn, "constructor": ImportEditorGrb},
            "eraser": {"button": self.app.ui.aperture_eraser_btn, "constructor": EraserEditorGrb},
            "copy": {"button": self.app.ui.aperture_copy_btn, "constructor": CopyEditorGrb},
            "transform": {"button": self.app.ui.grb_transform_btn, "constructor": TransformEditorGrb},
            "move": {"button": self.app.ui.aperture_move_btn, "constructor": MoveEditorGrb},
        })

        for tool in self.tools_gerber:
            if tool in ('bend', 'split'):
                continue  # Properties controls survive toolbar reconstruction and use clicked, not triggered.
            self.tools_gerber[tool]["button"].triggered.connect(self.make_callback(tool))  # Events
            self.tools_gerber[tool]["button"].setCheckable(True)

        if 'bend' not in self.tools_gerber:
            self.tools_gerber['bend'] = {"button": self.ui.bend_move_btn, "constructor": TrackBendEditorGrb}
            self.ui.bend_move_btn.setCheckable(True)
            self.ui.bend_move_btn.clicked.connect(self.make_callback('bend'))
        self.tools_gerber['split'] = {"button": self.ui.split_action, "constructor": FollowSplitEditorGrb}
        self.ui.split_action.setCheckable(True)

        for kind in ('rectangle', 'text'):
            getattr(self.app.ui, 'grb_add_%s_btn' % kind).triggered.connect(
                lambda checked=False, tool=kind: self.on_region_drawing(tool))
        self.app.ui.grb_difference_keep_btn.triggered.connect(
            lambda checked=False: self.apply_region_operation('difference_keep'))
        self.app.ui.grb_join_btn.triggered.connect(self.on_join_paths)

    @QtCore.pyqtSlot()
    def refresh_history(self):
        history = self.history
        history.enforce_limits()
        undo_label = history.undo_stack[-1]['label'] if history.undo_stack else ''
        redo_label = history.redo_stack[-1]['label'] if history.redo_stack else ''
        busy = history.pending is not None
        with QtCore.QSignalBlocker(self.ui.history_positions):
            self.ui.history_positions.clear()
            self.ui.history_positions.addItems(
                [_("Oldest retained state") if history.baseline_evicted else _("Initial state")] +
                [step['label'] for step in history.undo_stack + list(reversed(history.redo_stack))])
            self.ui.history_positions.setCurrentIndex(len(history.undo_stack))
            self.ui.history_positions.setEnabled(not busy)
        self.ui.exit_editor_button.setEnabled(not busy)
        if self.app.call_source == 'grb_editor':
            self.app.ui.on_history_changed('' if busy else undo_label, '' if busy else redo_label)

    def undo(self):
        return self.history.replay(False)

    def redo(self):
        return self.history.replay(True)

    def history_go_to(self, position):
        if not self.history.ready():
            return False
        if not 0 <= position <= len(self.history.undo_stack) + len(self.history.redo_stack):
            return False
        while len(self.history.undo_stack) != position:
            if not self.history.replay(len(self.history.undo_stack) < position):
                return False
        return True

    def submit_geometry(self, label, compute, matrix=None, shapes=None, success=None, on_commit=None):
        return self.history.submit(label, compute, matrix, shapes=shapes, success=success, on_commit=on_commit)

    @QtCore.pyqtSlot(object)
    def apply_geometry_result(self, result):
        self.history.apply_result(result)

    @QtCore.pyqtSlot(object)
    def apply_load_result(self, result):
        if self.history.pending is not result['pending']:
            return
        self.history.pending = None
        if result['pending'][1] != self.history.session:
            return
        if 'error' in result:
            self.app.inform.emit('[ERROR_NOTCL] %s' % result['error'])
            # nothing was loaded: leave a clean state
            self.loaded_as_regions = False
            self.history.notify()
            return
        self.storage_dict.update(result['storage'])
        self.loaded_as_regions = result['regions']
        self.touch_geometry()
        self.on_multiprocessing_finished()
        self.history.reset()
        self.is_modified = False

    def update_component_properties(self):
        component = self.ui.diagnostics_component.currentData() or 'solid'
        geometry = self.selected[-1].geo.get(component) if self.selected else None
        if geometry is None:
            self.ui.component_properties.setText(_("No selected geometry for this component."))
            self.ui.geo_coords_entry.setText('')
            self.ui.geo_vertex_entry.set_value(0)
            self.ui.area_entry.set_value(0)
            self.ui.is_valid_entry.setText(_("None"))
            return
        ring = geometry.exterior if isinstance(geometry, Polygon) else geometry
        ccw = str(ring.is_ccw) if isinstance(ring, LinearRing) else _("Not applicable")
        self.ui.component_properties.setText(
            '%s: %s | %s: %s | %s: %s\n%s: %s | %s: %s | %s: %.*f' % (
                _("Empty"), geometry.is_empty, _("Valid"), geometry.is_valid, _("Simple"), geometry.is_simple,
                _("Ring"), geometry.is_ring, _("CCW"), ccw, _("Length"), self.decimals, geometry.length))
        def vertex_count(geo):
            if isinstance(geo, Polygon):
                return len(geo.exterior.coords) + sum(len(ring.coords) for ring in geo.interiors)
            if hasattr(geo, 'geoms'):
                return sum(vertex_count(part) for part in geo.geoms)
            return len(geo.coords) if not geo.is_empty else 0

        self.ui.is_valid_entry.setText(str(geometry.is_valid))
        self.ui.area_entry.set_value(geometry.area)
        self.ui.geo_coords_entry.setText(geometry.wkt)
        self.ui.geo_vertex_entry.set_value(vertex_count(geometry))

    def diagnose_selected(self, component, tolerance):
        if not self.history.ready():
            return 'fail'
        try:
            tolerance = float(tolerance)
            if component not in ('solid', 'follow', 'clear') or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Choose a component and a finite non-negative tolerance."))
            return 'fail'
        if not self.selected:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select geometry to inspect."))
            return 'fail'
        report = {key: [] for key in ('missing', 'empty', 'zero_length', 'duplicate', 'invalid',
                                      'open', 'endpoints', 'near_endpoints')}
        selected = set(self.selected)
        normalized = {}

        def mark(kind, record):
            if record not in report[kind]:
                report[kind].append(record)

        def inspect(geometry, record):
            if geometry.is_empty:
                mark('empty', record)
                return
            if not geometry.is_valid or not geometry.is_simple:
                mark('invalid', record)
            if isinstance(geometry, Polygon):
                inspect(geometry.exterior, record)
                for ring in geometry.interiors:
                    inspect(ring, record)
            elif hasattr(geometry, 'geoms'):
                for part in geometry.geoms:
                    inspect(part, record)
            elif isinstance(geometry, (LineString, LinearRing)):
                if not math.isfinite(geometry.length):
                    mark('invalid', record)
                    return
                if geometry.length == 0:
                    mark('zero_length', record)
                    return
                key = shapely.normalize(LineString(geometry.coords)).wkb
                if key in normalized:
                    mark('duplicate', normalized[key])
                    mark('duplicate', record)
                else:
                    normalized[key] = record
                if not geometry.is_closed:
                    mark('open', record)
                    report['endpoints'].extend((record, tuple(geometry.coords[index])) for index in (0, -1))

        for apid, aperture in self.storage_dict.items():
            for index, shape in enumerate(aperture.get('geometry', ())):
                if shape not in selected:
                    continue
                record = (apid, index, component, shape)
                geometry = shape.geo.get(component)
                if geometry is None:
                    mark('missing', record)
                else:
                    inspect(geometry, record)
        endpoints = report['endpoints']
        if endpoints:
            points = [Point(coords[:2]) for record, coords in endpoints]
            tree = STRtree(points)
            for index, point in enumerate(points):
                for other in tree.query(point, predicate='dwithin', distance=tolerance):
                    if other > index:
                        report['near_endpoints'].append((endpoints[index], endpoints[other]))
        return report

    def on_diagnostics(self):
        report = self.diagnose_selected(self.ui.diagnostics_component.currentData(),
                                       self.ui.diagnostics_tolerance_entry.text().replace(',', '.'))
        if report == 'fail':
            return
        lines = [_('Read-only snapshot. Open FOLLOW paths are normal tracks, not automatically errors.')]
        for key, label in (('missing', _("Missing component")), ('empty', _("Empty")),
                           ('zero_length', _("Zero length")), ('duplicate', _("Duplicate")),
                           ('invalid', _("Invalid")), ('open', _("Open"))):
            records = ', '.join('%s/%s/%s' % (apid, index, component)
                                for apid, index, component, shape in report[key])
            lines.append('%s: %d%s' % (label, len(report[key]), ': ' + records if records else ''))
        lines.append('%s: %d' % (_("Near endpoint pairs"), len(report['near_endpoints'])))
        self.ui.diagnostics_result.setText('\n'.join(lines))
        return report

    def set_editor_ui(self):
        # updated units
        self.units = self.app.app_units.upper()
        self.decimals = self.app.decimals

        self.oldapcode_newapcode.clear()
        self.tid2apcode.clear()

        # update the oldapcode_newapcode dict to make sure we have an updated state of the plugin_table
        for key in self.storage_dict:
            self.oldapcode_newapcode[key] = key

        sort_temp = []
        for aperture in self.oldapcode_newapcode:
            sort_temp.append(int(aperture))
        self.sorted_apcode = sorted(sort_temp)

        # populate self.intial_table_rows dict with the tool number as keys and aperture codes as values
        for i in range(len(self.sorted_apcode)):
            tt_aperture = self.sorted_apcode[i]
            self.tid2apcode[i + 1] = tt_aperture

        # #############################################################################################################
        # Init appGUI
        # #############################################################################################################
        self.ui.scale_factor_entry.set_value(self.app.options.get(
            "gerber_editor_scale_f",
            self.app.defaults.get(
                "gerber_editor_scale_f",
                self.app.defaults.factory_defaults.get("gerber_editor_scale_f")
            )
        ))
        self.ui.ma_upper_threshold_entry.set_value(self.app.options.get(
            "gerber_editor_ma_high",
            self.app.defaults.get(
                "gerber_editor_ma_high",
                self.app.defaults.factory_defaults.get("gerber_editor_ma_high")
            )
        ))
        self.ui.ma_lower_threshold_entry.set_value(self.app.options.get(
            "gerber_editor_ma_low",
            self.app.defaults.get(
                "gerber_editor_ma_low",
                self.app.defaults.factory_defaults.get("gerber_editor_ma_low")
            )
        ))

        self.ui.apsize_entry.set_value(self.app.options.get(
            "gerber_editor_newsize",
            self.app.defaults.get(
                "gerber_editor_newsize",
                self.app.defaults.factory_defaults.get("gerber_editor_newsize")
            )
        ))
        self.ui.aptype_cb.set_value(self.app.options.get(
            "gerber_editor_newtype",
            self.app.defaults.get(
                "gerber_editor_newtype",
                self.app.defaults.factory_defaults.get("gerber_editor_newtype")
            )
        ))
        try:
            width, height = safe_eval(str(self.app.options.get(
                "gerber_editor_newdim",
                self.app.defaults.get(
                    "gerber_editor_newdim",
                    self.app.defaults.factory_defaults.get("gerber_editor_newdim")
                )
            )))
        except (TypeError, ValueError):
            width, height = 0.0, 0.0
        self.ui.apdim_entry.set_value(width)
        self.ui.apheight_entry.set_value(height)

        # PAD Array
        self.last_parray_type = 'linear'
        self.last_parray_size = int(self.app.options.get(
            'gerber_editor_array_size',
            self.app.defaults.get(
                'gerber_editor_array_size',
                self.app.defaults.factory_defaults.get('gerber_editor_array_size')
            )
        ))
        self.last_parray_lin_dir = self.app.options.get(
            'gerber_editor_lin_dir',
            self.app.defaults.get(
                'gerber_editor_lin_dir',
                self.app.defaults.factory_defaults.get('gerber_editor_lin_dir')
            )
        )
        self.last_parray_circ_dir = self.app.options.get(
            'gerber_editor_circ_dir',
            self.app.defaults.get(
                'gerber_editor_circ_dir',
                self.app.defaults.factory_defaults.get('gerber_editor_circ_dir')
            )
        )
        self.last_parray_pitch = float(self.app.options.get(
            'gerber_editor_lin_pitch',
            self.app.defaults.get(
                'gerber_editor_lin_pitch',
                self.app.defaults.factory_defaults.get('gerber_editor_lin_pitch')
            )
        ))
        self.last_parray_lin_angle = float(self.app.options.get(
            'gerber_editor_lin_angle',
            self.app.defaults.get(
                'gerber_editor_lin_angle',
                self.app.defaults.factory_defaults.get('gerber_editor_lin_angle')
            )
        ))
        self.last_parray_circ_angle = float(self.app.options.get(
            'gerber_editor_circ_angle',
            self.app.defaults.get(
                'gerber_editor_circ_angle',
                self.app.defaults.factory_defaults.get('gerber_editor_circ_angle')
            )
        ))
        self.last_parray_radius = 0.0

        self.ui.geo_coords_entry.setText('')
        self.ui.geo_vertex_entry.set_value(0.0)
        self.ui.geo_zoom.set_value(False)

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
            self.ui.apertures_table.itemChanged.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.apertures_table.cellPressed.disconnect()
        except (TypeError, AttributeError):
            pass

        # updated units
        self.units = self.app.app_units.upper()

        # make a new name for the new Excellon object (the one with edited content)
        self.edited_obj_name = self.gerber_obj.obj_options['name']
        self.ui.name_entry.set_value(self.edited_obj_name)

        self.apertures_row = 0
        # aper_no = self.apertures_row + 1

        sorted_apertures = sorted(self.storage_dict, key=aperture_rank)

        # sort = []
        # for k, v in list(self.gerber_obj.aperture_macros.items()):
        #     sort.append(k)
        # sorted_macros = sorted(sort)

        # n = len(sorted_apertures) + len(sorted_macros)
        n = len(sorted_apertures)
        self.ui.apertures_table.setRowCount(n)

        for ap_code in sorted_apertures:

            ap_code_item = QtWidgets.QTableWidgetItem('%d' % int(self.apertures_row + 1))
            ap_code_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.apertures_table.setItem(self.apertures_row, 0, ap_code_item)  # Tool name/id

            ap_code_item = QtWidgets.QTableWidgetItem(str(ap_code))
            ap_code_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

            ap_type_item = QtWidgets.QTableWidgetItem(str(self.storage_dict[ap_code]['type']))
            ap_type_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

            if str(self.storage_dict[ap_code]['type']) in ('R', 'O'):
                ap_dim_item = QtWidgets.QTableWidgetItem(
                    '%.*f, %.*f' % (self.decimals, self.storage_dict[ap_code]['width'],
                                    self.decimals, self.storage_dict[ap_code]['height']
                                    )
                )
                ap_dim_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable |
                                     QtCore.Qt.ItemFlag.ItemIsEnabled |
                                     QtCore.Qt.ItemFlag.ItemIsEditable)
            elif str(self.storage_dict[ap_code]['type']) == 'P':
                ap_dim_item = QtWidgets.QTableWidgetItem(
                    '%.*f, %.*f' % (self.decimals, self.storage_dict[ap_code]['diam'],
                                    self.decimals, self.storage_dict[ap_code]['nVertices'])
                )
                ap_dim_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable |
                                     QtCore.Qt.ItemFlag.ItemIsEnabled |
                                     QtCore.Qt.ItemFlag.ItemIsEditable)
            else:
                ap_dim_item = QtWidgets.QTableWidgetItem('')
                ap_dim_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

            try:
                if self.storage_dict[ap_code]['size'] is not None:
                    ap_size_item = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals,
                                                                        float(self.storage_dict[ap_code]['size'])))
                else:
                    ap_size_item = QtWidgets.QTableWidgetItem('')
            except KeyError:
                ap_size_item = QtWidgets.QTableWidgetItem('')

            if str(self.storage_dict[ap_code]['type']) == 'C':
                ap_size_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable |
                                      QtCore.Qt.ItemFlag.ItemIsEnabled |
                                      QtCore.Qt.ItemFlag.ItemIsEditable)
            else:
                ap_size_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable |
                                      QtCore.Qt.ItemFlag.ItemIsEnabled)

            self.ui.apertures_table.setItem(self.apertures_row, 1, ap_code_item)  # Aperture Code
            self.ui.apertures_table.setItem(self.apertures_row, 2, ap_type_item)  # Aperture Type
            self.ui.apertures_table.setItem(self.apertures_row, 3, ap_size_item)  # Aperture Size
            self.ui.apertures_table.setItem(self.apertures_row, 4, ap_dim_item)  # Aperture Dimensions

            self.apertures_row += 1
            if first_run is True:
                # set now the last aperture selected
                self.last_aperture_selected = ap_code

        # for ap_code in sorted_macros:
        #     ap_code = ap_code
        #
        #     ap_code_item = QtWidgets.QTableWidgetItem('%d' % int(self.apertures_row + 1))
        #     ap_code_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        #     self.ui.apertures_table.setItem(self.apertures_row, 0, ap_code_item)  # Tool name/id
        #
        #     ap_code_item = QtWidgets.QTableWidgetItem(ap_code)
        #
        #     ap_type_item = QtWidgets.QTableWidgetItem('AM')
        #     ap_type_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        #
        #     self.ui.apertures_table.setItem(self.apertures_row, 1, ap_code_item)  # Aperture Code
        #     self.ui.apertures_table.setItem(self.apertures_row, 2, ap_type_item)  # Aperture Type
        #
        #     self.apertures_row += 1
        #     if first_run is True:
        #         # set now the last aperture selected
        #         self.last_aperture_selected = ap_code

        self.ui.apertures_table.selectColumn(0)
        self.ui.apertures_table.resizeColumnsToContents()
        self.ui.apertures_table.resizeRowsToContents()

        vertical_header = self.ui.apertures_table.verticalHeader()
        # vertical_header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        vertical_header.hide()

        horizontal_header = self.ui.apertures_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 27)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.ui.apertures_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # self.ui.apertures_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.ui.apertures_table.setSortingEnabled(False)
        self.ui.apertures_table.setMinimumHeight(self.ui.apertures_table.getHeight())
        self.ui.apertures_table.setMaximumHeight(self.ui.apertures_table.getHeight())

        # make sure no rows are selected so the user have to click the correct row, meaning selecting the correct tool
        self.ui.apertures_table.clearSelection()

        # Remove anything else in the GUI Properties Tab
        self.app.ui.properties_scroll_area.takeWidget()
        # Put ourselves in the GUI Properties Tab
        self.app.ui.properties_scroll_area.setWidget(self.ui.grb_edit_widget)
        # Switch notebook to Properties page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

        # we reactivate the signals after the tool adding as we don't need to see the tool been populated
        self.ui.apertures_table.itemChanged.connect(self.on_tool_edit)
        self.ui.apertures_table.cellPressed.connect(self.on_row_selected)

        self.refresh_features()

        # for convenience set the next aperture code in the apcode field
        try:
            self.ui.apcode_entry.set_value(max(self.tid2apcode.values()) + 1)
        except ValueError:
            # this means that the edited object has no apertures, so we start with 10 (Gerber specifications)
            self.ui.apcode_entry.set_value(self.app.options.get(
                "gerber_editor_newcode",
                self.app.defaults.get(
                    "gerber_editor_newcode",
                    self.app.defaults.factory_defaults.get("gerber_editor_newcode")
                )
            ))

    @gerber_operation(_('Add aperture'))
    def on_aperture_add(self, apcode=None):
        self.is_modified = True
        if apcode is not None:
            ap_code = apcode
        else:
            ap_code = int(self.ui.apcode_entry.get_value())
            if ap_code == '' or ap_code is None:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Aperture code value is missing or wrong format. Add it and retry."))
                return

        size_val = float(self.ui.apsize_entry.get_value())
        type_val = self.ui.aptype_cb.currentText()

        if size_val == 0.0 and type_val not in ('R', 'O'):
            ap_code = 0

        if ap_code == 0:
            if ap_code not in self.tid2apcode.values():
                self.storage_dict[ap_code] = {
                    'type': 'REG',
                    'size': 0.0,
                    'geometry': []
                }
                self.ui.apsize_entry.set_value(0.0)

                # self.oldapcode_newapcode dict keeps the evidence on current aperture codes as keys and
                # gets updated on values each time an aperture code is edited or added
                self.oldapcode_newapcode[ap_code] = ap_code
            else:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Aperture already in the aperture table."))
                return
        else:
            if ap_code not in self.oldapcode_newapcode:
                if type_val in ('R', 'O', 'REG'):
                    try:
                        dims = self.ui.apdim_entry.get_value(), self.ui.apheight_entry.get_value()
                        if not all(math.isfinite(value) and value > 0 for value in dims):
                            raise ValueError(_("Dimensions must be finite and positive."))
                        size_val = np.sqrt((dims[0] ** 2) + (dims[1] ** 2))

                        self.storage_dict[ap_code] = {
                            'type': type_val,
                            'size': size_val,
                            'width': dims[0],
                            'height': dims[1],
                            'geometry': []
                        }

                        self.ui.apsize_entry.set_value(size_val)
                        # 'self.oldapcode_newapcode' dict keeps the evidence on current aperture codes as keys and gets
                        # updated on values  each time an aperture code is edited or added
                        self.oldapcode_newapcode[ap_code] = ap_code
                    except Exception as e:
                        self.app.log.error("AppGerberEditor.on_aperture_add() --> %s" % str(e))
                        self.app.inform.emit('[WARNING_NOTCL] %s' %
                                             _("Enter a positive width and height for the aperture and retry."))
                        return
                else:
                    self.storage_dict[ap_code] = {
                        'type':         type_val,
                        'size':         size_val,
                        'geometry':     []
                    }

                    # self.oldapcode_newapcode dict keeps the evidence on current aperture codes as keys and gets
                    # updated on values  each time an aperture code is edited or added
                    self.oldapcode_newapcode[ap_code] = ap_code
            else:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Aperture already in the aperture table."))
                return

        # since we add a new tool, we update also the initial state of the plugin_table through its dictionary
        # we add a new entry in the tid2apcode dict
        self.tid2apcode[len(self.oldapcode_newapcode)] = int(ap_code)
        self.touch_geometry()

        self.app.inform.emit('[success] %s: %s' % (_("Added new aperture with code"), str(ap_code)))

        self.build_ui()

        self.last_aperture_selected = ap_code

        if ap_code != 0:
            # make a quick sort through the tid2apcode dict, so we find which row to select
            row_to_be_selected = None
            for key in sorted(self.tid2apcode):
                if self.tid2apcode[key] == int(ap_code):
                    row_to_be_selected = int(key) - 1
                    break
        else:
            row_to_be_selected = 0
        self.ui.apertures_table.selectRow(row_to_be_selected)

    @gerber_operation(_('Delete aperture'))
    def on_aperture_delete(self, ap_code: str = None):
        """
        Called for aperture deletion.

        :param ap_code:     An Aperture code; String
        :return:
        """
        self.is_modified = True

        try:
            if ap_code:
                codes = [ap_code] if isinstance(ap_code, (str, int)) else list(ap_code)
                deleted_apcode_list = [key for code in codes for key in self.storage_dict if str(key) == str(code)]
            else:
                # deleted_tool_dia = float(self.ui.apertures_table.item(self.ui.apertures_table.currentRow(), 1).text())
                if len(self.ui.apertures_table.selectionModel().selectedRows()) == 0:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select an aperture in Aperture Table"))
                    return

                deleted_apcode_list = []
                for index in self.ui.apertures_table.selectionModel().selectedRows():
                    row = index.row()
                    deleted_apcode_list.append(int(self.ui.apertures_table.item(row, 1).text()))
        except Exception as exc:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Select an aperture in Aperture Table -->"), str(exc)))
            return

        if deleted_apcode_list:
            for deleted_aperture in deleted_apcode_list:
                # delete the storage used for that tool
                self.storage_dict.pop(deleted_aperture, None)
                self.touch_geometry()

                for deleted_tool in list(self.tid2apcode.keys()):
                    if self.tid2apcode[deleted_tool] == deleted_aperture:
                        # delete the tool
                        self.tid2apcode.pop(deleted_tool, None)

                self.oldapcode_newapcode.pop(deleted_aperture, None)
                # delete any shape that was left in the selected storage from the deleted aperture
                # that is required since to delete an aperture we need to first select it
                self.selected[:] = []
                if deleted_aperture not in self.storage_dict:
                    self.app.inform.emit('[success] %s: %s' % (_("Deleted aperture with code"), str(deleted_aperture)))
                else:
                    self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), str(deleted_aperture)))

        self.plot_all()
        self.build_ui()

        # if last aperture selected was in the apertures deleted than make sure to select a
        # 'new' last aperture selected because there are tools who depend on it.
        # if there is no aperture left, then add a default one :)
        if self.last_aperture_selected in deleted_apcode_list:
            if self.ui.apertures_table.rowCount() == 0:
                self.on_aperture_add(10)
                self.last_aperture_selected = 10
            else:
                self.last_aperture_selected = int(self.ui.apertures_table.item(0, 1).text())

    @gerber_operation(_('Edit aperture'))
    def on_tool_edit(self, *signal_args):
        if self.ui.apertures_table.currentItem() is None:
            return

        # if connected, disconnect the signal from the slot on item_changed as it creates issues
        self.ui.apertures_table.itemChanged.disconnect()
        try:
            result = self._apply_tool_edit()
        finally:
            # we reactivate the signals after the tool editing, whatever the outcome
            self.ui.apertures_table.itemChanged.connect(self.on_tool_edit)
        if result is True:
            self.build_ui()
            return None
        return result

    def _is_plain_flash(self, geo, aperture):
        """True if the 'solid' of a flash record of an 'R' or 'O' aperture is the full shape of the aperture."""
        solid, follow = geo.get('solid'), geo['follow']
        width, height = float(aperture['width']), float(aperture['height'])
        if solid is None or solid.is_empty or not getattr(geo.get('clear'), 'is_empty', True):
            return False
        if str(aperture['type']) == 'R':
            plain = box(follow.x - width / 2, follow.y - height / 2, follow.x + width / 2, follow.y + height / 2)
        else:
            plain = Gerber.create_flash_geometry(self, follow, aperture, gerber_steps(self.app, min(width, height) / 2))
        # compare the footprints, not the vertex order or the circle steps
        return solid.hausdorff_distance(plain) <= max(min(width, height) * 0.005, 1e-8)

    def _apply_tool_edit(self):
        """Apply the edited aperture table cell. Return True when the table must be rebuilt."""
        self.is_modified = True
        val_edited = None

        row_of_item_changed = self.ui.apertures_table.currentRow()
        col_of_item_changed = self.ui.apertures_table.currentColumn()

        # rows start with 0, tools start with 1, so we adjust the value by 1
        key_in_tid2apcode = row_of_item_changed + 1
        ap_code_old = self.tid2apcode[key_in_tid2apcode]


        if col_of_item_changed == 1:
            # we edited the Aperture Code column (int)
            try:
                val_edited = int(self.ui.apertures_table.currentItem().text())
            except ValueError as e:
                self.app.log.debug("AppGerberEditor.on_tool_edit() --> %s" % str(e))
                # self.ui.apertures_table.setCurrentItem(None)
                return
        elif col_of_item_changed == 3:
            # we edited the Size column (float)
            try:
                val_edited = float(self.ui.apertures_table.currentItem().text())
                if not math.isfinite(val_edited) or val_edited <= 0:
                    raise ValueError('Aperture size must be finite and positive')
            except ValueError as e:
                self.app.log.debug("AppGerberEditor.on_tool_edit() --> %s" % str(e))
                # self.ui.apertures_table.setCurrentItem(None)
                return
        elif col_of_item_changed == 4:
            # we edit the Dimensions column (tuple)
            try:
                val_edited = [
                    float(x.strip()) for x in self.ui.apertures_table.currentItem().text().split(",") if x != ''
                ]
            except ValueError as e:
                self.app.log.debug("AppGerberEditor.on_tool_edit() --> %s" % str(e))
                return

            if len(val_edited) != 2:
                self.app.inform.emit("[WARNING_NOTCL] %s" % _("Dimensions need two float values separated by comma."))
                old_dims_txt = '%s, %s' % (str(self.storage_dict[ap_code_old]['width']),
                                           str(self.storage_dict[ap_code_old]['height']))

                self.ui.apertures_table.currentItem().setText(old_dims_txt)
                return
            else:
                if not all(math.isfinite(value) and value > 0 for value in val_edited):
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Dimensions must be finite and positive."))
                    return 'fail'
                # the edit makes new full flashes: a flash with another shape (cut or edited) would lose its shape
                aperture = self.storage_dict[ap_code_old]
                if str(aperture['type']) in ('R', 'O') and \
                        not all(self._is_plain_flash(shape.geo, aperture) for shape in aperture['geometry']
                                if isinstance(shape.geo.get('follow'), Point)):
                    self.app.inform.emit('[WARNING_NOTCL] %s' %
                                         _("Dimension editing supports flashes with the aperture shape only."))
                    return 'fail'
                self.app.inform.emit("[success] %s" % _("Dimensions edited."))

        # In case we edited the Aperture Code therefore the val_edited holds a new Aperture Code
        # TODO Edit of the Aperture Code is not active yet
        if col_of_item_changed == 1:
            # aperture code is not used, so we create a new Aperture with the desired Aperture Code
            if val_edited not in self.oldapcode_newapcode.values():
                # update the dict that holds as keys old Aperture Codes and as values the new Aperture Codes
                self.oldapcode_newapcode[ap_code_old] = val_edited
                # update the dict that holds tool_no as key and tool_dia as value
                self.tid2apcode[key_in_tid2apcode] = val_edited

                old_aperture_val = self.storage_dict.pop(ap_code_old)
                self.storage_dict[val_edited] = old_aperture_val
                if self.last_aperture_selected == ap_code_old:
                    self.last_aperture_selected = val_edited
                self.touch_geometry()

            else:
                # aperture code is already in use so, we move the pads from the prior tool to the new tool
                # but only if they are of the same type

                target = next((key for key in self.storage_dict if str(key) == str(val_edited)), None)
                old_meta = {key: value for key, value in self.storage_dict[ap_code_old].items() if key != 'geometry'}
                new_meta = {key: value for key, value in self.storage_dict[target].items() if key != 'geometry'}
                if old_meta != new_meta:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Only identical apertures can be merged."))
                    return 'fail'
                if target != ap_code_old:
                    self.storage_dict[target]['geometry'].extend(self.storage_dict[ap_code_old]['geometry'])
                    del self.storage_dict[ap_code_old]
                    self.oldapcode_newapcode[ap_code_old] = target
                    self.last_aperture_selected = target
                    self.touch_geometry()

        # In case we edited the Size of the Aperture therefore the val_edited holds the new Aperture Size
        # It will happen only for the Aperture Type == 'C' - I make sure of that in the self.build_ui()
        elif col_of_item_changed == 3:
            old_size = float(self.storage_dict[ap_code_old]['size'])
            new_size = float(val_edited)
            adjust_size = (new_size - old_size) / 2
            quad_segs = gerber_quad(self.app, new_size / 2.0)
            geometry = []
            for geo_el in self.storage_dict[ap_code_old]['geometry']:
                g_data = geo_el.geo
                new_geo_el = dict(g_data)
                if 'solid' in g_data:
                    if 'follow' in g_data:
                        if isinstance(g_data['follow'], Point):
                            new_geo_el['solid'] = deepcopy(g_data['solid'].buffer(adjust_size, quad_segs=quad_segs))
                        else:
                            new_geo_el['solid'] = deepcopy(g_data['solid'].buffer(adjust_size, join_style="mitre"))
                    else:
                        new_geo_el['solid'] = g_data['solid'].buffer(adjust_size, quad_segs=quad_segs)
                if 'follow' in g_data:
                    new_geo_el['follow'] = deepcopy(g_data['follow'])
                if 'clear' in g_data:
                    new_geo_el['clear'] = deepcopy(g_data['clear'].buffer(adjust_size, join_style="mitre"))
                geometry.append(DrawToolShape(new_geo_el))

            replacements = dict(zip(self.storage_dict[ap_code_old]['geometry'], geometry))
            self.selected = [replacements.get(shape, shape) for shape in self.selected]
            self.storage_dict[ap_code_old]['geometry'].clear()
            self.storage_dict[ap_code_old]['size'] = new_size
            self.add_gerber_shape(geometry, self.storage_dict[ap_code_old]['geometry'])
            self.touch_geometry()
            # self.storage_dict[ap_code_old]['geometry'] = geometry

        # In case we edited the Dims of the Aperture therefore the val_edited holds a list with the dimensions
        # in the format [width, height]
        # It will happen only for the Aperture Type in ['R', 'O'] - I make sure of that in the self.build_ui()
        # and below
        elif col_of_item_changed == 4:
            if str(self.storage_dict[ap_code_old]['type']) == 'R' or \
                    str(self.storage_dict[ap_code_old]['type']) == 'REG':
                # use the biggest from them: a track grows (or shrinks) by half of its change, like a 'C' size edit
                old_aperture = self.storage_dict[ap_code_old]
                buff_val_lines = (max(val_edited) - max(float(old_aperture['width']),
                                                        float(old_aperture['height']))) / 2
                new_width = val_edited[0]
                new_height = val_edited[1]

                geometry = []
                for geo_el in self.storage_dict[ap_code_old]['geometry']:
                    g_data = geo_el.geo
                    new_geo_el = dict(g_data)
                    if 'solid' in g_data:
                        if 'follow' in g_data:
                            if isinstance(g_data['follow'], Point):
                                x = g_data['follow'].x
                                y = g_data['follow'].y
                                minx = x - (new_width / 2)
                                miny = y - (new_height / 2)
                                maxx = x + (new_width / 2)
                                maxy = y + (new_height / 2)
                                geo = box(xmin=minx, ymin=miny, xmax=maxx, ymax=maxy)
                                new_geo_el['solid'] = deepcopy(geo)
                            else:
                                new_geo_el['solid'] = deepcopy(g_data['solid'].buffer(buff_val_lines, join_style="mitre"))
                    if 'follow' in g_data:
                        new_geo_el['follow'] = deepcopy(g_data['follow'])
                    if 'clear' in g_data:
                        if 'follow' in g_data:
                            if isinstance(g_data['follow'], Point):
                                x = g_data['follow'].x
                                y = g_data['follow'].y
                                minx = x - (new_width / 2)
                                miny = y - (new_height / 2)
                                maxx = x + (new_width / 2)
                                maxy = y + (new_height / 2)
                                geo = box(xmin=minx, ymin=miny, xmax=maxx, ymax=maxy)
                                new_geo_el['clear'] = deepcopy(geo)
                            else:
                                new_geo_el['clear'] = deepcopy(g_data['clear'].buffer(buff_val_lines, join_style="mitre"))
                    geometry.append(DrawToolShape(new_geo_el))

                replacements = dict(zip(self.storage_dict[ap_code_old]['geometry'], geometry))
                self.selected = [replacements.get(shape, shape) for shape in self.selected]
                self.storage_dict[ap_code_old]['geometry'].clear()
                self.storage_dict[ap_code_old].update(width=new_width, height=new_height,
                                                     size=math.hypot(new_width, new_height))
                self.add_gerber_shape(geometry, self.storage_dict[ap_code_old]['geometry'])
                self.touch_geometry()

            elif self.storage_dict[ap_code_old]['type'] == 'O':
                new_meta = dict(self.storage_dict[ap_code_old])
                new_meta.update(width=val_edited[0], height=val_edited[1], size=math.hypot(*val_edited))
                geometry = []
                for shape in self.storage_dict[ap_code_old]['geometry']:
                    geo = dict(shape.geo)
                    if not isinstance(geo.get('follow'), Point) or not getattr(geo.get('clear'), 'is_empty', True):
                        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Oblong dimension editing supports flashes only."))
                        return 'fail'
                    geo['solid'] = Gerber.create_flash_geometry(
                        self, geo['follow'], new_meta, gerber_steps(self.app, min(val_edited) / 2.0))
                    geometry.append(DrawToolShape(geo))
                replacements = dict(zip(self.storage_dict[ap_code_old]['geometry'], geometry))
                self.selected = [replacements.get(shape, shape) for shape in self.selected]
                self.storage_dict[ap_code_old].update(new_meta)
                self.storage_dict[ap_code_old]['geometry'][:] = geometry
                self.touch_geometry()

        self.plot_all()
        return True

    def update_ui(self, zoom=True):
        """
        :param zoom:    False for history refreshes: only a user selection change may move the view
        """
        self.refresh_features()
        if self.selected:
            selected = set(self.selected)
            self.sync_aperture_rows(apid for apid, shape in self._feature_refs if shape in selected)
        self.update_component_properties()

        if not self.selected:
            self.ui.geo_coords_entry.setText('')
            self.ui.geo_vertex_entry.set_value(0)
            self.ui.area_entry.set_value(0)
            self.ui.is_valid_entry.setText(_("None"))
            return

        if zoom:
            self.zoom_selection()

    def zoom_selection(self):
        if not self.selected or not self.ui.geo_zoom.get_value():
            return

        # update the GUI only with the last selected shape information's
        last_sel_geo = self.selected[-1].geo
        last_sel_geo_solid = last_sel_geo.get('solid')

        # zoom on selected
        if last_sel_geo_solid is not None and not last_sel_geo_solid.is_empty:
            xmin, ymin, xmax, ymax = last_sel_geo_solid.bounds
            if xmin == xmax and ymin != ymax:
                xmin = ymin
                xmax = ymax
            elif xmin != xmax and ymin == ymax:
                ymin = xmin
                ymax = xmax

            rect = Rect(xmin, ymin, xmax, ymax)
            rect.left, rect.right = xmin, xmax
            rect.bottom, rect.top = ymin, ymax

            # Lock updates in other threads
            self.shapes.lock_updates()

            # adjust the view camera to be slightly bigger than the bounds so the shape collection can be
            # seen clearly otherwise the shape collection boundary will have no border
            dx = rect.right - rect.left
            dy = rect.top - rect.bottom
            x_factor = dx * 0.02
            y_factor = dy * 0.02

            rect.left -= x_factor
            rect.bottom -= y_factor
            rect.right += x_factor
            rect.top += y_factor

            self.app.plotcanvas.view.camera.rect = rect
            self.shapes.unlock_updates()

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

            self.ui.shape_frame.hide()

            # Context Menu section
            # self.ui.apertures_table.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            self.ui.shape_frame.show()

            # Context Menu section
            # self.ui.apertures_table.setupContextMenu()

    def on_name_activate(self):
        self.edited_obj_name = self.ui.name_entry.get_value()

    def on_aptype_changed(self, current_index):
        # 'O' is letter O not zero.
        # print(current_index)
        if current_index == 1 or current_index == 2:    # 1 == 'R' and 2 == 'O'
            self.ui.apdim_lbl.setDisabled(False)
            self.ui.apdim_entry.setDisabled(False)
            self.ui.apheight_entry.setDisabled(False)
            self.ui.apsize_entry.setDisabled(True)
        else:
            self.ui.apdim_lbl.setDisabled(True)
            self.ui.apdim_entry.setDisabled(True)
            self.ui.apheight_entry.setDisabled(True)
            self.ui.apsize_entry.setDisabled(False)

    def activate_grb_editor(self):
        self.history.reset()
        # adjust the status of the menu entries related to the editor
        self.app.ui.menueditedit.setDisabled(True)
        self.app.ui.menueditok.setDisabled(False)
        self.app.ui.on_history_changed('', '')
        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(False)
        self.app.ui.popmenu_save.setVisible(True)

        self.connect_canvas_event_handlers()

        # init working objects
        self.storage_dict = {}
        self.touch_geometry()
        self.current_storage = []
        self.sorted_apcode = []
        self.new_apertures = {}
        self.new_aperture_macros = {}
        self.grb_plot_promises = []
        self.oldapcode_newapcode = {}
        self.tid2apcode = {}
        self.loaded_as_regions = False
        self.geo_to_delete = []

        self.shapes.enabled = True
        self.tool_shape.enabled = True

        self.app.ui.corner_snap_btn.setVisible(True)
        self.app.ui.snap_magnet.setVisible(True)

        self.app.ui.grb_editor_menu.setDisabled(False)
        self.app.ui.grb_editor_menu.menuAction().setVisible(True)

        self.app.ui.editor_exit_btn_ret_action.setVisible(True)
        self.app.ui.editor_start_btn.setVisible(False)
        self.app.ui.grb_editor_cmenu.setEnabled(True)

        self.app.ui.grb_edit_toolbar.setDisabled(False)
        self.app.ui.grb_edit_toolbar.setVisible(True)
        # self.app.ui.grid_toolbar.setDisabled(False)

        # start with GRID toolbar activated
        if self.app.ui.grid_snap_btn.isChecked() is False:
            self.app.ui.grid_snap_btn.trigger()

        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(False)
        self.app.ui.popmenu_save.setVisible(True)

        self.app.ui.pop_menucolor.menuAction().setVisible(False)
        self.app.ui.popmenu_numeric_move.setVisible(False)
        self.app.ui.popmenu_move2origin.setVisible(False)

        self.app.ui.popmenu_disable.setVisible(False)
        self.app.ui.cmenu_newmenu.menuAction().setVisible(False)
        self.app.ui.popmenu_properties.setVisible(False)
        self.app.ui.grb_editor_cmenu.menuAction().setVisible(True)

    def deactivate_grb_editor(self):
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception as e:
            self.app.log.error("AppGerberEditor.deactivate_grb_editor() --> %s" % str(e))

        self.clear()

        # adjust the status of the menu entries related to the editor
        self.app.ui.menueditedit.setDisabled(False)
        self.app.ui.menueditok.setDisabled(True)
        history_of(self.app).notify()
        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(True)
        self.app.ui.popmenu_save.setVisible(False)

        self.disconnect_canvas_event_handlers()
        self.app.ui.grb_edit_toolbar.setDisabled(True)

        self.app.ui.corner_snap_btn.setVisible(False)
        self.app.ui.snap_magnet.setVisible(False)

        # set the Editor Toolbar visibility to what it was before entering the Editor
        self.app.ui.grb_edit_toolbar.setVisible(False) if self.toolbar_old_state is False \
            else self.app.ui.grb_edit_toolbar.setVisible(True)

        # Disable visuals
        self.shapes.enabled = False
        self.tool_shape.enabled = False
        # self.app.app_cursor.enabled = False

        self.app.ui.grb_editor_menu.setDisabled(True)
        self.app.ui.grb_editor_menu.menuAction().setVisible(False)

        self.app.ui.editor_exit_btn_ret_action.setVisible(False)
        self.app.ui.editor_start_btn.setVisible(True)

        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(True)
        self.app.ui.popmenu_save.setVisible(False)

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
        if self.gerber_obj:
            self.gerber_obj.visible = True

    # the other editors name this method deactivate(); App._deactivate_editor() calls that name on every editor
    deactivate = deactivate_grb_editor

    def connect_canvas_event_handlers(self):
        # Canvas events

        # make sure that the shortcuts key and mouse events will no longer be linked to the methods from FlatCAMApp
        # but those from AppGeoEditor

        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.canvas.graph_event_connect('mouse_press', self.on_canvas_click)
        self.canvas.graph_event_connect('mouse_move', self.on_canvas_move)
        self.canvas.graph_event_connect('mouse_release', self.on_canvas_click_release)
        self.canvas.graph_event_connect('key_release', self.on_canvas_key_release)

        self.canvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.canvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.canvas.graph_event_disconnect('mouse_double_click', self.app.on_mouse_double_click_over_plot)

        self.app.collection.view.clicked.disconnect()

        # Canvas context Menu
        self.app.ui.popmenu_copy.triggered.disconnect()
        self.app.ui.popmenu_delete.triggered.disconnect()
        self.app.ui.popmenu_move.triggered.disconnect()

        self.app.ui.popmenu_copy.triggered.connect(self.on_copy_button)
        self.app.ui.popmenu_delete.triggered.connect(self.on_delete_btn)
        self.app.ui.popmenu_move.triggered.connect(self.on_move_button)

        # Gerber Editor
        self.app.ui.grb_draw_pad.triggered.connect(self.on_pad_add)
        self.app.ui.grb_draw_pad_array.triggered.connect(self.on_pad_add_array)
        self.app.ui.grb_draw_track.triggered.connect(self.on_track_add)
        self.app.ui.grb_draw_region.triggered.connect(self.on_region_add)

        self.app.ui.grb_draw_poligonize.triggered.connect(self.on_poligonize)
        self.app.ui.grb_draw_semidisc.triggered.connect(self.on_add_semidisc)
        self.app.ui.grb_draw_disc.triggered.connect(self.on_disc_add)
        self.app.ui.grb_draw_buffer.triggered.connect(lambda: self.select_tool("buffer"))
        self.app.ui.grb_draw_scale.triggered.connect(lambda: self.select_tool("scale"))
        self.app.ui.grb_draw_markarea.triggered.connect(lambda: self.select_tool("markarea"))
        self.app.ui.grb_draw_eraser.triggered.connect(self.on_eraser)
        self.app.ui.grb_draw_transformations.triggered.connect(self.on_transform)

    def disconnect_canvas_event_handlers(self):

        # we restore the key and mouse control to FlatCAMApp method
        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.canvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.canvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
        self.canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.canvas.graph_event_connect('mouse_double_click', self.app.on_mouse_double_click_over_plot)
        self.app.collection.view.clicked.connect(self.app.collection.on_mouse_down)

        self.canvas.graph_event_disconnect('mouse_press', self.on_canvas_click)
        self.canvas.graph_event_disconnect('mouse_move', self.on_canvas_move)
        self.canvas.graph_event_disconnect('mouse_release', self.on_canvas_click_release)
        self.canvas.graph_event_disconnect('key_release', self.on_canvas_key_release)

        # Canvas context Mneu
        try:
            self.app.ui.popmenu_copy.triggered.disconnect(self.on_copy_button)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.popmenu_delete.triggered.disconnect(self.on_delete_btn)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.popmenu_move.triggered.disconnect(self.on_move_button)
        except (TypeError, AttributeError):
            pass

        self.app.ui.popmenu_copy.triggered.connect(self.app.on_copy_command)
        self.app.ui.popmenu_delete.triggered.connect(self.app.on_delete)
        self.app.ui.popmenu_move.triggered.connect(self.app.obj_move)

        # Gerber Editor
        try:
            self.app.ui.grb_draw_pad.triggered.disconnect(self.on_pad_add)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.grb_draw_pad_array.triggered.disconnect(self.on_pad_add_array)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.grb_draw_track.triggered.disconnect(self.on_track_add)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.grb_draw_region.triggered.disconnect(self.on_region_add)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.grb_draw_poligonize.triggered.disconnect(self.on_poligonize)
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_semidisc.triggered.disconnect(self.on_add_semidisc)
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_disc.triggered.disconnect(self.on_disc_add)
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_buffer.triggered.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_scale.triggered.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_markarea.triggered.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_eraser.triggered.disconnect(self.on_eraser)
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.grb_draw_transformations.triggered.disconnect(self.on_transform)
        except (TypeError, AttributeError):
            pass

        # disconnect the Jump signal
        try:
            self.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def clear(self):
        if isinstance(getattr(self, 'history', None), GerberHistory):
            self.history.reset()
        # try:
        #     self.thread.quit()
        #     self.thread.wait()
        # except Exception as erp:
        #     self.app.log.error("AppGerberEditor.clear() -> %s" % str(erp))

        self.close_region_drawing(return_to_select=False)
        if isinstance(self.active_tool, (ImportEditorGrb, TrackBendEditorGrb, ArcEditorGrb, CopperPourEditorGrb)):
            self.active_tool.clean_up()
        self.vertex_grips.hide()
        self.vertex_grips.segment_drag.clear()
        self.app.plotcanvas.text_cursor.parent = None
        self.app.plotcanvas.view.camera.zoom_callback = lambda *args: None
        self.active_tool = None
        self.selected = []
        self.geo_to_delete = []
        self.loaded_as_regions = False
        self.storage_dict.clear()
        self.touch_geometry()
        self._pick_cache = None
        self._shape_apertures.clear()
        self._rendered_shapes.clear()
        self._rendered_selection.clear()
        self._rendered_geometry = {}
        self._render_style = None
        self._render_had_utility = False
        self._render_state = None
        self.pick_cycle = None
        self.results.clear()

        self.shapes.clear(update=True)
        self.tool_shape.clear(update=True)
        self.ma_annotation.clear(update=True)

        self.refresh_features()

    def edit_fcgerber(self, orig_grb_obj):
        """
        Imports the geometry found in "self.tools" from the given FlatCAM Gerber object
        into the editor.

        :param orig_grb_obj: ExcellonObject
        :return: None
        """

        self.deactivate_grb_editor()
        self.activate_grb_editor()

        # reset the tool table
        self.ui.apertures_table.clear()

        self.ui.apertures_table.setHorizontalHeaderLabels(['#', _('Code'), _('Type'), _('Size'), _('Dim')])
        self.last_aperture_selected = None

        # create a reference to the source object
        self.gerber_obj = orig_grb_obj
        self.gerber_obj_options = orig_grb_obj.obj_options

        file_units = self.gerber_obj.units if self.gerber_obj.units else 'IN'
        app_units = self.app.app_units
        # self.conversion_factor = 25.4 if file_units == 'IN' else (1 / 25.4) if file_units != app_units else 1

        if file_units == app_units:
            self.conversion_factor = 1
        else:
            if file_units == 'IN':
                self.conversion_factor = 25.4
            else:
                self.conversion_factor = 0.0393700787401575

        def_tol_val = float(self.app.options.get(
            "global_tolerance",
            self.app.defaults.get(
                "global_tolerance",
                self.app.defaults.factory_defaults.get("global_tolerance")
            )
        ))
        self.tolerance = def_tol_val if self.units == 'MM' else def_tol_val / 25.4

        # Hide original geometry
        orig_grb_obj.visible = False

        # Set selection tolerance
        # DrawToolShape.tolerance = fc_excellon.drawing_tolerance * 10

        self.select_tool("select")

        try:
            # we activate this after the initial build as we don't need to see the tool been populated
            self.ui.apertures_table.itemChanged.connect(self.on_tool_edit)
        except Exception as e:
            self.app.log.error("AppGerberEditor.edit_fcgerber() --> %s" % str(e))

        # apply the conversion factor on the obj.tools
        for apcode in self.gerber_obj.tools:
            object_keys = list(self.gerber_obj.tools[apcode].keys())

            for key in object_keys:
                if key == 'width':
                    self.gerber_obj.tools[apcode]['width'] = self.gerber_obj.tools[apcode]['width'] * \
                                                             self.conversion_factor
                elif key == 'height':
                    self.gerber_obj.tools[apcode]['height'] = self.gerber_obj.tools[apcode]['height'] * \
                                                              self.conversion_factor
                elif key == 'diam':
                    self.gerber_obj.tools[apcode]['diam'] = self.gerber_obj.tools[apcode]['diam'] * \
                                                            self.conversion_factor
                elif key == 'size':
                    self.gerber_obj.tools[apcode]['size'] = self.gerber_obj.tools[apcode]['size'] * \
                                                            self.conversion_factor
                else:
                    self.gerber_obj.tools[apcode][key] = self.gerber_obj.tools[apcode][key]

        self.gerber_obj.units = app_units

        # # and then add it to the storage elements (each storage elements is a member of a list
        # def job_thread(aperture_id):
        #     with self.app.proc_container.new('%s: %s ...' %
        #                                      (_("Adding geometry for aperture"),  str(aperture_id))):
        #         storage_elem = []
        #         self.storage_dict[aperture_id] = {}
        #
        #         # add the Gerber geometry to editor storage
        #         for k, v in self.gerber_obj.tools[aperture_id].items():
        #             try:
        #                 if k == 'geometry':
        #                     for geo_el in v:
        #                         if geo_el:
        #                             self.add_gerber_shape(DrawToolShape(geo_el), storage_elem)
        #                     self.storage_dict[aperture_id][k] = storage_elem
        #                 else:
        #                     self.storage_dict[aperture_id][k] = self.gerber_obj.tools[aperture_id][k]
        #             except Exception as e:
        #                 self.app.log.error("AppGerberEditor.edit_fcgerber().job_thread() --> %s" % str(e))
        #
        #         # Check promises and clear if exists
        #         while True:
        #             try:
        #                 self.grb_plot_promises.remove(aperture_id)
        #                 time.sleep(0.5)
        #             except ValueError:
        #                 break
        #
        # # we create a job work each aperture, job that work in a threaded way to store the geometry in local storage
        # # as DrawToolShapes
        # for ap_code in self.gerber_obj.tools:
        #     self.grb_plot_promises.append(ap_code)
        #     self.app.worker_task.emit({'fcn': job_thread, 'params': [ap_code]})
        #
        # self.set_editor_ui()
        #
        # # do the delayed plot only if there is something to plot (the gerber is not empty)
        # try:
        #     if bool(self.gerber_obj.tools):
        #         self.start_delayed_plot(check_period=1000)
        #     else:
        #         raise AttributeError
        # except AttributeError:
        #     # now that we have data (empty data actually), create the GUI interface and add it to the Tool Tab
        #     self.build_ui(first_run=True)
        #     # and add the first aperture to have something to play with
        #     self.on_aperture_add(10)

        # self.app.worker_task.emit({'fcn': worker_job, 'params': [self]})

        pending = self.history.reserve()
        source = self.gerber_obj
        # read at each open: 0 loads an object with clear features as regions, 1 keeps its apertures
        load_apertures = self.app.options.get(
            "gerber_editor_load",
            self.app.defaults.get(
                "gerber_editor_load",
                self.app.defaults.factory_defaults.get("gerber_editor_load")
            )
        ) == 1

        def load():
            result = {'pending': pending}
            with self.app.proc_container.new('%s ...' % _("Loading")):
                try:
                    tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry,
                                                             apertures=load_apertures)
                    result['regions'] = tools is not source.tools
                    jobs = [self.pool.apply_async(AppGerberEditor.add_apertures, args=(key, value))
                            for key, value in tools.items()]
                    result['storage'] = {key: deepcopy(value) for key, value in (job.get() for job in jobs)}
                except Exception as err:
                    result['error'] = str(err)
                self.load_result.emit(result)

        self.app.worker_task.emit({'fcn': load, 'params': []})

    @staticmethod
    def build_edit_tools(tools, solid_geometry, apertures=False):
        """
        Build the apertures dictionary that the editor loads.

        The editor draws and saves only the 'solid' of the records, so the 'clear' (LPC) features of a record would
        be lost. An object that holds such features is loaded in one of two ways:

        * as its composed geometry (the polarity is already applied there): one region 'aperture' 0 with 1 record
          per polygon, the largest exterior first;
        * with ``apertures`` set, as its apertures, with the clear features cut from the 'solid' and the 'follow'
          of the records (see clip_edit_tools()).

        An object without clear features is loaded as it is.

        :param tools:           the apertures dictionary of the source Gerber object; it is not changed
        :param solid_geometry:  the composed solid_geometry of the source Gerber object
        :param apertures:       keep the apertures of an object with clear features
        :return:                ``tools`` itself (no clear feature), or a new dictionary
        """
        # same test as Gerber.needs_region_export(); None, or [] of older code, is no clear geometry
        clears = [record['clear'] for tool in tools.values() for record in tool.get('geometry', ())
                  if not getattr(record.get('clear'), 'is_empty', True)]
        if not clears:
            return tools

        from appParsers.feature_access import normalize_polygonal
        solid = normalize_polygonal(solid_geometry if solid_geometry is not None else [])
        if apertures:
            return AppGerberEditor.clip_edit_tools(tools, solid, clears)
        polygons = [solid] if isinstance(solid, Polygon) else list(solid.geoms)
        polygons = [poly for poly in polygons if not poly.is_empty]
        polygons.sort(key=lambda poly: Polygon(poly.exterior).area, reverse=True)
        return {0: {'type': 'REG', 'size': 0.0,
                    'geometry': [{'solid': poly, 'follow': poly.exterior} for poly in polygons]}}

    @staticmethod
    def clip_edit_tools(tools, solid, clears):
        """
        Keep the apertures of an object with clear features: cut from each record the area that the clear features
        remove. That area is the part of the clear features that is not in the composed solid, so the cut needs no
        order of the records, and the union of the cut records is the composed solid.

        The records that hold only a clear feature are dropped, and no record keeps its 'clear'. The editor draws only
        a Polygon solid, so each polygon of a cut record is a record, with the part of the 'follow' inside it: a
        region gets the exterior of its polygon, a flash whose 'follow' point the cut removes gets the boundary.
        The work is done on shapely arrays: one tree query finds the (record, removed part) pairs.

        :param tools:   the apertures dictionary of the source Gerber object; it is not changed
        :param solid:   the composed solid of the source Gerber object (Polygon or MultiPolygon)
        :param clears:  the clear geometries of the records
        :return:        a new apertures dictionary with the same aperture codes
        """
        removed = shapely.get_parts(shapely.difference(shapely.union_all(np.array(clears, dtype=object)), solid))
        removed = removed[shapely.area(removed) > 0]

        records = [record for tool in tools.values() for record in tool.get('geometry', ())
                   if record.get('solid') is not None and not record['solid'].is_empty]
        cut = {}
        if len(removed) and records:
            solids = np.array([record['solid'] for record in records], dtype=object)
            rec_idx, part_idx = shapely.STRtree(removed).query(solids, predicate='intersects')
            order = np.argsort(rec_idx, kind='stable')
            rec_idx, part_idx = rec_idx[order], part_idx[order]
            starts = np.flatnonzero(np.r_[True, rec_idx[1:] != rec_idx[:-1]]) if len(rec_idx) else rec_idx
            ends = np.r_[starts[1:], len(rec_idx)]
            # a record with 1 removed part is cut by that part, the others by the union of their parts
            cutters = np.empty(len(starts), dtype=object)
            single = (ends - starts) == 1
            cutters[single] = removed[part_idx[starts[single]]]
            for group in np.flatnonzero(~single):
                cutters[group] = shapely.union_all(removed[part_idx[starts[group]:ends[group]]])
            hit = rec_idx[starts]
            new_solids = shapely.difference(solids[hit], cutters)
            follows = np.array([records[i].get('follow') for i in hit], dtype=object)
            has_follow = np.array([follow is not None for follow in follows], dtype=bool)
            new_follows = np.empty(len(hit), dtype=object)
            new_follows[has_follow] = shapely.difference(follows[has_follow], cutters[has_follow])
            for i, new_solid, new_follow in zip(hit, new_solids, new_follows):
                cut[id(records[i])] = (new_solid, new_follow)

        new_tools = {}
        for apid, tool in tools.items():
            new_tools[apid] = {key: value for key, value in tool.items() if key != 'geometry'}
            geometry = new_tools[apid]['geometry'] = []
            for record in tool.get('geometry', ()):
                if record.get('solid') is None or record['solid'].is_empty:
                    continue
                new_record = {key: value for key, value in record.items() if key != 'clear'}
                if id(record) not in cut:
                    geometry.append(new_record)
                    continue
                new_solid, new_follow = cut[id(record)]
                # the editor draws only a Polygon solid: each polygon of the cut is a record
                for part in shapely.get_parts(new_solid):
                    if not isinstance(part, Polygon) or part.is_empty:
                        continue
                    part_record = dict(new_record, solid=part)
                    if tool.get('type') == 'REG':
                        part_record['follow'] = part.exterior
                    elif new_follow is not None:
                        piece = new_follow.intersection(part)
                        part_record['follow'] = part.boundary if piece.is_empty else piece
                    geometry.append(part_record)
        return new_tools

    @staticmethod
    def add_apertures(aperture_id, aperture_dict):
        storage_elem = []
        storage_dict = {}

        for k, v in list(aperture_dict.items()):
            try:
                if k == 'geometry':
                    for geo_el in v:
                        if geo_el:
                            storage_elem.append(DrawToolShape(geo_el))
                    storage_dict[k] = storage_elem
                else:
                    storage_dict[k] = aperture_dict[k]
            except Exception as e:
                log.error("AppGerberEditor.edit_fcgerber().job_thread() --> %s" % str(e))

        return [aperture_id, storage_dict]

    def on_multiprocessing_finished(self):
        self.touch_geometry()
        self.app.proc_container.update_view_text(' %s' % _("Setting up the UI"))
        self.app.inform.emit('[success] %s.' % _("Adding geometry finished. Preparing the GUI"))
        self.set_editor_ui()
        self.build_ui(first_run=True)
        self.plot_all()

        # HACK: enabling/disabling the cursor seams to somehow update the shapes making them more 'solid'
        # - perhaps is a bug in VisPy implementation
        self.app.app_cursor.enabled = False
        self.app.app_cursor.enabled = True
        self.app.inform.emit('[success] %s' % _("Finished loading the Gerber object into the editor."))

    def update_fcgerber(self):
        """
        Save as New: create a new Gerber object that contain the edited content of the source Gerber object.
        The source object is not changed.

        :return: None
        """
        if isinstance(getattr(self, 'history', None), GerberHistory) and not self.history.ready():
            return 'fail'
        new_grb_name = edit_copy_name(self.edited_obj_name)

        source = self.gerber_obj
        history = getattr(self, 'history', None)
        history = history if isinstance(history, GerberHistory) else None
        pending = history.reserve() if history is not None else None

        def apply_signal_handler(result):
            if history is not None:
                if history.pending is not pending:
                    return
                history.pending = None
                history.notify()
                if pending[1:] != (history.session, self.geometry_revision) or self.gerber_obj is not source:
                    return
            if not isinstance(result, dict) or 'obj' not in result:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The edited content could not be saved. "
                                                            "The object is unchanged."))
                return
            edited = result['obj']

            def obj_init(grb_obj, app_obj):
                grb_obj.obj_options.update(edited.obj_options)
                grb_obj.units = edited.units
                for attr in ('int_digits', 'frac_digits'):
                    if hasattr(edited, attr):
                        setattr(grb_obj, attr, getattr(edited, attr))
                AppGerberEditor.apply_edited_gerber(grb_obj, edited)

            try:
                created = self.app.app_obj.new_object('gerber', new_grb_name, obj_init)
                if created == 'fail':
                    return
            except Exception as e:
                self.app.log.error("AppGerberEditor.update_fcgerber() --> %s" % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The edited content could not be saved. "
                                                            "The object is unchanged."))
                return
            self.deactivate_grb_editor()
            self.app.inform.emit('[success] %s' % _("Done."))

        self.app.connect_custom_signal(apply_signal_handler, object)
        context = AppGerberEditor._save_context(self)
        storage = AppGerberEditor._save_storage(self.storage_dict)
        build = lambda name, records: AppGerberEditor.build_edited_gerber(context, name, records)
        try:
            self.app.worker_task.emit({'fcn': build, 'params': [new_grb_name, storage]})
        except Exception:
            if history is not None:
                history.pending = None
                history.notify()
            raise

    def update_fcgerber_in_place(self, on_complete=None):
        """
        Save: the edited content goes into the source Gerber object, which keeps its name, history uid, place in the
        project and options (the bounds are recomputed).

        The content is built by a worker task in a detached Gerber object, without touching the source. The source is
        changed on the GUI thread, in one step (apply_edited_gerber()). A failed build leaves the source unchanged
        and the editor open, with an error message.

        :param on_complete: called on the GUI thread after the source is updated and the editor is deactivated; it
                            calls refresh_edited_gerber() (after it cleans the Properties area)
        :return:            None
        """
        if isinstance(getattr(self, 'history', None), GerberHistory) and not self.history.ready():
            return 'fail'
        source = self.gerber_obj
        history = getattr(self, 'history', None)
        history = history if isinstance(history, GerberHistory) else None
        pending = history.reserve() if history is not None else None
        context = AppGerberEditor._save_context(self)
        storage = AppGerberEditor._save_storage(self.storage_dict)

        def apply_signal_handler(result):
            if history is not None:
                if history.pending is not pending:
                    return
                history.pending = None
                history.notify()
                if pending[1:] != (history.session, self.geometry_revision) or self.gerber_obj is not source:
                    return
            if not isinstance(result, dict) or 'obj' not in result:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The edited content could not be saved. "
                                                            "The object is unchanged."))
                return
            try:
                AppGerberEditor.apply_edited_gerber(source, result['obj'])
            except Exception as e:
                self.app.log.error("AppGerberEditor.update_fcgerber_in_place() --> %s" % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The edited content could not be saved. "
                                                            "The object is unchanged."))
                return
            self.deactivate_grb_editor()
            if on_complete is not None:
                on_complete()

        self.app.connect_custom_signal(apply_signal_handler, object)
        build = (lambda name, records: AppGerberEditor.build_edited_gerber(context, name, records)) \
            if history is not None else self.build_edited_gerber
        try:
            self.app.worker_task.emit({'fcn': build, 'params': [source.obj_options['name'], storage]})
        except Exception:
            if history is not None:
                history.pending = None
                history.notify()
            raise

    def build_edited_gerber(self, outname, aperture_storage):
        """
        Worker thread. Builds the edited content in a detached Gerber object (not in the project, the source object
        is only read) and sends it to the GUI thread with the custom_signal: {'obj': object} or {'error': text}.

        :param outname:             name of the detached object (the name of the source)
        :param aperture_storage:    a dictionary that holds all the objects geometry
        :return:                    None
        """
        result = {}
        try:
            with self.app.proc_container.new('%s...' % _("Working")):
                edited = type(self.gerber_obj)(outname, app=self.app)
                for attr in ('int_digits', 'frac_digits'):
                    if hasattr(self.gerber_obj, attr):
                        setattr(edited, attr, getattr(self.gerber_obj, attr))
                AppGerberEditor._fill_edited_gerber(
                    self, edited, self.app, outname, AppGerberEditor._storage_with_geometry(aperture_storage))
                edited.moveToThread(self.app.main_thread)
                result = {'obj': edited}
        except Exception as e:
            self.app.log.error("AppGerberEditor.build_edited_gerber() --> %s" % str(e))
            result = {'error': str(e)}
        finally:
            self.results = []
        self.app.custom_signal.emit(result)

    @staticmethod
    def apply_edited_gerber(source, edited):
        """
        Put the content of a detached edited Gerber object into the source object. The name, history uid, tree
        position and obj_options of the source stay; the bounds in obj_options are recomputed. All the values are
        read before the first assignment, a failure leaves the source unchanged.

        :param source:  the source GerberObject
        :param edited:  the detached GerberObject made by build_edited_gerber()
        :return:        None
        """
        bounds = get_finite_bounds(edited)
        tools = edited.tools
        solid_geometry = edited.solid_geometry
        follow_geometry = edited.follow_geometry
        aperture_macros = edited.aperture_macros
        export_as_regions = edited.export_as_regions

        source.tools = tools
        source.solid_geometry = solid_geometry
        source.follow_geometry = follow_geometry
        source.aperture_macros = aperture_macros
        source.export_as_regions = export_as_regions
        source.multigeo = False
        source.follow = False
        if edited.source_file:
            source.source_file = edited.source_file
        # the aperture table and the aperture marks belong to the old apertures
        source.ui_build = False
        source.marked_rows = []
        for key, value in zip(('xmin', 'ymin', 'xmax', 'ymax'), bounds):
            source.obj_options[key] = value

    @staticmethod
    def refresh_edited_gerber(source):
        """
        Refresh the GUI of a source object after apply_edited_gerber(): aperture table, plot, Properties.
        """
        try:
            source.clear_plot_apertures()
        except Exception as e:
            source.app.log.debug("AppGerberEditor.refresh_edited_gerber() --> %s" % str(e))
        source.build_ui()
        source.plot()
        source.app.app_obj.object_changed.emit(source)

    @staticmethod
    def update_options(obj):
        try:
            if not obj.obj_options:
                obj.obj_options = {'xmin': 0, 'ymin': 0, 'xmax': 0, 'ymax': 0}
                return True
            else:
                return False
        except AttributeError:
            obj.obj_options = {}
            return True

    @staticmethod
    def _save_storage(storage):
        return {key: {**{name: value for name, value in aperture.items() if name != 'geometry'},
                      'geometry': [DrawToolShape(dict(shape.geo) if isinstance(shape.geo, dict) else shape.geo)
                                   for shape in aperture.get('geometry', ())]}
                for key, aperture in storage.items()}

    @staticmethod
    def _save_context(editor):
        return SimpleNamespace(app=editor.app, gerber_obj=editor.gerber_obj,
                               gerber_obj_options=dict(editor.gerber_obj_options),
                               loaded_as_regions=getattr(editor, 'loaded_as_regions', False), results=[])

    @staticmethod
    def _storage_with_geometry(storage_dict):
        """
        A deep copy of the apertures of the editor storage that have geometry.
        """
        local_storage_dict = {}
        for aperture in storage_dict:
            if 'geometry' in storage_dict[aperture]:
                # add aperture only if it has geometry
                if len(storage_dict[aperture]['geometry']) > 0:
                    local_storage_dict[aperture] = deepcopy(storage_dict[aperture])
        return local_storage_dict

    def _fill_edited_gerber(self, grb_obj, app_obj, out_name, local_storage_dict):
        """
        Fill a Gerber object with the edited content: tools, solid and follow geometry, aperture macros, options.
        It reads the source object (self.gerber_obj) and writes only to grb_obj.

        :param grb_obj:             the GerberObject to fill
        :param app_obj:             the App
        :param out_name:            name of the object
        :param local_storage_dict:  the apertures that have geometry, from _storage_with_geometry()
        :return:                    None
        """
        poly_buffer = []
        follow_buffer = []

        for storage_apcode, storage_val in local_storage_dict.items():
            grb_obj.tools[storage_apcode] = {}

            for k, val in storage_val.items():
                if k == 'geometry':
                    grb_obj.tools[storage_apcode][k] = []
                    for geo_el in val:
                        geometric_data = geo_el.geo
                        # An explicit empty solid is not a native flash/track, even if its old follow remains.
                        # Keep clear-only data; never recreate copper from the stale follow.
                        if geometric_data.get('solid') is not None and geometric_data['solid'].is_empty:
                            geometric_data = {key: value for key, value in geometric_data.items()
                                              if key not in ('solid', 'follow')}
                        new_geo_el = {}
                        if 'solid' in geometric_data:
                            new_geo_el['solid'] = geometric_data['solid']
                            poly_buffer.append(deepcopy(new_geo_el['solid']))

                        if 'follow' in geometric_data:
                            # if isinstance(geometric_data['follow'], Polygon):
                            #     buff_val = -(int(storage_val['size']) / 2)
                            #     geo_f = (geometric_data['follow'].buffer(buff_val)).exterior
                            #     new_geo_el['follow'] = geo_f
                            # else:
                            #     new_geo_el['follow'] = geometric_data['follow']
                            new_geo_el['follow'] = geometric_data['follow']
                            follow_buffer.append(deepcopy(new_geo_el['follow']))
                        else:
                            if 'solid' in geometric_data:
                                solid = geometric_data['solid']
                                geo_f = solid.exterior if isinstance(solid, Polygon) else solid.boundary
                                new_geo_el['follow'] = geo_f
                                follow_buffer.append(deepcopy(new_geo_el['follow']))

                        if 'clear' in geometric_data:
                            new_geo_el['clear'] = geometric_data['clear']

                        if new_geo_el:
                            grb_obj.tools[storage_apcode][k].append(deepcopy(new_geo_el))
                else:
                    grb_obj.tools[storage_apcode][k] = val

        grb_obj.aperture_macros = deepcopy(self.gerber_obj.aperture_macros)
        # the rebuilt records don't prove that they represent the edited result: keep the flag
        # an object loaded as regions (it had clear features) is saved through the region export too: it keeps
        # the holes and the islands of the regions in any order
        grb_obj.export_as_regions = bool(getattr(self.gerber_obj, 'export_as_regions', False) or
                                         getattr(self, 'loaded_as_regions', False) or
                                         any(isinstance(poly, MultiPolygon) for poly in poly_buffer))

        follow_buffer = flatten_shapely_geometry(follow_buffer)

        new_poly = MultiPolygon(flatten_shapely_geometry(poly_buffer))
        new_poly = new_poly.buffer(0.00000001)
        new_poly = new_poly.buffer(-0.00000001)
        new_poly = flatten_shapely_geometry(new_poly)
        grb_obj.solid_geometry = deepcopy(new_poly)

        grb_obj.follow_geometry = follow_buffer

        for k, v in self.gerber_obj_options.items():
            if k == 'name':
                grb_obj.obj_options[k] = out_name
            else:
                grb_obj.obj_options[k] = deepcopy(v)

        grb_obj.multigeo = False
        grb_obj.follow = False
        grb_obj.units = app_obj.app_units

        try:
            grb_obj.create_geometry()
        except KeyError:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("There are no Aperture definitions in the file. Aborting Gerber creation."))
        except Exception:
            msg = '[ERROR] %s' % _("An internal error has occurred. See shell.\n")
            msg += traceback.format_exc()
            app_obj.inform.emit(msg)
            raise

        grb_obj.regenerate_source(name=out_name)

    def on_tool_select(self, tool):
        """
        Behavior of the toolbar. Tool initialization.

        :rtype : None
        """
        if not self.history.ready():
            # the toolbar button was already toggled by the user: restore its previous state
            if tool in self.tools_gerber:
                button = self.tools_gerber[tool]["button"]
                button.setChecked(not button.isChecked())
            return 'fail'
        current_tool = tool
        self.vertex_grips.cancel()
        # switching toolbar tools ends the numeric drawing session
        self.close_region_drawing(return_to_select=False)

        self.app.log.debug("on_tool_select('%s')" % tool)

        if isinstance(self.active_tool, ScaleEditorGrb):
            self.active_tool.disconnect_scale()
        if isinstance(self.active_tool, (ImportEditorGrb, TrackBendEditorGrb, ArcEditorGrb, CopperPourEditorGrb)):
            self.active_tool.clean_up()

        if self.last_aperture_selected is None and current_tool not in ['select', 'disc', 'semidisc', 'region', 'bend', 'split', 'copper_pour']:
            # self.draw_app.select_tool('select')
            self.complete = True
            current_tool = 'select'
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("No aperture is selected.")))

        # This is to make the group behave as radio group
        if current_tool in self.tools_gerber:
            if self.tools_gerber[current_tool]["button"].isChecked():
                self.app.log.debug("%s is checked." % current_tool)
                for t in self.tools_gerber:
                    if t != current_tool:
                        self.tools_gerber[t]["button"].setChecked(False)

                # this is where the Editor toolbar classes (button's) are instantiated
                self.active_tool = self.tools_gerber[current_tool]["constructor"](self)
                # self.app.inform.emit(self.active_tool.start_msg)
            else:
                self.app.log.debug("%s is NOT checked." % current_tool)
                for t in self.tools_gerber:
                    self.tools_gerber[t]["button"].setChecked(False)

                self.select_tool('select')
                self.active_tool = SelectEditorGrb(self)

        self.vertex_grips.sync()

    def on_row_selected(self, row, col):
        if not self.history.ready():
            return 'fail'
        # log.debug("AppGerberEditor.on_row_selected() --> %s" % str(inspect.stack()[1][3]))
        key_modifier = QtWidgets.QApplication.keyboardModifiers()
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
            self.selected = []

        try:
            selected_ap_code = int(self.ui.apertures_table.item(row, 1).text())
            self.last_aperture_selected = copy(selected_ap_code)

            selected = set(self.selected)
            for obj in self.storage_dict[selected_ap_code]['geometry']:
                if obj not in selected:
                    self.selected.append(obj)
                    selected.add(obj)
        except Exception as e:
            self.app.log.error("AppGerberEditor.on_row_selected() --> %s" % str(e))

        # #########################################################################################################
        # ######################### calculate vertex numbers for all selected shapes ##############################
        # #########################################################################################################
        vertex_nr = 0
        for sha in self.selected:
            sha_geo = sha.geo
            if 'solid' in sha_geo:
                sha_geo_solid = sha_geo['solid']
                if sha_geo_solid.geom_type == 'Polygon':
                    sha_geo_solid_coords = list(sha_geo_solid.exterior.coords)
                elif sha_geo_solid.geom_type in ['LinearRing', 'LineString']:
                    sha_geo_solid_coords = list(sha_geo_solid.coords)
                else:
                    sha_geo_solid_coords = []

                vertex_nr += len(sha_geo_solid_coords)

        self.ui.geo_vertex_entry.set_value(vertex_nr)

        # #########################################################################################################
        # ######################### calculate total area for all selected shapes ##################################
        # #########################################################################################################
        t_area = 0
        for sha in self.selected:
            sha_geo = sha.geo
            if 'solid' in sha_geo:
                sha_geo_solid = sha_geo['solid']
                if sha_geo_solid.geom_type == 'Polygon':
                    t_area += sha_geo_solid.area

        self.ui.area_entry.set_value(t_area)

        self.zoom_selection()
        self.plot_selection()

    def on_table_selection(self):
        if not self.history.ready():
            return 'fail'
        # log.debug("AppGerberEditor.on_table_selection() -> %s" % str(inspect.stack()[1][3]))
        selected_rows = self.ui.apertures_table.selectionModel().selectedRows(0)

        if len(selected_rows) == self.ui.apertures_table.rowCount():
            selected = set(self.selected)
            for row in range(self.ui.apertures_table.rowCount()):
                try:
                    selected_ap_code = int(self.ui.apertures_table.item(row, 1).text())
                    self.last_aperture_selected = copy(selected_ap_code)

                    for obj in self.storage_dict[selected_ap_code]['geometry']:
                        if obj not in selected:
                            self.selected.append(obj)
                            selected.add(obj)
                except Exception as e:
                    self.app.log.error("AppGerberEditor.on_row_selected() --> %s" % str(e))

            # #########################################################################################################
            # ######################### calculate vertex numbers for all selected shapes ##############################
            # #########################################################################################################
            vertex_nr = 0
            for sha in self.selected:
                sha_geo = sha.geo
                if 'solid' in sha_geo:
                    sha_geo_solid = sha_geo['solid']
                    if sha_geo_solid.geom_type == 'Polygon':
                        sha_geo_solid_coords = list(sha_geo_solid.exterior.coords)
                    elif sha_geo_solid.geom_type in ['LinearRing', 'LineString']:
                        sha_geo_solid_coords = list(sha_geo_solid.coords)
                    else:
                        sha_geo_solid_coords = []

                    vertex_nr += len(sha_geo_solid_coords)

            self.ui.geo_vertex_entry.set_value(vertex_nr)

            # #########################################################################################################
            # ######################### calculate total area for all selected shapes ##################################
            # #########################################################################################################
            t_area = 0
            for sha in self.selected:
                sha_geo = sha.geo
                if 'solid' in sha_geo:
                    sha_geo_solid = sha_geo['solid']
                    if sha_geo_solid.geom_type == 'Polygon':
                        t_area += sha_geo_solid.area

            self.ui.area_entry.set_value(t_area)

            self.zoom_selection()
            self.plot_selection()
            return True
        return False

    @gerber_operation(_('Draw shape'))
    def on_grb_shape_complete(self, storage=None, specific_shape=None, no_plot=False):
        """

        :param storage: where to store the shape
        :param specific_shape: optional, the shape to be stored
        :param no_plot: use this if you want the added shape not plotted
        :return:
        """
        self.app.log.debug("on_grb_shape_complete()")

        if isinstance(self.active_tool, CopperPourEditorGrb):
            result = self.add_region_geometry(self.active_tool.geometry)
            self.delete_utility_geometry()
            return result

        if getattr(self.active_tool, 'name', None) in ('region', 'disc', 'semidisc'):
            self.storage_dict.setdefault(0, {'type': 'REG', 'size': 0.0, 'geometry': []})
            storage = self.storage_dict[0]['geometry']
            self.current_storage = storage
            self.last_aperture_selected = 0

        if specific_shape:
            geo = specific_shape
        else:
            geo = deepcopy(self.active_tool.geometry)
            if geo is None:
                return

        if isinstance(self.active_tool, TrackEditorGrb) and specific_shape is None:
            provisional = {shape for shape, store in self.active_tool.click_shapes}
            owners = [apid for apid, aperture in self.storage_dict.items()
                      if any(shape in provisional for shape in aperture.get('geometry', ()))]
            self.history.capture(owners)
            for apid in owners:
                shapes = self.storage_dict[apid]['geometry']
                shapes[:] = [shape for shape in shapes if shape not in provisional]

        if storage is not None:
            # Add shape
            self.add_gerber_shape(geo, storage)
        else:
            stora = self.storage_dict[self.last_aperture_selected]['geometry']
            self.add_gerber_shape(geo, storage=stora)

        # Remove any utility shapes
        self.delete_utility_geometry()
        self.tool_shape.clear(update=True)

        if no_plot is False:
            # Re-plot and reset tool.
            self.plot_all()

    def touch_geometry(self):
        """
        Every code that adds, removes, replaces or changes a shape of the storage calls this, so the pick index is
        rebuilt on the next pick.
        """
        self._snap_cache = None
        self._feature_state = None
        history = getattr(self, 'history', None)
        if isinstance(history, GerberHistory) and history.depth:
            if history.dirty:
                self._pick_cache = None
                return
            history.dirty = True
        self.geometry_revision += 1

    def get_pick_index(self):
        """
        :return: (tree, refs): tree is a STRtree on the solids (None if there is none), refs is a list of
                 (aperture_key, element_index, shape) in the same order.
        """
        count = 0
        for storage_val in self.storage_dict.values():
            count += len(storage_val.get('geometry', ()))

        cache = self._pick_cache
        if cache is not None and cache[0] == self.geometry_revision and cache[1] == count:
            return cache[2], cache[3]

        refs = []
        solids = []
        self._shape_apertures = {}
        for ap_key in sorted(self.storage_dict, key=aperture_rank):
            for idx, shape in enumerate(self.storage_dict[ap_key].get('geometry', ())):
                self._shape_apertures.setdefault(shape, set()).add(ap_key)
                geo = getattr(shape, 'geo', None)
                if not isinstance(geo, dict):
                    continue
                solid = geo.get('solid')
                if solid is None or solid.is_empty:
                    continue
                refs.append((ap_key, idx, shape))
                solids.append(solid)

        tree = STRtree(solids) if solids else None
        self._pick_cache = (self.geometry_revision, count, tree, refs)
        return tree, refs

    def pick_candidates(self, point):
        """
        All the shapes under (or near) a point, the best first.

        The tolerance is PICK_TOLERANCE_PX screen pixels. The order is: distance to the solid, area of the solid (the
        smaller first, so a small shape on a big one is the first pick), distance to the 'follow' line (a missing one
        sorts last), aperture order, element index.

        :param point:   (x, y) in plot units
        :return:        list of (shape, aperture_key)
        """
        tree, refs = self.get_pick_index()
        if tree is None:
            return []

        tol = PICK_TOLERANCE_PX * self.canvas.pixel_size()
        pt = Point(point[0], point[1])
        found, dists = tree.query_nearest(pt, max_distance=tol, return_distance=True, all_matches=True)

        keyed = []
        for pos, dist in zip(found, dists):
            ap_key, idx, shape = refs[int(pos)]
            follow = shape.geo.get('follow')
            if follow is None or follow.is_empty:
                follow_dist = float('inf')
            else:
                follow_dist = pt.distance(follow)
            key = (round(float(dist), 9), shape.geo['solid'].area, follow_dist, aperture_rank(ap_key), idx, int(pos))
            keyed.append((key, shape, ap_key))
        keyed.sort(key=lambda item: item[0])
        return [(shape, ap_key) for __, shape, ap_key in keyed]

    def pick_at(self, point):
        """
        Pick the shape under (or near) a point: the first of pick_candidates().

        :param point:   (x, y) in plot units
        :return:        (shape, aperture_key) or None
        """
        candidates = self.pick_candidates(point)
        return candidates[0] if candidates else None

    def pick_cycle_at(self, point):
        """
        Pick a shape, and go to the next candidate when the click is repeated at the same place.

        A click is a repeat when it is less than CLICK_DRAG_THRESHOLD_PX screen pixels from the last pick, the storage
        did not change, and the candidates are the same. Then the next candidate is picked (after the last one, the
        first again).

        :param point:   (x, y) in plot units
        :return:        (hit, previous, position, count): hit is (shape, aperture_key) or None; previous is the shape
                        that the last click picked when this click is a repeat, else None; position is the index of hit
                        in the candidates; count is the number of candidates
        """
        candidates = self.pick_candidates(point)
        if not candidates:
            self.pick_cycle = None
            return None, None, 0, 0

        ids = tuple(id(shape) for shape, __ in candidates)
        last = self.pick_cycle
        position = 0
        previous = None
        if last is not None and len(candidates) > 1 and last['ids'] == ids and \
                last['revision'] == self.geometry_revision:
            pixel_size = self.canvas.pixel_size()
            if pixel_size and pixel_size > 0:
                moved = math.hypot(point[0] - last['point'][0], point[1] - last['point'][1]) / pixel_size
                if moved < CLICK_DRAG_THRESHOLD_PX:
                    position = (last['position'] + 1) % len(candidates)
                    previous = candidates[last['position']][0]

        self.pick_cycle = {'point': (point[0], point[1]), 'ids': ids, 'position': position,
                           'revision': self.geometry_revision}
        return candidates[position], previous, position, len(candidates)

    def sync_aperture_rows(self, apertures):
        """
        Select in the Apertures Table the rows of the given apertures, without firing the table handlers.
        """
        table = self.ui.apertures_table
        apertures = list(apertures)
        wanted = {str(ap) for ap in apertures}
        by_text = {str(ap): ap for ap in apertures}

        with QtCore.QSignalBlocker(table), QtCore.QSignalBlocker(table.selectionModel()):
            table.clearSelection()
            if wanted:
                old_mode = table.selectionMode()
                table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
                for row in range(table.rowCount()):
                    item = table.item(row, 1)
                    if item is not None and item.text() in wanted:
                        table.selectRow(row)
                        self.last_aperture_selected = by_text[item.text()]
                table.setSelectionMode(old_mode)
        show_selected_rows(table)

    def get_snap_index(self):
        """Index FOLLOW vertices and segments once per geometry revision and editor session."""
        state = (id(self.storage_dict), self.geometry_revision, self.history.session)
        cache = getattr(self, '_snap_cache', None)
        if cache is not None and cache[0] == state:
            return cache[1:]
        points, segments = [], []

        def collect(geo):
            if geo is None or geo.is_empty:
                return
            if isinstance(geo, Point):
                points.append(geo)
            elif isinstance(geo, (LineString, LinearRing)):
                coords = [coord[:2] for coord in geo.coords]
                points.extend(Point(coord) for coord in coords)
                segments.extend(LineString((a, b)) for a, b in zip(coords, coords[1:]) if a != b)
            elif hasattr(geo, 'geoms'):
                for part in geo.geoms:
                    collect(part)

        for aperture in self.storage_dict.values():
            for shape in aperture.get('geometry', ()):
                collect(shape.geo.get('follow'))
        point_tree = STRtree(points) if points else None
        segment_tree = STRtree(segments) if segments else None
        self._snap_cache = (state, point_tree, points, segment_tree, segments)
        return self._snap_cache[1:]

    def snap(self, x, y):
        """Use the shared grid settings, but acquire only this editor's FOLLOW geometry."""
        self.snap_acquisition = None
        if isinstance(self.active_tool, TrackEditorGrb) and self.active_tool.ui.snap_to_pad_cb.isChecked():
            point = Point(x, y)
            tree, refs = self.get_pick_index()
            pads = []
            for idx in tree.query(point, predicate='intersects') if tree is not None else ():
                shape = refs[int(idx)][2]
                follow = shape.geo.get('follow')
                if isinstance(follow, Point) and not follow.is_empty:
                    pads.append((shape.geo['solid'].area, point.distance(follow), int(idx),
                                 (follow.x, follow.y)))
            if pads:
                self.active_snap_type = 'pad'
                self.ui.snap_type_label.setText('%s: %s' % (_("Snap"), _("Pad center")))
                return min(pads)[3]
        if not self.app.grid_status():
            self.active_snap_type = None
            self.ui.snap_type_label.setText('%s: %s' % (_("Snap"), _("None")))
            return x, y
        options = self.app.geo_editor.editor_options
        best = (float('inf'), 99, (x, y), None)
        corner = options.get('corner_snap', False)
        midpoint = self.ui.midpoint_snap_cb.isChecked()
        segment = self.ui.segment_snap_cb.isChecked()
        self.snap_acquisition = None
        if corner or midpoint or segment:
            point_tree, points, segment_tree, segments = self.get_snap_index()
            point = Point(x, y)
            if corner and point_tree is not None:
                radius = float(options.get('global_snap_max', 0.05))
                query = box(x - radius, y - radius, x + radius, y + radius)
                for idx in point_tree.query(query):
                    target = points[int(idx)]
                    dist = point.distance(target)
                    if dist <= radius:
                        best = min(best, (dist, 0, (target.x, target.y), 'corner'))
            if midpoint or segment:
                radius = self.ui.snap_radius_entry.get_value()
                try:
                    screen = np.asarray(self.canvas.translate_coords_2((x, y))[:2], dtype=float)
                    corners = np.asarray([self.canvas.translate_coords((screen[0] + dx, screen[1] + dy))[:2]
                                          for dx, dy in ((-radius, -radius), (-radius, radius),
                                                         (radius, -radius), (radius, radius))], dtype=float)
                    if not np.isfinite(screen).all() or not np.isfinite(corners).all():
                        raise ValueError
                    query = box(*corners.min(axis=0), *corners.max(axis=0))
                    self.snap_acquisition = 'pixels'
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    radius = float(options.get('global_snap_max', 0.05))
                    query = box(x - radius, y - radius, x + radius, y + radius)
                    self.snap_acquisition = 'world'
                for idx in segment_tree.query(query) if segment_tree is not None else ():
                    line = segments[int(idx)]
                    candidates = []
                    if midpoint:
                        candidates.append((line.interpolate(0.5, normalized=True), 1, 'midpoint'))
                    if segment:
                        candidates.append((line.interpolate(line.project(point)), 2, 'segment'))
                    for target, priority, kind in candidates:
                        coords = (target.x, target.y)
                        acquisition = distance(screen, self.canvas.translate_coords_2(coords)[:2]) \
                            if self.snap_acquisition == 'pixels' else point.distance(target)
                        if acquisition <= radius:
                            best = min(best, (point.distance(target), priority, coords, kind))
        if options.get('grid_snap', False):
            gx = float(options['global_gridx'])
            gy = gx if self.app.ui.grid_gap_link_cb.isChecked() else float(options['global_gridy'])
            if isinstance(self.active_tool, TrackEditorGrb) and getattr(self.active_tool, 'fine_grid', False):
                gx /= 10
                gy /= 10
            coords = (round(x / gx) * gx if gx else x, round(y / gy) * gy if gy else y)
            best = min(best, (distance((x, y), coords), 3, coords, 'grid'))
        self.active_snap_type = best[3]
        names = {None: _("None"), 'corner': _("Endpoint / bend"), 'midpoint': _("Midpoint"),
                 'segment': _("Nearest FOLLOW segment"), 'grid': _("Grid")}
        text = '%s: %s' % (_("Snap"), names[best[3]])
        if self.snap_acquisition == 'world':
            text += ' — ' + _("Compatibility: model units (global_snap_max)")
        self.ui.snap_type_label.setText(text)
        return best[2]

    def refresh_features(self):
        """List aperture records without changing aperture-table selection semantics."""
        table = self.ui.features_table
        state = (id(self.storage_dict), self.geometry_revision, self.history.session)
        with QtCore.QSignalBlocker(table), QtCore.QSignalBlocker(table.selectionModel()):
            if getattr(self, '_feature_state', None) != state:
                self._feature_refs = [(apid, shape) for apid in sorted(self.storage_dict, key=aperture_rank)
                                      for shape in self.storage_dict[apid].get('geometry', ())]
                # one repaint for the whole rebuild, not one per cell
                table.setUpdatesEnabled(False)
                table.setRowCount(len(self._feature_refs))
                for row, (apid, shape) in enumerate(self._feature_refs):
                    solid, follow = shape.geo.get('solid'), shape.geo.get('follow')
                    values = (str(apid), str(row + 1),
                              ', '.join('%s: %s' % (key, shape.geo[key].geom_type)
                                        for key in ('solid', 'follow', 'clear') if shape.geo.get(key) is not None),
                              '' if solid is None else '%.*f' % (self.decimals, solid.area),
                              '' if follow is None else '%.*f' % (self.decimals, follow.length))
                    for col, value in enumerate(values):
                        table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
                table.setUpdatesEnabled(True)
                self._feature_state = state
            selected = set(self.selected)
            table.clearSelection()
            selection = QtCore.QItemSelection()
            for row, (apid, shape) in enumerate(self._feature_refs):
                if shape in selected:
                    selection.select(table.model().index(row, 0), table.model().index(row, table.columnCount() - 1))
            table.selectionModel().select(selection, QtCore.QItemSelectionModel.SelectionFlag.Select)
        # the panel stays on the aperture row (sync_aperture_rows); the feature table scrolls inside itself
        show_selected_rows(table, panel=False)

    def on_feature_selection(self):
        if not self.history.ready():
            self.refresh_features()
            return 'fail'
        matches = [self._feature_refs[index.row()] for index in self.ui.features_table.selectionModel().selectedRows()]
        self.selected = list(dict.fromkeys(shape for apid, shape in matches))
        selected = set(self.selected)
        self.sync_aperture_rows(apid for apid, shape in self._feature_refs if shape in selected)
        self.update_ui()
        self.plot_selection()

    def size_filter_matches(self, whole_board=False):
        """
        Validate every enabled filter before changing selection or entering history.
        Select searches the whole board (whole_board=True), so repeating it never only narrows the selection.
        Delete acts on the selected shapes if any, else on all the shapes.
        """
        dimensions = {}
        ranges = {}
        try:
            for key, check, entry in (
                    ('width', self.ui.size_width_chk, self.ui.size_width_entry),
                    ('height', self.ui.size_height_chk, self.ui.size_height_entry)):
                if check.isChecked():
                    value = float(entry.text().strip().replace(',', '.'))
                    if not math.isfinite(value) or value < 0:
                        raise ValueError
                    dimensions[key] = value
            tolerance = float(self.ui.size_tolerance_entry.text().strip().replace(',', '.'))
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError
            for key in ('area', 'length'):
                if getattr(self.ui, 'size_%s_chk' % key).isChecked():
                    low = float(getattr(self.ui, 'size_%s_min_entry' % key).text().strip().replace(',', '.'))
                    high = float(getattr(self.ui, 'size_%s_max_entry' % key).text().strip().replace(',', '.'))
                    if not math.isfinite(low) or not math.isfinite(high) or low < 0 or high < low:
                        raise ValueError
                    ranges[key] = (low, high)
        except (ValueError, TypeError, OverflowError):
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Dimensions, range bounds and tolerance must be finite and non-negative. Minimum must not exceed maximum."))
            return 'fail'
        if not dimensions and not ranges:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Enable at least one size filter."))
            return 'fail'

        scope = set(self.selected) if self.selected and not whole_board else None
        matches = []
        matched = set()
        for apid, aperture in self.storage_dict.items():
            for shape in aperture.get('geometry', ()):
                if scope is not None and shape not in scope:
                    continue
                solid = shape.geo.get('solid')
                if (dimensions or 'area' in ranges) and (solid is None or solid.is_empty):
                    continue
                sizes = {}
                if dimensions:
                    xmin, ymin, xmax, ymax = solid.bounds
                    sizes.update(width=xmax - xmin, height=ymax - ymin)
                if 'area' in ranges:
                    sizes['area'] = solid.area
                if 'length' in ranges:
                    follow = shape.geo.get('follow')
                    if follow is None or follow.is_empty:
                        continue
                    sizes['length'] = follow.length
                if all(math.isfinite(sizes[key]) and abs(sizes[key] - value) <= tolerance
                       for key, value in dimensions.items()) and all(
                        math.isfinite(sizes[key]) and low <= sizes[key] <= high
                        for key, (low, high) in ranges.items()):
                    if shape not in matched:
                        matches.append(shape)
                        matched.add(shape)
        return matches

    def apply_path_split(self, point, tolerance, target=None, session=None, revision=None, state=None):
        return apply_path_split(self, point, tolerance, target, session, revision, state)

    def apply_path_join(self, tolerance):
        return apply_path_join(self, tolerance)

    def on_join_paths(self):
        return self.apply_path_join(self.ui.join_tolerance_entry.text())

    def apply_resize_circles(self, diameter):
        return apply_resize_circles(self, diameter)

    def on_resize_circles(self):
        return self.apply_resize_circles(self.ui.resize_circles_entry.get_value())

    def remove_editor_actions(self):
        """Remove the menu actions of a previous Gerber editor and cut their connections."""
        menu = self.app.ui.grb_editor_menu
        for action in menu.actions():
            if action.property(EDITOR_ACTION_PROPERTY):
                try:
                    action.triggered.disconnect()
                except (TypeError, RuntimeError):
                    pass
                menu.removeAction(action)
                action.deleteLater()

    def reject_busy(self):
        """True (with a status message) while a save or a worker holds the history."""
        return not self.history.ready()

    def apply_vertex_edit(self, action, index, point=None, target=None, on_segment=True):
        return apply_vertex_edit(self, action, index, point, target, on_segment)

    def on_edit_vertices(self):
        return edit_vertices_dialog(self)

    def on_select_by_size(self):
        matches = self.size_filter_matches(whole_board=True)
        if matches == 'fail':
            return 'fail'
        self.selected = matches
        selected = set(matches)
        self.sync_aperture_rows(apid for apid, aperture in self.storage_dict.items()
                                if any(shape in selected for shape in aperture.get('geometry', ())))
        self.update_ui()
        self.plot_selection()
        self.app.inform.emit(_("{n} shapes match").format(n=len(matches)))
        return matches

    def on_delete_by_size(self):
        matches = self.size_filter_matches()
        if matches == 'fail':
            return 'fail'
        if not matches:
            self.app.inform.emit(_("{n} shapes match").format(n=0))
            return []
        with self.history.operation(_("Delete by Size")) as accepted:
            if not accepted:
                return 'fail'
            self.selected = matches
            self.delete_selected()
            self.history.needs_plot = True
        return matches

    @QtCore.pyqtSlot(object)
    def apply_simplification(self, result):
        if self.history.pending is not result.get('pending'):
            return
        self.history.pending = None
        try:
            return self._apply_simplification(result)
        finally:
            self.history.notify()

    @gerber_operation(_('Simplify'))
    def _apply_simplification(self, result):
        """Commit a completed batch on the GUI thread, only if its source is still current."""
        if result.get('error') is not None:
            self.app.log.error("Gerber simplification failed: %s" % result['error'])
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed. Geometry was not changed."))
            return
        if result['storage'] is not self.storage_dict or result['revision'] != self.geometry_revision:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Geometry changed. Retry simplification."))
            return
        updates = result['updates']
        for apid, storage, shapes, replacements in updates:
            if self.storage_dict.get(apid, {}).get('geometry') is not storage or tuple(storage) != shapes:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Geometry changed. Retry simplification."))
                return
        for apid, storage, shapes, replacements in updates:
            storage[:] = replacements
        self.selected = [result['replacements'].get(shape, shape) for shape in self.selected]
        self.is_modified = True
        self.touch_geometry()
        selected = set(self.selected)
        self.sync_aperture_rows(apid for apid, aperture in self.storage_dict.items()
                                if any(shape in selected for shape in aperture.get('geometry', ())))
        self.plot_all()
        if isinstance(self.active_tool, SimplifyEditorGrb):
            self.active_tool.simp_tool.calculate_coords_vertex()

    def _tool_wants_raw_pick(self):
        """
        True when the active tool uses the click to pick a shape: that click is never grid snapped.
        """
        tool = self.active_tool
        if tool is None:
            return False
        wants = getattr(tool, 'wants_raw_pick', None)
        if callable(wants):
            return bool(wants())
        return bool(getattr(tool, 'uses_raw_pick', False))

    def _pixel_distance_from_press(self, pos):
        """
        :return: the distance in screen pixels between the last mouse press and pos, None if it is not known
        """
        if self.press_pos_raw is None:
            return None
        pixel_size = self.canvas.pixel_size()
        if not pixel_size or pixel_size <= 0:
            return None
        return math.hypot(pos[0] - self.press_pos_raw[0], pos[1] - self.press_pos_raw[1]) / pixel_size

    def _is_click_not_drag(self, pos):
        dist = self._pixel_distance_from_press(pos)
        return dist is not None and dist < CLICK_DRAG_THRESHOLD_PX

    @gerber_operation(_('Add shapes'))
    def add_gerber_shape(self, shape_element, storage):
        """
        Adds a shape to the shape storage.

        :param shape_element: Shape to be added.
        :type shape_element: DrawToolShape or DrawToolUtilityShape Geometry is stored as a dict with keys: solid,
        follow, clear, each value being a list of Shapely objects. The dict can have at least one of the mentioned keys
        :param storage: Where to store the shape
        :return: None
        """
        # List of DrawToolShape?

        if isinstance(shape_element, list):
            for subshape in shape_element:
                self.add_gerber_shape(subshape, storage)
            return

        assert isinstance(shape_element, DrawToolShape), \
            "Expected a DrawToolShape, got %s" % str(type(shape_element))

        assert shape_element.geo is not None, \
            "Shape object has empty geometry (None)"

        assert (isinstance(shape_element.geo, list) and len(shape_element.geo) > 0) or not \
            isinstance(shape_element.geo, list), "Shape objects has empty geometry ([])"

        if isinstance(shape_element, DrawToolUtilityShape):
            self.utility.append(shape_element)
        else:
            storage.append(shape_element)
            self.touch_geometry()

    def apply_selection_drag(self, geometries, offset=None):
        return apply_selection_drag(self, geometries, offset=offset)

    def on_canvas_click(self, event):
        """
        event.pos has canvas coordinates, translated below to plot coordinates.

        :param event: Event object dispatched by VisPy
        :return: None
        """
        if not self.history.ready():
            return 'fail'
        event_pos = event.pos
        self.pos = self.canvas.translate_coords(event_pos)
        self.press_pos_raw = (self.pos[0], self.pos[1])
        raw_pick = self._tool_wants_raw_pick()
        if isinstance(self.active_tool, TrackEditorGrb):
            snapped_pos = self.snap(*self.press_pos_raw)
            self.snap_x, self.snap_y = snapped_pos
        elif self.app.grid_status():
            snapped_pos = self.app.geo_editor.snap(self.pos[0], self.pos[1])
        else:
            snapped_pos = (self.pos[0], self.pos[1])
        # a press on a vertex grip starts a vertex drag and never reaches the tool (the Select tool would deselect)
        if event.button == 1:
            origin = self.press_pos_raw if raw_pick else snapped_pos
            self._delta_origin = tuple(origin)
            self.app.dx = self.app.dy = 0
            self.app.mouse_pos = list(origin)
            self.app.ui.update_location_labels(0, 0, *origin)
            self.app.plotcanvas.on_update_text_hud(0, 0, *origin)
            if self.vertex_grips.press(event_pos, self.pos, QtWidgets.QApplication.keyboardModifiers()):
                return
        else:
            self.vertex_grips.cancel()
        # a click that picks a shape uses the raw position, all the others the snapped one
        self.pos = self.press_pos_raw if raw_pick else snapped_pos

        if event.button == 1:
            # Selection with left mouse button
            if self.active_tool is not None:
                modifiers = QtWidgets.QApplication.keyboardModifiers()

                # If the SHIFT key is pressed when LMB is clicked then the coordinates are copied to clipboard
                if modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
                    self.app.clipboard.setText(
                        self.app.options.get(
                            "global_point_clipboard_format",
                            self.app.defaults.get(
                                "global_point_clipboard_format",
                                self.app.defaults.factory_defaults.get("global_point_clipboard_format")
                            )
                        ) %
                        (self.decimals, snapped_pos[0], self.decimals, snapped_pos[1])
                    )
                    self.app.inform.emit('[success] %s' % _("Copied to clipboard."))
                    return

                # Dispatch event to active_tool
                if raw_pick or isinstance(self.active_tool, TrackEditorGrb):
                    self.active_tool.click(self.pos)
                else:
                    self.active_tool.click(self.app.geo_editor.snap(self.pos[0], self.pos[1]))

                # If it is a shape generating tool
                if isinstance(self.active_tool, ShapeToolEditorGrb) and self.active_tool.complete:
                    if self.current_storage is not None:
                        self.on_grb_shape_complete(self.current_storage)
                        self.build_ui()

                    # MS: always return to the Select Tool if modifier key is not pressed
                    # else return to the current tool
                    key_modifier = QtWidgets.QApplication.keyboardModifiers()
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
                    if key_modifier == modifier_to_use:
                        self.select_tool(self.active_tool.name)
                    else:
                        # return to Select tool but not for PadEditorGrb
                        if isinstance(self.active_tool, PadEditorGrb):
                            self.select_tool(self.active_tool.name)
                        else:
                            self.select_tool("select")
                        return

                # if isinstance(self.active_tool, SelectEditorGrb):
                #     self.plot_all()
            else:
                self.app.log.debug("No active tool to respond to click!")

    def on_canvas_click_release(self, event):
        if not self.history.ready():
            self.vertex_grips.cancel()
            return 'fail'
        self.modifiers = QtWidgets.QApplication.keyboardModifiers()
        event_pos = event.pos
        right_button = 2

        pos_canvas = self.canvas.translate_coords(event_pos)
        raw_pos = (pos_canvas[0], pos_canvas[1])
        if event.button == 1 and self.vertex_grips.release(event_pos, pos_canvas):
            self.press_pos_raw = None
            self.app.selection_type = None
            return
        if self.app.grid_status() and not self._tool_wants_raw_pick():
            pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
        else:
            pos = raw_pos

        if isinstance(self.active_tool, (TrackBendEditorGrb, FollowSplitEditorGrb)):
            if event.button == 1:
                self.active_tool.click_release(raw_pos)
            elif event.button == right_button and not event.is_dragging:
                self.active_tool.clean_up()
            self.app.selection_type = None
            return

        # if the released mouse button was RMB then test if it was a panning motion or not, if not it was a context
        # canvas menu
        try:
            if event.button == right_button:  # right click
                if self.app.ui.popMenu.mouse_is_panning is False:
                    if self.in_action is False:
                        try:
                            QtGui.QGuiApplication.restoreOverrideCursor()
                        except Exception as e:
                            self.app.log.error("AppGerberEditor.on_grb_click_release() --> %s" % str(e))

                        if self.active_tool.complete is False:
                            if not isinstance(self.active_tool, SelectEditorGrb):
                                self.active_tool.complete = True
                                self.in_action = False
                                self.delete_utility_geometry()
                                self.app.inform.emit('[success] %s' % _("Done."))
                                self.select_tool('select')
                        else:
                            self.app.cursor = QtGui.QCursor()
                            self.app.populate_cmenu_grids()
                            self.app.ui.popMenu.popup(self.app.cursor.pos())
                    else:
                        # if right click on canvas and the active tool need to be finished (like Path or Polygon)
                        # right mouse click will finish the action
                        if isinstance(self.active_tool, ShapeToolEditorGrb):
                            if isinstance(self.active_tool, TrackEditorGrb):
                                self.active_tool.make()
                            else:
                                self.active_tool.click(self.app.geo_editor.snap(self.x, self.y))
                                self.active_tool.make()

                            if self.active_tool.complete:
                                self.on_grb_shape_complete()
                                self.app.inform.emit('[success] %s' % _("Done."))

                                # MS: always return to the Select Tool if modifier key is not pressed
                                # else return to the current tool but not for TrackEditorGrb

                                if isinstance(self.active_tool, TrackEditorGrb):
                                    self.select_tool(self.active_tool.name)
                                else:
                                    key_modifier = QtWidgets.QApplication.keyboardModifiers()
                                    if (self.app.options.get(
                                            "global_mselect_key",
                                            self.app.defaults.get(
                                                "global_mselect_key",
                                                self.app.defaults.factory_defaults.get("global_mselect_key")
                                            )) == 'Control' and
                                        key_modifier == Qt.KeyboardModifier.ControlModifier) or \
                                            (self.app.options.get(
                                                "global_mselect_key",
                                                self.app.defaults.get(
                                                    "global_mselect_key",
                                                    self.app.defaults.factory_defaults.get("global_mselect_key")
                                                )) == 'Shift' and
                                             key_modifier == Qt.KeyboardModifier.ShiftModifier):

                                        self.select_tool(self.active_tool.name)
                                    else:
                                        self.select_tool("select")
        except Exception as e:
            self.app.log.error("AppGerberEditor.on_grb_click_release() RMB click --> Error: %s" % str(e))
            raise

        # if the released mouse button was LMB then test if we had a right-to-left selection or a left-to-right
        # selection and then select a type of selection ("enclosing" or "touching")
        try:
            if event.button == 1:  # left click
                # a small move between the press and the release is a click, not a drag
                if self.app.selection_type is not None and self._is_click_not_drag(raw_pos):
                    self.app.selection_type = None
                    self.app.delete_selection_shape()

                if self.app.selection_type is not None:
                    self.draw_selection_area_handler(self.pos, pos, self.app.selection_type)
                    self.app.selection_type = None
                    if isinstance(self.active_tool, SimplifyEditorGrb):
                        self.active_tool.simp_tool.calculate_coords_vertex()

                elif isinstance(self.active_tool, (SelectEditorGrb, SimplifyEditorGrb)):
                    self.active_tool.click_release((self.pos[0], self.pos[1]))

                    # # if there are selected objects then plot them
                    # if self.selected:
                    #     self.plot_all()
        except Exception as e:
            self.app.log.error("AppGerberEditor.on_grb_click_release() LMB click --> Error: %s" % str(e))
            raise

    def draw_selection_area_handler(self, start_pos, end_pos, sel_type):
        """
        :param start_pos:   mouse position when the selection LMB click was done
        :param end_pos:     mouse position when the left mouse button is released
        :param sel_type:    if True it's a left to right selection (enclosure), if False it's a 'touch' selection
        :return:
        """

        poly_selection = Polygon([start_pos, (end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])
        self.app.delete_selection_shape()

        tree, refs = self.get_pick_index()
        # A dict preserves selection order and avoids repeated searches/removals in a growing list.
        selected = dict.fromkeys(self.selected)
        toggle = self.key == self.app.options.get(
            "global_mselect_key",
            self.app.defaults.get(
                "global_mselect_key",
                self.app.defaults.factory_defaults.get("global_mselect_key")
            ))
        if tree is not None:
            predicate = 'contains' if sel_type is True else 'intersects'
            candidates = tree.query(poly_selection, predicate=predicate)
            # STRtree results are unordered; keep the original aperture/element order.
            aperture_order = {ap: index for index, ap in enumerate(self.storage_dict)}
            for index in sorted(candidates, key=lambda i: (aperture_order[refs[i][0]], refs[i][1])):
                obj = refs[index][2]
                if toggle and obj in selected:
                    del selected[obj]
                else:
                    selected[obj] = None
        self.selected = list(selected)

        # #############################################################################################################
        # ##########  select the aperture code of the selected geometry, in the tool table  ###########################
        # #############################################################################################################
        sel_aperture = {ap for obj in self.selected for ap in self._shape_apertures.get(obj, ())}
        self.sync_aperture_rows(sel_aperture)

        # #############################################################################################################
        # ######################### calculate vertex numbers for all selected shapes ##################################
        # #############################################################################################################
        vertex_nr = 0
        for sha in self.selected:
            sha_geo = sha.geo
            if 'solid' in sha_geo:
                sha_geo_solid = sha_geo['solid']
                if sha_geo_solid.geom_type == 'Polygon':
                    sha_geo_solid_coords = list(sha_geo_solid.exterior.coords)
                elif sha_geo_solid.geom_type in ['LinearRing', 'LineString']:
                    sha_geo_solid_coords = list(sha_geo_solid.coords)
                else:
                    sha_geo_solid_coords = []

                vertex_nr += len(sha_geo_solid_coords)

        self.ui.geo_vertex_entry.set_value(vertex_nr)

        # #############################################################################################################
        # ######################### calculate total area for all selected shapes ######################################
        # #############################################################################################################
        t_area = 0
        for sha in self.selected:
            sha_geo = sha.geo
            if 'solid' in sha_geo:
                sha_geo_solid = sha_geo['solid']
                if sha_geo_solid.geom_type == 'Polygon':
                    t_area += sha_geo_solid.area

        self.ui.area_entry.set_value(t_area)

        self.plot_selection()

    def on_canvas_key_release(self, event):
        if isinstance(self.active_tool, TrackEditorGrb) and event.key in ('Space', QtCore.Qt.Key.Key_Space):
            native = getattr(event, 'native', None)
            if hasattr(native, 'isAutoRepeat') and native.isAutoRepeat():
                return
            self.active_tool.set_fine_grid(False)

    def on_canvas_move(self, event):
        """
        Called on 'mouse_move' event

        event.pos have canvas screen coordinates

        :param event: Event object dispatched by VisPy SceneCavas
        :return: None
        """

        if not self.app.plotcanvas.native.hasFocus():
            self.app.plotcanvas.native.setFocus()

        event_pos = event.pos
        event_is_dragging = event.is_dragging
        right_button = 2

        pos_canvas = self.canvas.translate_coords(event_pos)
        self.x, self.y = pos_canvas[0], pos_canvas[1]

        self.app.ui.popMenu.mouse_is_panning = False

        # if the RMB is clicked and mouse is moving over plot then 'panning_action' is True
        if event.button == right_button and event_is_dragging == 1:
            self.app.ui.popMenu.mouse_is_panning = True
            return

        try:
            x = float(self.x)
            y = float(self.y)
        except TypeError:
            return

        if self.active_tool is None:
            return

        # # ## Snap coordinates
        track = isinstance(self.active_tool, TrackEditorGrb)
        if not self.app.grid_status() and not track:
            self.snap(x, y)
        if self.app.grid_status() or track:
            snap_x, snap_y = self.snap(x, y) if track else self.app.geo_editor.snap(x, y)
            # the rubber band of a pick tool uses the same raw position as the release
            if not self._tool_wants_raw_pick():
                x, y = snap_x, snap_y

            # Update cursor
            self.app.app_cursor.set_data(np.asarray([(snap_x, snap_y)]), symbol='++', edge_color=self.app.plotcanvas.cursor_color,
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

        self.app.mouse_pos = [x, y]

        if self.pos is None:
            self.pos = (0, 0)
        origin = getattr(self, '_delta_origin', (0, 0))
        self.app.dx = x - origin[0]
        self.app.dy = y - origin[1]

        # # update the position label in the infobar since the APP mouse event handlers are disconnected
        # self.app.ui.position_label.setText("&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
        #                                    "<b>Y</b>: %.4f&nbsp;" % (x, y))
        #
        # # update the reference position label in the infobar since the APP mouse event handlers are disconnected
        # self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
        #                                        "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (self.app.dx, self.app.dy))
        self.app.ui.update_location_labels(self.app.dx, self.app.dy, x, y)

        # units = self.app.app_units.lower()
        # self.app.plotcanvas.text_hud.text = \
        #     'Dx:\t{:<.4f} [{:s}]\nDy:\t{:<.4f} [{:s}]\n\nX:  \t{:<.4f} [{:s}]\nY:  \t{:<.4f} [{:s}]'.format(
        #         self.app.dx, units, self.app.dy, units, x, units, y, units)
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, x, y)

        self.update_utility_geometry(data=(x, y))
        if self.active_tool.name in [
            'pad', 'array', 'track', 'region'
        ]:
            try:
                self.active_tool.draw_cursor_data(pos=self.app.mouse_pos)
            except AttributeError:
                # this can happen if the method is not implemented yet for the active_tool
                pass

        # a vertex grip drag: no rubber band selection
        if self.vertex_grips.move(event_pos, pos_canvas):
            self.app.selection_type = None
            return

        # # ## Selection area on canvas section # ##
        if event_is_dragging == 1 and event.button == 1:
            # I make an exception for RegionEditorGrb and TrackEditorGrb because clicking and dragging while making 
            # regions can create strange issues like missing a point in a track/region
            if isinstance(self.active_tool, (RegionEditorGrb, TrackEditorGrb, TrackBendEditorGrb,
                                             FollowSplitEditorGrb, ArcEditorGrb, RegionDrawingPanel)):
                self.app.selection_type = None
            elif self._is_click_not_drag((pos_canvas[0], pos_canvas[1])):
                self.app.selection_type = None
                self.app.delete_selection_shape()
            else:
                dx = pos_canvas[0] - self.pos[0]
                if dx < 0:
                    self.app.draw_moving_selection_shape((self.pos[0], self.pos[1]), (x, y),
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
                    self.app.draw_moving_selection_shape((self.pos[0], self.pos[1]), (x, y))
                    self.app.selection_type = True
        else:
            self.app.selection_type = None

    def update_utility_geometry(self, data):
        # # ## Utility geometry (animated)
        geo = self.active_tool.utility_geometry(data=data)

        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            # Remove any previous utility shape
            self.tool_shape.clear(update=False)
            self.draw_utility_geometry(geo_shape=geo)
        elif getattr(self.active_tool, 'clear_utility_on_none', False):
            # an invalid drawing input must not leave the last valid preview on the canvas
            self.tool_shape.clear(update=True)

    def draw_utility_geometry(self, geo_shape):
        # it's a DrawToolShape therefore it stores his geometry in the geo attribute
        geometry = geo_shape.geo

        try:
            for el in geometry:
                geometric_data = el['solid']
                # Add the new utility shape
                self.tool_shape.add(
                    shape=geometric_data, color=self.get_draw_color(),
                    # face_color=self.app.options['global_alt_sel_fill'],
                    update=False, layer=0, tolerance=None
                )
        except TypeError:
            geometric_data = geometry['solid']
            # Add the new utility shape
            self.tool_shape.add(
                shape=geometric_data,
                color=self.get_draw_color(),
                # face_color=self.app.options['global_alt_sel_fill'],
                update=False, layer=0, tolerance=None
            )

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

    def plot_selection(self):
        """Update selection colors without rebuilding the unchanged geometry buffers."""
        self.refresh_features()
        count = sum(len(storage.get('geometry', ())) for storage in self.storage_dict.values())
        if self._render_state != (self.geometry_revision, count):
            # Selection after an unplotted edit is a recovery path, not a structural commit.
            self._render_state = None
            self.plot_all()
            return

        selected = set(self.selected)
        for changed, color in (
                (self._rendered_selection - selected, self.get_draw_color()),
                (selected - self._rendered_selection, self.get_sel_color())):
            indexes = {index for shape in changed for index in self._rendered_shapes.get(shape, ())}
            if indexes:
                # Match the alpha applied by plot_shape() to solid geometry.
                color = color[:7] + 'AF'
                self.shapes.redraw(indexes=indexes, update_colors=(color, color))
        self._rendered_selection = selected
        self.vertex_grips.sync()

    def plot_all(self):
        """
        Plots all shapes in the editor.

        :return: None
        """
        history = getattr(self, 'history', None)
        if isinstance(history, GerberHistory) and (history.depth or history.replaying):
            history.needs_plot = True
            return
        with self.app.proc_container.new('%s ...' % _("Plotting")):
            selected = set(self.selected)

            if len(self.get_sel_color()) == 7:
                sel_draw_color = self.get_sel_color() + 'FF'
            else:
                sel_draw_color = self.get_sel_color()[:-2] + 'FF'

            if len(self.get_draw_color()) == 7:
                draw_color = self.get_draw_color() + 'FF'
            else:
                draw_color = self.get_draw_color()[:-2] + 'FF'

            style = (self.shapes, draw_color, sel_draw_color, self.tolerance)
            previous = getattr(self, '_rendered_geometry', {})
            reusable = self._render_state is not None and getattr(self, '_render_style', None) == style and \
                not self.utility and not getattr(self, '_render_had_utility', False)
            current = {elem: elem.geo.get('solid') for storage in self.storage_dict.values()
                       for elem in storage.get('geometry', ())}
            if reusable:
                # Color updates refer to the existing merged mesh. Apply them before changing its membership.
                for changed, color in ((self._rendered_selection - selected, draw_color),
                                       (selected - self._rendered_selection, sel_draw_color)):
                    indexes = {index for elem in changed for index in self._rendered_shapes.get(elem, ())}
                    if indexes:
                        self.shapes.redraw(indexes=indexes, update_colors=(color[:7] + 'AF', color[:7] + 'AF'))
                for elem in list(self._rendered_shapes):
                    if elem not in current or previous.get(elem) is not current[elem]:
                        for index in self._rendered_shapes.pop(elem):
                            self.shapes.remove(index)
            else:
                self._rendered_shapes.clear()
                self.shapes.clear(update=True)
            self._render_state = None

            for storage in self.storage_dict:
                # fix for apertures with no geometry inside
                if 'geometry' in self.storage_dict[storage]:
                    for elem in self.storage_dict[storage]['geometry']:
                        if elem in self._rendered_shapes:
                            continue
                        if 'solid' in elem.geo:
                            geometric_data = elem.geo['solid']
                            if geometric_data is None or geometric_data.is_empty:
                                continue

                            if elem in selected:
                                indexes = self.plot_shape(geometry=geometric_data, color=sel_draw_color, linewidth=2)
                            else:
                                indexes = self.plot_shape(geometry=geometric_data, color=draw_color)
                            if indexes:
                                self._rendered_shapes.setdefault(elem, []).extend(indexes)

            if self.utility:
                for elem in self.utility:
                    geometric_data = elem.geo['solid']
                    self.plot_shape(geometry=geometric_data, linewidth=1)
                    continue

            self.shapes.redraw()
            self._rendered_selection = selected
            self._rendered_geometry = current
            self._render_style = style
            self._render_had_utility = bool(self.utility)
            count = sum(len(storage.get('geometry', ())) for storage in self.storage_dict.values())
            self._render_state = (self.geometry_revision, count)
        self.vertex_grips.sync()

    def plot_transformed(self, shapes, matrix=None):
        """
        Call after the geometry of the shapes was changed in place (transform actions). Only these shapes are plotted
        again, with no plot_all(): an affine change (matrix is a shapely 6-tuple) maps the triangulated buffers of the
        shapes, any other change removes the plotted shapes and adds the new ones. If the canvas was not in sync with
        the storage before the change, it does 1 plot_all(). The selection is kept.

        :param shapes:  the changed DrawToolShape
        :param matrix:  the affine matrix that was applied, or None
        :return:        None
        """
        count = sum(len(storage.get('geometry', ())) for storage in self.storage_dict.values())
        synced = self._render_state == (self.geometry_revision, count)
        self.is_modified = True
        self.touch_geometry()
        if not synced:
            history = getattr(self, 'history', None)
            if isinstance(history, GerberHistory) and history.depth:
                history.needs_plot = True
            else:
                self.plot_all()
            return

        keys = [index for shape in shapes for index in self._rendered_shapes.get(shape, ())]
        transform_keys = getattr(self.shapes, 'transform_keys', None)
        if matrix is not None and transform_keys is not None and transform_keys(keys, matrix, update=True) is True:
            if hasattr(self, '_rendered_geometry'):
                self._rendered_geometry.update((shape, shape.geo.get('solid')) for shape in shapes)
            self._render_state = (self.geometry_revision, count)
            self.vertex_grips.sync()
            return

        selected = set(self.selected)
        sel_color = self.get_sel_color()
        sel_color = sel_color + 'FF' if len(sel_color) == 7 else sel_color[:-2] + 'FF'
        draw_color = self.get_draw_color()
        draw_color = draw_color + 'FF' if len(draw_color) == 7 else draw_color[:-2] + 'FF'
        for key in keys:
            self.shapes.remove(key)
        for shape in shapes:
            self._rendered_shapes.pop(shape, None)
            solid = shape.geo.get('solid')
            if solid is None or solid.is_empty:
                continue
            if shape in selected:
                indexes = self.plot_shape(geometry=solid, color=sel_color, linewidth=2)
            else:
                indexes = self.plot_shape(geometry=solid, color=draw_color)
            if indexes:
                self._rendered_shapes.setdefault(shape, []).extend(indexes)
        self.shapes.redraw()
        if hasattr(self, '_rendered_geometry'):
            self._rendered_geometry.update((shape, shape.geo.get('solid')) for shape in shapes)
        self._render_state = (self.geometry_revision, count)
        self.vertex_grips.sync()

    def plot_shape(self, geometry=None, color='#000000FF', linewidth=1):
        """
        Plots a geometric object without rendering and returns its collection indexes.

        The canvas draws only a Polygon, a LineString or a LinearRing: a multipart geometry (a region that the
        Eraser cut in 2, for example) is drawn part by part.

        :param geometry:    Geometry to be plotted (Any "Shapely.geom" kind or list of such)
        :param color:       Shape color
        :param linewidth:   Width of lines in # of pixels.
        :return:            List of collection indexes (empty for a point).
        """

        if geometry is None:
            geometry = self.active_tool.geometry

        try:
            return [self.shapes.add(shape=geometry.geo, color=color, face_color=color, layer=0,
                                    tolerance=self.tolerance)]
        except AttributeError:
            if len(color) == 9:
                color = color[:7] + 'AF'
            return [self.shapes.add(shape=part, color=color, face_color=color, layer=0, tolerance=self.tolerance)
                    for part in shapely.get_parts(geometry) if not isinstance(part, Point) and not part.is_empty]

    def on_shape_complete(self):
        pass

    @property
    def visible(self):
        return self.shapes.visible

    @visible.setter
    def visible(self, value, threaded=True):
        log.debug("FlatCAMObj.visible()")

        current_visibility = self.shapes.visible

        # self.shapes.visible = value   # maybe this is slower in VisPy? use enabled property?

        def task(visibility):
            if visibility is True:
                if value is False:
                    self.shapes.visible = False
            else:
                if value is True:
                    self.shapes.visible = True

            # Not all object types has annotations
            try:
                self.ma_annotation.visible = value
            except Exception:
                pass

        if threaded:
            self.app.worker_task.emit({'fcn': task, 'params': [current_visibility]})
        else:
            task(current_visibility)

    # def start_delayed_plot(self, check_period):
    #     """
    #     This function starts an QTImer, and it will periodically check if all the workers finish the plotting functions
    #
    #     :param check_period: time at which to check periodically if all plots finished to be plotted
    #     :return:
    #     """
    #
    #     # self.plot_thread = threading.Thread(target=lambda: self.check_plot_finished(check_period))
    #     # self.plot_thread.start()
    #     self.app.log.debug("AppGerberEditor --> Delayed Plot started.")
    #     self.plot_thread = QtCore.QTimer()
    #     self.plot_thread.setInterval(check_period)
    #     self.plot_finished.connect(self.setup_ui_after_delayed_plot)
    #     self.plot_thread.timeout.connect(self.check_plot_finished)
    #     self.plot_thread.start()
    #
    # def check_plot_finished(self):
    #     """
    #     If all the promises made are finished then all the shapes are in shapes_storage and can be plotted safely and
    #     then the UI is rebuilt accordingly.
    #     :return:
    #     """
    #
    #     try:
    #         if not self.grb_plot_promises:
    #             self.plot_thread.stop()
    #             self.plot_finished.emit()
    #             self.app.log.debug("AppGerberEditor --> delayed_plot finished")
    #     except Exception as e:
    #         traceback.print_exc()
    #
    # def setup_ui_after_delayed_plot(self):
    #     self.plot_finished.disconnect()
    #
    #     # now that we have data, create the GUI interface and add it to the Tool Tab
    #     self.build_ui(first_run=True)
    #     self.plot_all()
    #
    #     # HACK: enabling/disabling the cursor seams to somehow update the shapes making them more 'solid'
    #     # - perhaps is a bug in VisPy implementation
    #     self.app.app_cursor.enabled = False
    #     self.app.app_cursor.enabled = True

    def on_zoom_fit(self):
        """
        Callback for zoom-fit request in Gerber Editor

        :return:        None
        """
        self.app.log.debug("AppGerberEditor.on_zoom_fit()")

        # calculate all the geometry in the edited Gerber object
        edit_geo = []
        for ap_code in self.storage_dict:
            for geo_el in self.storage_dict[ap_code]['geometry']:
                actual_geo = geo_el.geo
                solid = actual_geo.get('solid')
                if solid is not None and not solid.is_empty:
                    edit_geo.append(solid)

        if not edit_geo:
            return

        # calculate the bounds values for the edited Gerber object
        xmin, ymin, xmax, ymax = get_shapely_list_bounds(edit_geo)

        new_rect = Rect((xmin, ymin), (xmax - xmin, ymax - ymin))
        self.app.plotcanvas.fit_view(rect=new_rect)

    def get_selected(self):
        """
        Returns list of shapes that are selected in the editor.

        :return: List of shapes.
        """
        # return [shape for shape in self.shape_buffer if shape["selected"]]
        return self.selected

    @gerber_operation(_('Delete shapes'))
    def delete_selected(self):
        selected = set(self.selected)

        if not selected:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Failed. No aperture geometry is selected."))
            return

        self.is_modified = True
        geometry_selected = selected - set(self.utility)
        self.utility[:] = [shape for shape in self.utility if shape not in selected]
        if geometry_selected:
            for storage in self.storage_dict.values():
                if 'geometry' in storage:
                    storage['geometry'][:] = [shape for shape in storage['geometry'] if shape not in geometry_selected]
            self.touch_geometry()

        self.selected = []
        self.build_ui()
        self.app.inform.emit('[success] %s' % _("Done."))

    @gerber_operation(_('Delete shape'))
    def delete_shape(self, geo_el):
        self.is_modified = True

        if geo_el in self.utility:
            self.utility.remove(geo_el)
            return

        for storage in self.storage_dict:
            try:
                if geo_el in self.storage_dict[storage]['geometry']:
                    self.storage_dict[storage]['geometry'].remove(geo_el)
            except KeyError:
                pass
        self.touch_geometry()
        if geo_el in self.selected:
            self.selected.remove(geo_el)

    def delete_utility_geometry(self):
        # for_deletion = [shape for shape in self.shape_buffer if shape.utility]
        # for_deletion = [shape for shape in self.storage.get_objects() if shape.utility]
        for_deletion = [geo_el for geo_el in self.utility]
        for geo_el in for_deletion:
            self.delete_shape(geo_el)

        self.tool_shape.clear(update=True)
        self.tool_shape.redraw()

    def on_delete_btn(self):
        self.delete_selected()
        self.plot_all()

    def select_tool(self, pluginName):
        """
        Selects a drawing tool. Impacts the object and appGUI.

        :param pluginName: Name of the tool.
        :return: None
        """
        self.tools_gerber[pluginName]["button"].setChecked(True)
        self.on_tool_select(pluginName)

    def set_selected(self, geo_el):

        # Remove and add to the end.
        if geo_el in self.selected:
            self.selected.remove(geo_el)

        self.selected.append(geo_el)

    def set_unselected(self, geo_el):
        if geo_el in self.selected:
            self.selected.remove(geo_el)

    def on_copy_button(self):
        self.select_tool('copy')
        return

    def on_move_button(self):
        self.select_tool('move')
        return

    def on_pad_add(self):
        self.select_tool('pad')

    def on_pad_add_array(self):
        self.select_tool('array')

    def on_track_add(self):
        self.select_tool('track')

    def on_region_add(self):
        self.select_tool('region')

    def on_poligonize(self):
        self.select_tool('poligonize')

    def on_region_drawing(self, kind):
        if not self.history.ready():
            return
        self.close_region_drawing()
        self.select_tool('select')
        # the panel is not a toolbar tool: leave the Select action unchecked so MainGUI forwards keys to it
        self.tools_gerber['select']['button'].setChecked(False)
        if self.app.ui.properties_scroll_area.widget() is not self.ui.grb_edit_widget:
            self.app.ui.properties_scroll_area.takeWidget()
            self.app.ui.properties_scroll_area.setWidget(self.ui.grb_edit_widget)
        self.region_drawing_panel = RegionDrawingPanel(self, kind)
        self.ui.custom_box.insertWidget(0, self.region_drawing_panel)
        self.active_tool = self.region_drawing_panel
        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

    def close_region_drawing(self, return_to_select=True):
        panel = getattr(self, 'region_drawing_panel', None)
        if panel is None:
            return
        self.region_drawing_panel = None
        self.ui.custom_box.removeWidget(panel)
        panel.hide()
        panel.deleteLater()
        self.delete_utility_geometry()
        if self.active_tool is panel:
            self.active_tool = None
            if return_to_select:
                self.select_tool('select')

    @gerber_operation(_('Draw regions'))
    def add_region_geometry(self, geometry):
        """Commit validated material without assigning it a guessed native aperture."""
        if not isinstance(geometry, (Polygon, MultiPolygon)) or geometry.is_empty or not geometry.is_valid or \
                not all(math.isfinite(value) for value in geometry.bounds):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Drawing requires valid, finite polygon material."))
            return 'fail'
        polygons = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
        shapes = [DrawToolShape({'solid': polygon, 'follow': LineString(polygon.exterior.coords)})
                  for polygon in polygons]
        key = next((key for key, aperture in self.storage_dict.items() if aperture['type'] == 'REG'), None)
        if key is None:
            key = 0 if not any(str(key) == '0' for key in self.storage_dict) else \
                max(numeric_aperture_codes(self.storage_dict), default=0) + 1
        self.history.capture([key])
        region = self.storage_dict.setdefault(key, {'type': 'REG', 'size': 0.0, 'geometry': []})
        region['geometry'].extend(shapes)
        self.loaded_as_regions = True
        self.selected = shapes
        self.last_aperture_selected = key
        self.current_storage = region['geometry']
        self.touch_geometry()
        self.build_ui()
        self.sync_aperture_rows({key})
        self.plot_all()
        return shapes

    @gerber_operation(_('Material regions'))
    def apply_region_operation(self, operation):
        """Difference targets the first selection; difference_keep retains the other records and their owners."""
        shapes = list(dict.fromkeys(self.selected))
        selected = set(shapes)
        owners = {shape: key for key, aperture in self.storage_dict.items()
                  for shape in aperture.get('geometry', ()) if shape in selected}
        solids = [shape.geo.get('solid') for shape in shapes]
        if operation not in ('union', 'intersection', 'difference', 'difference_keep', 'explode') or not shapes or \
                len(owners) != len(shapes) or any(
                    solid is None or solid.is_empty or not solid.is_valid or
                    not isinstance(solid, (Polygon, MultiPolygon)) for solid in solids) or any(
                    shape.geo.get('clear') is not None and not shape.geo['clear'].is_empty for shape in shapes):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select valid polygon material without clear records."))
            return 'fail'
        if operation in ('intersection', 'difference', 'difference_keep') and len(shapes) < 2:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select at least two material features."))
            return 'fail'

        # Compute and validate the complete output before changing ownership or history state.
        if operation == 'union':
            output = [unary_union(solids)]
        elif operation in ('difference', 'difference_keep'):
            output = [solids[0].difference(unary_union(solids[1:]))]
        elif operation == 'intersection':
            result = solids[0]
            for solid in solids[1:]:
                result = result.intersection(solid)
            output = [result]
        else:
            output = solids
        polygons = []
        for result in output:
            if result.is_empty:
                continue
            parts = flatten_shapely_geometry(result)
            # A touching intersection has no polygon material, not a drawable open path.
            polygons.extend(part for part in parts if isinstance(part, Polygon) and not part.is_empty)
        if not polygons:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The operation produced no polygon material. Nothing was changed."))
            return 'fail'
        if any(not polygon.is_valid for polygon in polygons):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The operation produced invalid geometry. Nothing was changed."))
            return 'fail'
        replacements = [DrawToolShape({'solid': polygon, 'follow': LineString(polygon.exterior.coords)})
                        for polygon in polygons]
        cutters = shapes[1:] if operation == 'difference_keep' else []
        removed = {shapes[0]} if cutters else selected
        region_key = next((key for key, aperture in self.storage_dict.items() if aperture['type'] == 'REG'), None)
        if region_key is None:
            region_key = 0 if not any(str(key) == '0' for key in self.storage_dict) else \
                max(numeric_aperture_codes(self.storage_dict), default=0) + 1
        history = getattr(self, 'history', None)
        if isinstance(history, GerberHistory):
            history.capture(set(owners.values()) | {region_key})
        for key in set(owners.values()):
            storage = self.storage_dict[key]['geometry']
            storage[:] = [shape for shape in storage if shape not in removed]
        region = self.storage_dict.setdefault(region_key, {'type': 'REG', 'size': 0.0, 'geometry': []})
        region['geometry'].extend(replacements)
        self.loaded_as_regions = True
        self.selected = replacements + cutters
        self.last_aperture_selected = region_key
        self.current_storage = region['geometry']
        self.touch_geometry()
        self.build_ui()
        self.sync_aperture_rows({region_key} | {owners[shape] for shape in cutters})
        self.plot_all()
        return replacements

    def on_disc_add(self):
        self.select_tool('disc')

    def on_add_semidisc(self):
        self.select_tool('semidisc')

    def on_simplification(self):
        self.select_tool('simplify')

    def on_scale(self, *signal_args, on_commit=None):
        scale_factor = 1.0
        self.app.log.debug("AppGerberEditor.on_scale()")

        try:
            scale_factor = float(self.ui.scale_factor_entry.get_value())
        except ValueError:
            # try to convert comma to decimal point. if it's still not working error message and return
            try:
                scale_factor = float(self.ui.scale_factor_entry.get_value().replace(',', '.'))
                self.ui.scale_factor_entry.set_value(scale_factor)
            except ValueError:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Scale factor value is missing or wrong format. Add it and retry."))
                if self.history.depth:
                    self.history.failed = True
                return 'fail'

        if not math.isfinite(scale_factor) or scale_factor <= 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Scale factor must be finite and positive."))
            if self.history.depth:
                self.history.failed = True
            return 'fail'

        if not self.ui.apertures_table.selectedItems():
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("No aperture to scale. Select at least one aperture and try again."))
            if self.history.depth:
                self.history.failed = True
            return 'fail'

        selection = set(self.selected)
        rows = {item.row() for item in self.ui.apertures_table.selectedItems()}
        codes = {self.ui.apertures_table.item(row, 1).text() for row in rows}
        shapes = list(dict.fromkeys(shape for key, aperture in self.storage_dict.items() if str(key) in codes
                                    for shape in aperture.get('geometry', ()) if shape in selection))
        if not shapes:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No shape selected."))
            if self.history.depth:
                self.history.failed = True
            return 'fail'
        return self.submit_geometry(_("Scale"), lambda detached: scale_selection_about_centers(detached, scale_factor),
                                    shapes=shapes, success=_("Done."), on_commit=on_commit)

    def on_markarea(self):
        # clear previous marking
        self.ma_annotation.clear(update=True)
        self.geo_to_delete = []

        self.units = self.app.app_units.upper()

        text = []
        position = []

        for apcode in self.storage_dict:
            if 'geometry' in self.storage_dict[apcode]:
                for geo_el in self.storage_dict[apcode]['geometry']:
                    if 'solid' in geo_el.geo:
                        area = geo_el.geo['solid'].area
                        try:
                            upper_threshold_val = self.ui.ma_upper_threshold_entry.get_value()
                        except Exception:
                            return

                        try:
                            lower_threshold_val = self.ui.ma_lower_threshold_entry.get_value()
                        except Exception:
                            lower_threshold_val = 0.0

                        if float(upper_threshold_val) > area > float(lower_threshold_val):
                            current_pos = geo_el.geo['solid'].exterior.coords[-1]
                            text_elem = '%.*f' % (self.decimals, area)
                            text.append(text_elem)
                            position.append(current_pos)
                            self.geo_to_delete.append(geo_el)

        if text:
            self.ma_annotation.set(text=text, pos=position, visible=True,
                                   font_size=self.app.options.get(
                                       "cncjob_annotation_fontsize",
                                       self.app.defaults.get(
                                           "cncjob_annotation_fontsize",
                                           self.app.defaults.factory_defaults.get("cncjob_annotation_fontsize")
                                       )
                                   ),
                                   color='#000000FF')
            self.app.inform.emit('[success] %s' % _("Polygons marked."))
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No polygons were marked. None fit within the limits."))

    @gerber_operation(_('Delete marked polygons'))
    def delete_marked_polygons(self):
        marked, self.geo_to_delete = self.geo_to_delete, []
        for shape_sel in marked:
            self.delete_shape(shape_sel)

        self.build_ui()
        self.plot_all()
        self.app.inform.emit('[success] %s' % _("Done."))

    def on_eraser(self):
        self.select_tool('eraser')

    def on_transform(self):
        if isinstance(self.active_tool, TransformEditorGrb):
            self.select_tool('select')
        else:
            self.select_tool('transform')

    def hide_tool(self, tool_name):
        # self.app.ui.notebook.setTabText(2, _("Tools"))
        try:
            if tool_name == 'all':
                self.ui.apertures_frame.hide()
            if tool_name == 'select':
                self.ui.apertures_frame.show()
            if tool_name == 'scale' or tool_name == 'all':
                self.ui.scale_tool_frame.hide()
            if tool_name == 'markarea' or tool_name == 'all':
                self.ui.ma_tool_frame.hide()
        except Exception as e:
            self.app.log.error("AppGerberEditor.hide_tool() --> %s" % str(e))
        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)


class AppGerberEditorUI:
    def __init__(self, app):
        self.app = app

        # Number of decimals used by tools in this class
        self.decimals = self.app.decimals

        # ## Current application units in Upper Case
        self.units = self.app.app_units.upper()

        self.grb_edit_widget = QtWidgets.QWidget()

        layout = QtWidgets.QVBoxLayout()
        self.grb_edit_widget.setLayout(layout)

        # Page Title box (spacing between children)
        self.title_box = QtWidgets.QHBoxLayout()
        layout.addLayout(self.title_box)

        # Page Title icon
        pixmap = QtGui.QPixmap(self.app.resource_location + '/app32.png')
        self.icon = FCLabel()
        self.icon.setPixmap(pixmap)
        self.title_box.addWidget(self.icon, stretch=0)

        # Title label
        self.title_label = FCLabel("<font size=5><b>%s</b></font>" % _('Gerber Editor'))
        self.title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
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
        # self.level.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        # Object name
        self.name_box = QtWidgets.QHBoxLayout()
        layout.addLayout(self.name_box)
        name_label = FCLabel(_("Name:"))
        self.name_box.addWidget(name_label)
        self.name_entry = FCEntry()
        self.name_entry.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.name_box.addWidget(self.name_entry)

        history_title = FCLabel(_("History"), bold=True, color='tomato')
        history_title.setToolTip(_("Jump to any retained state of this editing session."))
        layout.addWidget(history_title)

        history_frame = FCFrame()
        layout.addWidget(history_frame)

        history_grid = GLay(v_spacing=5, h_spacing=3)
        history_frame.setLayout(history_grid)
        self.history_positions = QtWidgets.QComboBox()
        self.history_positions.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                             QtWidgets.QSizePolicy.Policy.Fixed)
        history_grid.addWidget(self.history_positions, 0, 0, 1, 2)

        # Box for custom widgets
        # This gets populated in offspring implementations.
        self.custom_box = QtWidgets.QVBoxLayout()
        layout.addLayout(self.custom_box)

        # #############################################################################################################
        # #################################### Gerber Apertures Table #################################################
        # #############################################################################################################
        self.apertures_table_label = FCLabel('%s' % _('Apertures'), bold=True, color='orange')
        self.apertures_table_label.setToolTip(
            _("Apertures Table for the Gerber Object.")
        )
        self.custom_box.addWidget(self.apertures_table_label)

        tw_frame = FCFrame()
        self.custom_box.addWidget(tw_frame)

        # Grid Layout
        ap_grid = GLay(v_spacing=5, h_spacing=3)
        tw_frame.setLayout(ap_grid)

        self.apertures_table = FCTable()
        # delegate = SpinBoxDelegate(units=self.units)
        # self.apertures_table.setItemDelegateForColumn(1, delegate)

        ap_grid.addWidget(self.apertures_table, 0, 0, 1, 2)

        self.apertures_table.setColumnCount(5)
        self.apertures_table.setHorizontalHeaderLabels(['#', _('Code'), _('Type'), _('Size'), _('Dim')])
        self.apertures_table.setSortingEnabled(False)
        self.apertures_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.apertures_table.horizontalHeaderItem(0).setToolTip(
            _("Index"))
        self.apertures_table.horizontalHeaderItem(1).setToolTip(
            _("Aperture Code"))
        self.apertures_table.horizontalHeaderItem(2).setToolTip(
            _("Type of aperture: circular, rectangle, macros etc"))
        self.apertures_table.horizontalHeaderItem(4).setToolTip(
            _("Aperture Size:"))
        self.apertures_table.horizontalHeaderItem(4).setToolTip(
            _("Aperture Dimensions:\n"
              " - (width, height) for R, O type.\n"
              " - (dia, nVertices) for P type"))

        # add a frame and inside add a vertical box layout. Inside this vbox layout I add all the Apertures widgets
        # this way I can hide/show the frame
        self.apertures_frame = QtWidgets.QFrame()
        self.apertures_frame.setContentsMargins(0, 0, 0, 0)
        ap_grid.addWidget(self.apertures_frame, 2, 0, 1, 2)

        self.apertures_box = QtWidgets.QVBoxLayout()
        self.apertures_box.setContentsMargins(0, 0, 0, 0)
        self.apertures_frame.setLayout(self.apertures_box)

        # #############################################################################################################
        # ############################ Add/Delete an new Aperture #####################################################
        # #############################################################################################################
        grid1 = GLay(v_spacing=5, h_spacing=3)
        self.apertures_box.addLayout(grid1)

        # Title
        apadd_del_lbl = FCLabel('%s:' % _('Add/Delete Aperture'), bold=True)
        apadd_del_lbl.setToolTip(
            _("Add/Delete an aperture in the aperture table")
        )
        grid1.addWidget(apadd_del_lbl, 0, 0, 1, 2)

        # Aperture Code
        apcode_lbl = FCLabel('%s:' % _('Code'))
        apcode_lbl.setToolTip(_("Code for the new aperture"))

        self.apcode_entry = FCSpinner()
        self.apcode_entry.set_range(0, 1000)
        self.apcode_entry.setWrapping(True)

        grid1.addWidget(apcode_lbl, 1, 0)
        grid1.addWidget(self.apcode_entry, 1, 1)

        # Aperture Size
        apsize_lbl = FCLabel('%s' % _('Size:'))
        apsize_lbl.setToolTip(
            _("Size for the new aperture.\n"
              "If aperture type is 'R' or 'O' then\n"
              "this value is automatically\n"
              "calculated as:\n"
              "sqrt(width**2 + height**2)")
        )

        self.apsize_entry = FCDoubleSpinner()
        self.apsize_entry.set_precision(self.decimals)
        self.apsize_entry.set_range(0.0, 10000.0000)

        grid1.addWidget(apsize_lbl, 2, 0)
        grid1.addWidget(self.apsize_entry, 2, 1)

        # Aperture Type
        aptype_lbl = FCLabel('%s:' % _('Type'))
        aptype_lbl.setToolTip(
            _("Select the type of new aperture. Can be:\n"
              "C = circular\n"
              "R = rectangular\n"
              "O = oblong")
        )

        self.aptype_cb = FCComboBox()
        self.aptype_cb.addItems(['C', 'R', 'O'])

        grid1.addWidget(aptype_lbl, 3, 0)
        grid1.addWidget(self.aptype_cb, 3, 1)

        # Aperture Dimensions
        self.apdim_lbl = FCLabel('%s:' % _('Dims'))
        self.apdim_lbl.setToolTip(
            _("Dimensions for the new aperture: width, then height.")
        )

        self.apdim_entry = FCDoubleSpinner()
        self.apheight_entry = FCDoubleSpinner()
        for entry, text in ((self.apdim_entry, _("Width")), (self.apheight_entry, _("Height"))):
            entry.set_precision(self.decimals)
            entry.set_range(0.0, 10000.0000)
            entry.setToolTip(text)

        dims_layout = QtWidgets.QHBoxLayout()
        dims_layout.setContentsMargins(0, 0, 0, 0)
        dims_layout.setSpacing(3)
        dims_layout.addWidget(self.apdim_entry)
        dims_layout.addWidget(self.apheight_entry)

        grid1.addWidget(self.apdim_lbl, 4, 0)
        grid1.addLayout(dims_layout, 4, 1)

        # Aperture Buttons
        vlay_buttons = QtWidgets.QVBoxLayout()
        grid1.addLayout(vlay_buttons, 1, 2, 4, 1)

        self.addaperture_btn = QtWidgets.QToolButton()
        self.addaperture_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)
        self.addaperture_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/plus16.png'))
        self.addaperture_btn.setToolTip(
            _("Add a new aperture to the aperture list.")
        )

        self.delaperture_btn = QtWidgets.QToolButton()
        # self.delaperture_btn.setSizePolicy(
        #     QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding)

        self.delaperture_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/trash32.png'))
        self.delaperture_btn.setToolTip(
            _("Delete a aperture in the aperture list")
        )
        vlay_buttons.addWidget(self.addaperture_btn)
        vlay_buttons.addWidget(self.delaperture_btn)

        # Zoom Selection
        self.geo_zoom = FCCheckBox(_("Zoom on selection"))
        ap_grid.addWidget(self.geo_zoom, 4, 0, 1, 2)

        # #############################################################################################################
        # ################################ Advanced sections (hidden in Beginner mode) ################################
        # #############################################################################################################
        self.shape_frame = QtWidgets.QFrame()
        self.shape_frame.setContentsMargins(0, 0, 0, 0)
        self.custom_box.addWidget(self.shape_frame)

        shape_box = QtWidgets.QVBoxLayout()
        shape_box.setContentsMargins(0, 0, 0, 0)
        self.shape_frame.setLayout(shape_box)

        # #############################################################################################################
        # ############################################ Shape Properties ###############################################
        # #############################################################################################################
        param_title = FCLabel('%s' % _("Parameters"), bold=True, color='blue')
        param_title.setToolTip(
            _("Geometry parameters.")
        )
        shape_box.addWidget(param_title)

        param_frame = FCFrame()
        shape_box.addWidget(param_frame)

        self.shape_grid = GLay(v_spacing=5, h_spacing=3)
        param_frame.setLayout(self.shape_grid)

        # Is Valid
        valid_lbl = FCLabel('%s:' % _("Valid"))
        valid_lbl.setToolTip(
            _("Show if the selected polygon is valid.")
        )
        self.is_valid_entry = FCLabel("False")
        self.shape_grid.addWidget(valid_lbl, 0, 0)
        self.shape_grid.addWidget(self.is_valid_entry, 0, 1)

        # Area
        area_lbl = FCLabel('%s:' % _("Area"))
        area_lbl.setToolTip(
            _("Show the area of the selected polygon.")
        )
        self.area_entry = FCEntry(decimals=self.decimals)
        self.area_entry.set_value(0.0)
        area_units_lbl = FCLabel(self.units + '²')

        self.shape_grid.addWidget(area_lbl, 1, 0)
        self.shape_grid.addWidget(self.area_entry, 1, 1)
        self.shape_grid.addWidget(area_units_lbl, 1, 2)

        # Vertex Points Number
        vertex_lbl = FCLabel('%s:' % _("Vertex Points"))
        vertex_lbl.setToolTip(
            _("The number of vertex points in the selected geometry element.")
        )
        self.geo_vertex_entry = FCEntry(decimals=self.decimals)
        self.geo_vertex_entry.setReadOnly(True)

        self.shape_grid.addWidget(vertex_lbl, 4, 0)
        self.shape_grid.addWidget(self.geo_vertex_entry, 4, 1)

        # Coordinates
        coords_lbl = FCLabel('%s:' % _("Coordinates"))
        coords_lbl.setToolTip(
            _("The coordinates of the selected geometry element.")
        )
        self.shape_grid.addWidget(coords_lbl, 2, 0, 1, 3)

        self.geo_coords_entry = FCTextEdit()
        self.geo_coords_entry.setPlaceholderText(
            _("The coordinates of the selected geometry element.")
        )
        self.shape_grid.addWidget(self.geo_coords_entry, 3, 0, 1, 3)

        self.vertex_edit_btn = FCButton(_("Edit vertices"))
        self.vertex_edit_btn.setToolTip(_("Numerically move, insert or remove a vertex of one selected region or track.\n"
                                          "A region edits its solid, a track its FOLLOW line. Pads have no vertices."))
        self.shape_grid.addWidget(self.vertex_edit_btn, 5, 0, 1, 3)

        # #############################################################################################################
        # ############################################## Select by Size ###############################################
        # #############################################################################################################
        size_title = FCLabel(_("Select by Size"), bold=True, color='green')
        shape_box.addWidget(size_title)

        size_frame = FCFrame()
        shape_box.addWidget(size_frame)

        size_grid = GLay(v_spacing=5, h_spacing=3)
        size_frame.setLayout(size_grid)

        self.size_width_chk = FCCheckBox(_("Width"))
        self.size_width_entry = FCEntry()
        self.size_width_entry.setText('1')
        self.size_height_chk = FCCheckBox(_("Height"))
        self.size_height_entry = FCEntry()
        self.size_height_entry.setText('1')
        self.size_tolerance_entry = FCEntry()
        self.size_tolerance_entry.setText('0')
        for row, check, entry in ((0, self.size_width_chk, self.size_width_entry),
                                  (1, self.size_height_chk, self.size_height_entry)):
            size_grid.addWidget(check, row, 0)
            size_grid.addWidget(entry, row, 1)
            size_grid.addWidget(FCLabel(self.units), row, 2)
        size_grid.addWidget(FCLabel('%s:' % _("Tolerance")), 2, 0)
        size_grid.addWidget(self.size_tolerance_entry, 2, 1)
        size_grid.addWidget(FCLabel(self.units), 2, 2)

        self.size_area_chk = FCCheckBox(_("Solid area"))
        self.size_length_chk = FCCheckBox(_("FOLLOW length"))
        self.size_area_min_entry, self.size_area_max_entry = FCEntry(), FCEntry()
        self.size_length_min_entry, self.size_length_max_entry = FCEntry(), FCEntry()
        for row, check, low, high, units in (
                (3, self.size_area_chk, self.size_area_min_entry, self.size_area_max_entry, self.units + '²'),
                (6, self.size_length_chk, self.size_length_min_entry, self.size_length_max_entry, self.units)):
            low.setText('0')
            high.setText('10000')
            low.setPlaceholderText(_("Minimum"))
            high.setPlaceholderText(_("Maximum"))
            low.setToolTip(_("Minimum (inclusive)") + ' (' + units + ')')
            high.setToolTip(_("Maximum (inclusive)") + ' (' + units + ')')
            check.setToolTip(_("Inclusive range. All enabled filters must match. Missing or empty components don't match.")
                             + ' (' + units + ')')
            size_grid.addWidget(check, row, 0, 1, 3)
            size_grid.addWidget(FCLabel('%s:' % _("Min")), row + 1, 0)
            size_grid.addWidget(low, row + 1, 1)
            size_grid.addWidget(FCLabel(units), row + 1, 2)
            size_grid.addWidget(FCLabel('%s:' % _("Max")), row + 2, 0)
            size_grid.addWidget(high, row + 2, 1)
            size_grid.addWidget(FCLabel(units), row + 2, 2)

        self.size_select_btn = FCButton(_("Select"))
        self.size_select_btn.setToolTip(
            _("Select shapes by their solid bounding-box width and height in current units.\n"
              "All enabled dimensions must match within the tolerance (inclusive).\n"
              "Searches the selected shapes if any, else all shapes. Replaces the current selection.") + '\n' +
            _("Solid area and FOLLOW length use inclusive ranges, without dimension tolerance."))
        self.size_delete_btn = FCButton(_("Delete by Size"))
        self.size_delete_btn.setToolTip(_("Delete matching records from the selected subset, or all records if none are selected. Undo as one operation."))
        size_grid.addWidget(self.size_select_btn, 9, 0, 1, 3)
        size_grid.addWidget(self.size_delete_btn, 10, 0, 1, 3)

        # #############################################################################################################
        # ################################################### Snapping ################################################
        # #############################################################################################################
        snap_title = FCLabel(_("Snapping"), bold=True, color='teal')
        shape_box.addWidget(snap_title)

        snap_frame = FCFrame()
        shape_box.addWidget(snap_frame)

        snap_grid = GLay(v_spacing=5, h_spacing=3)
        snap_frame.setLayout(snap_grid)

        self.midpoint_snap_cb = FCCheckBox(_("FOLLOW midpoint snap"))
        self.segment_snap_cb = FCCheckBox(_("Nearest FOLLOW segment snap"))
        self.snap_radius_entry = FCDoubleSpinner()
        self.snap_radius_entry.set_range(1, 100)
        self.snap_radius_entry.set_precision(1)
        self.snap_radius_entry.set_value(8)
        self.snap_radius_entry.setToolTip(_("Acquisition radius in pixels. Falls back to global_snap_max model units if canvas conversion is unavailable."))
        self.snap_type_label = FCLabel(_("Snap: None"))
        self.snap_type_label.setWordWrap(True)
        self.snap_type_label.setToolTip(_("Nearest target wins. Ties prefer endpoint/bend, midpoint, segment, then grid. Corner snapping uses FOLLOW vertices and flash centers, never solid outlines."))
        snap_grid.addWidget(self.midpoint_snap_cb, 0, 0, 1, 3)
        snap_grid.addWidget(self.segment_snap_cb, 1, 0, 1, 3)
        snap_grid.addWidget(FCLabel('%s:' % _("Snap radius")), 2, 0)
        snap_grid.addWidget(self.snap_radius_entry, 2, 1)
        snap_grid.addWidget(FCLabel(_("px")), 2, 2)
        snap_grid.addWidget(self.snap_type_label, 3, 0, 1, 3)

        # #############################################################################################################
        # ################################################### Tracks ##################################################
        # #############################################################################################################
        tracks_title = FCLabel(_("Tracks"), bold=True, color='brown')
        shape_box.addWidget(tracks_title)

        tracks_frame = FCFrame()
        shape_box.addWidget(tracks_frame)

        tracks_grid = GLay(v_spacing=5, h_spacing=3)
        tracks_frame.setLayout(tracks_grid)

        self.bend_move_btn = FCButton(_("Move Track Bend"))
        self.bend_move_btn.setToolTip(
            _("Drag a FOLLOW bend of a circular-aperture track. Neighboring bends stay fixed.\n"
              "Supports one path or a two-record same-aperture join. Arc samples are polyline vertices."))
        tracks_grid.addWidget(self.bend_move_btn, 0, 0, 1, 3)

        self.split_tolerance_entry = FCEntry()
        self.split_tolerance_entry.setText('0.01')
        self.split_tolerance_entry.setToolTip(_("Maximum click distance and excluded endpoint distance in current units. Zero requires an exact interior click."))
        tracks_grid.addWidget(FCLabel('%s:' % _("Split tolerance")), 1, 0)
        tracks_grid.addWidget(self.split_tolerance_entry, 1, 1)
        tracks_grid.addWidget(FCLabel(self.units), 1, 2)

        self.split_action = QtGui.QAction(_("Split path"), self.grb_edit_widget)
        self.split_action.setToolTip(_("Select one open circular-aperture FOLLOW path, then click its interior."))
        self.split_btn = QtWidgets.QToolButton()
        self.split_btn.setDefaultAction(self.split_action)
        self.split_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        tracks_grid.addWidget(self.split_btn, 2, 0, 1, 3)

        self.join_tolerance_entry = FCEntry()
        self.join_tolerance_entry.setText('0.01')
        self.join_tolerance_entry.setToolTip(_("Endpoint ambiguity search radius in current units. Unlike Geometry, Gerber does not bridge gaps or move endpoints."))
        tracks_grid.addWidget(FCLabel('%s:' % _("Join tolerance")), 3, 0)
        tracks_grid.addWidget(self.join_tolerance_entry, 3, 1)
        tracks_grid.addWidget(FCLabel(self.units), 3, 2)

        self.join_btn = FCButton(_("Join paths"))
        self.join_btn.setToolTip(_("Join compatible same-aperture circular FOLLOW records. Endpoints must coincide; branches, gaps and material changes are rejected."))
        tracks_grid.addWidget(self.join_btn, 4, 0, 1, 3)

        # #############################################################################################################
        # ################################################### Regions #################################################
        # #############################################################################################################
        regions_title = FCLabel(_("Regions"), bold=True, color='indigo')
        shape_box.addWidget(regions_title)

        regions_frame = FCFrame()
        shape_box.addWidget(regions_frame)

        regions_grid = GLay(v_spacing=5, h_spacing=3)
        regions_frame.setLayout(regions_grid)

        self.region_buttons = {}
        for index, (operation, label) in enumerate((('union', _("Material Union")),
                                                    ('intersection', _("Material Intersection")),
                                                    ('difference', _("Material Difference")),
                                                    ('difference_keep', _("Diff. Keep")),
                                                    ('explode', _("Explode Regions")))):
            button = FCButton(label)
            button.setToolTip(_("Replace selected polygon material with explicit regions. Holes are retained. "
                                "Difference subtracts the other selections from the first. Keep Cutters retains them. "
                                "Clear records are rejected."))
            button.clicked.connect(lambda checked=False, op=operation: self.app.grb_editor.apply_region_operation(op))
            regions_grid.addWidget(button, index // 2, index % 2)
            self.region_buttons[operation] = button

        self.rectangle_region_btn = FCButton(_("Rectangle Region"))
        self.text_region_btn = FCButton(_("Text Regions"))
        for column, (kind, button) in enumerate((('rectangle', self.rectangle_region_btn),
                                                 ('text', self.text_region_btn))):
            button.setToolTip(_("Draw numeric material as explicit regions, retaining text holes and islands."))
            button.clicked.connect(lambda checked=False, tool=kind: self.app.grb_editor.on_region_drawing(tool))
            regions_grid.addWidget(button, 3, column)

        # #############################################################################################################
        # ############################################### Resize Circles ##############################################
        # #############################################################################################################
        resize_title = FCLabel(_("Resize Circles"), bold=True, color='magenta')
        shape_box.addWidget(resize_title)

        resize_frame = FCFrame()
        shape_box.addWidget(resize_frame)

        resize_grid = GLay(v_spacing=5, h_spacing=3)
        resize_frame.setLayout(resize_grid)

        self.resize_circles_entry = FCDoubleSpinner(policy=False)
        self.resize_circles_entry.set_precision(self.decimals)
        self.resize_circles_entry.set_range(0.0000, 10000.0000)
        self.resize_circles_entry.setSingleStep(10 ** -self.decimals)
        self.resize_circles_entry.setToolTip(_("New diameter of selected plain circular flashes. Tracks and noncircular features are skipped; shared apertures are not resized."))
        resize_grid.addWidget(FCLabel('%s:' % _("Diameter")), 0, 0)
        resize_grid.addWidget(self.resize_circles_entry, 0, 1)
        resize_grid.addWidget(FCLabel(self.units), 0, 2)
        self.resize_circles_btn = FCButton(_("Resize"))
        self.resize_circles_btn.setToolTip(_("Resize selected circular flashes about their own centers, as one undo step."))
        resize_grid.addWidget(self.resize_circles_btn, 1, 0, 1, 3)

        # #############################################################################################################
        # ################################################### Inspect #################################################
        # #############################################################################################################
        inspect_title = FCLabel(_("Inspect"), bold=True, color='purple')
        shape_box.addWidget(inspect_title)

        inspect_frame = FCFrame()
        shape_box.addWidget(inspect_frame)

        inspect_grid = GLay(v_spacing=5, h_spacing=3)
        inspect_frame.setLayout(inspect_grid)

        self.diagnostics_component = FCComboBox()
        for label, value in ((_('Solid'), 'solid'), (_('Follow'), 'follow'), (_('Clear'), 'clear')):
            self.diagnostics_component.addItem(label, value)
        self.diagnostics_tolerance_entry = FCEntry()
        self.diagnostics_tolerance_entry.setText('0.01')
        self.diagnostics_btn = FCButton(_("Inspect selected geometry"))
        self.diagnostics_result = FCLabel('')
        self.diagnostics_result.setWordWrap(True)
        self.diagnostics_result.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.diagnostics_result.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.component_properties = FCLabel('')
        self.component_properties.setWordWrap(True)
        self.component_properties.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        inspect_grid.addWidget(FCLabel('%s:' % _("Component")), 0, 0)
        inspect_grid.addWidget(self.diagnostics_component, 0, 1)
        inspect_grid.addWidget(FCLabel('%s:' % _("Endpoint tolerance")), 1, 0)
        inspect_grid.addWidget(self.diagnostics_tolerance_entry, 1, 1)
        inspect_grid.addWidget(FCLabel(self.units), 1, 2)
        inspect_grid.addWidget(self.diagnostics_btn, 2, 0, 1, 3)
        inspect_grid.addWidget(self.component_properties, 3, 0, 1, 3)
        inspect_grid.addWidget(self.diagnostics_result, 4, 0, 1, 3)

        self.features_table = FCTable()
        self.features_table.setColumnCount(5)
        self.features_table.setHorizontalHeaderLabels([_("Aperture"), _("Feature"), _("Components"),
                                                       _("Solid area"), _("FOLLOW length")])
        self.features_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.features_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.features_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.features_table.setSortingEnabled(False)
        self.features_table.setToolTip(_("Select individual records for component inspection. Shared records select all of their aperture owners. Values use current units; area uses squared units."))
        inspect_grid.addWidget(FCLabel(_("Features"), bold=True), 5, 0, 1, 3)
        inspect_grid.addWidget(self.features_table, 6, 0, 1, 3)

        # Same width for the label column in the sections with a label/entry layout
        GLay.set_common_column_size([self.shape_grid, size_grid, snap_grid, tracks_grid, regions_grid, resize_grid, inspect_grid], 0)

        # #############################################################################################################
        # ########################################### SCALE TOOL ######################################################
        # #############################################################################################################
        self.scale_tool_frame = QtWidgets.QFrame()
        self.scale_tool_frame.setContentsMargins(0, 0, 0, 0)
        self.custom_box.addWidget(self.scale_tool_frame)
        self.scale_tools_box = QtWidgets.QVBoxLayout()
        self.scale_tools_box.setContentsMargins(0, 0, 0, 0)
        self.scale_tool_frame.setLayout(self.scale_tools_box)
        self.scale_tool_frame.hide()

        # Title
        scale_title_lbl = FCLabel('%s:' % _('Scale Aperture'), bold=True)
        scale_title_lbl.setToolTip(
            _("Scale a aperture in the aperture list")
        )
        self.scale_tools_box.addWidget(scale_title_lbl)

        # Grid Layout
        scale_grid = GLay(v_spacing=5, h_spacing=3)
        self.scale_tools_box.addLayout(scale_grid)

        self.scale_factor_lbl = FCLabel('%s:' % _("Scale factor"))
        self.scale_factor_lbl.setToolTip(
            _("The factor by which to scale the selected aperture.\n"
              "Values can be between 0.0000 and 999.9999")
        )
        self.scale_factor_entry = FCDoubleSpinner()
        self.scale_factor_entry.set_precision(self.decimals)
        self.scale_factor_entry.set_range(0.0000, 10000.0000)

        scale_grid.addWidget(self.scale_factor_lbl, 0, 0)
        scale_grid.addWidget(self.scale_factor_entry, 0, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        scale_grid.addWidget(separator_line, 2, 0, 1, 2)

        # Buttons
        self.scale_button = FCButton(_("Scale"))
        self.scale_button.setIcon(QtGui.QIcon(self.app.resource_location + '/scale32.png'))
        scale_grid.addWidget(self.scale_button, 4, 0, 1, 2)

        # #############################################################################################################
        # ######################################### Mark Area TOOL ####################################################
        # #############################################################################################################
        self.ma_tool_frame = QtWidgets.QFrame()
        self.ma_tool_frame.setContentsMargins(0, 0, 0, 0)
        self.custom_box.addWidget(self.ma_tool_frame)
        self.ma_tools_box = QtWidgets.QVBoxLayout()
        self.ma_tools_box.setContentsMargins(0, 0, 0, 0)
        self.ma_tool_frame.setLayout(self.ma_tools_box)
        self.ma_tool_frame.hide()

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.ma_tools_box.addWidget(separator_line)

        # Title
        ma_title_lbl = FCLabel('%s:' % _('Mark polygons'), bold=True)
        ma_title_lbl.setToolTip(
            _("Mark the polygon areas.")
        )
        self.ma_tools_box.addWidget(ma_title_lbl)

        # Form Layout
        mark_grid = GLay(v_spacing=5, h_spacing=3)
        self.ma_tools_box.addLayout(mark_grid)

        # Upper Threshold
        self.ma_upper_threshold_lbl = FCLabel('%s:' % _("Area UPPER threshold"))
        self.ma_upper_threshold_lbl.setToolTip(
            _("The threshold value, all areas less than this are marked.\n"
              "Can have a value between 0.0000 and 10000.0000")
        )
        self.ma_upper_threshold_entry = FCDoubleSpinner()
        self.ma_upper_threshold_entry.set_precision(self.decimals)
        self.ma_upper_threshold_entry.set_range(0, 10000)

        mark_grid.addWidget(self.ma_upper_threshold_lbl, 0, 0)
        mark_grid.addWidget(self.ma_upper_threshold_entry, 0, 1)

        # Lower Threshold
        self.ma_lower_threshold_lbl = FCLabel('%s:' % _("Area LOWER threshold"))
        self.ma_lower_threshold_lbl.setToolTip(
            _("The threshold value, all areas more than this are marked.\n"
              "Can have a value between 0.0000 and 10000.0000")
        )
        self.ma_lower_threshold_entry = FCDoubleSpinner()
        self.ma_lower_threshold_entry.set_precision(self.decimals)
        self.ma_lower_threshold_entry.set_range(0, 10000)

        mark_grid.addWidget(self.ma_lower_threshold_lbl, 2, 0)
        mark_grid.addWidget(self.ma_lower_threshold_entry, 2, 1)

        # Buttons
        hlay_ma = QtWidgets.QHBoxLayout()
        mark_grid.addLayout(hlay_ma, 4, 0, 1, 2)

        self.ma_threshold_button = FCButton(_("Mark"))
        self.ma_threshold_button.setIcon(QtGui.QIcon(self.app.resource_location + '/markarea32.png'))
        self.ma_threshold_button.setToolTip(
            _("Mark the polygons that fit within limits.")
        )
        hlay_ma.addWidget(self.ma_threshold_button)

        self.ma_delete_button = FCButton(_("Delete"))
        self.ma_delete_button.setIcon(QtGui.QIcon(self.app.resource_location + '/trash32.png'))
        self.ma_delete_button.setToolTip(
            _("Delete all the marked polygons.")
        )
        hlay_ma.addWidget(self.ma_delete_button)

        self.ma_clear_button = FCButton(_("Clear"))
        self.ma_clear_button.setIcon(QtGui.QIcon(self.app.resource_location + '/clean32.png'))
        self.ma_clear_button.setToolTip(
            _("Clear all the markings.")
        )
        hlay_ma.addWidget(self.ma_clear_button)

        self.custom_box.addStretch()
        layout.addStretch()

        # Editor
        self.exit_editor_button = FCButton(_('Exit Editor'), bold=True)
        self.exit_editor_button.setIcon(QtGui.QIcon(self.app.resource_location + '/power16.png'))
        self.exit_editor_button.setToolTip(
            _("Exit from Editor.")
        )
        layout.addWidget(self.exit_editor_button)


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
