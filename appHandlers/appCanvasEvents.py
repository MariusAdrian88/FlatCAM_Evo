# ###########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# https://github.com/MariusAdrian88/FlatCAM_Evo            #
# MIT Licence                                              #
# by Marius Stanciu (2019)                                  #
# ###########################################################

from PyQt6 import QtGui, QtWidgets, QtCore
from PyQt6.QtCore import Qt

from copy import copy
import numpy as np

from shapely import Point, Polygon
from shapely.ops import unary_union

from appCommon.SafeEval import safe_eval

import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AppCanvasEvents(QtCore.QObject):
    """Handler for canvas mouse events, selection, and hover/drag shapes."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.log = app.log
        self.inform = app.inform
        self.options = app.options
        self.defaults = getattr(app, 'defaults', {})
        self.ui = app.ui

    def on_mouse_click_over_plot(self, event):
        """
        Default actions are:
        :param event: Contains information about the event, like which button
            was clicked, the pixel coordinates and the axes coordinates.
        :return: None
        """
        event_pos = event.pos
        pos_canvas = self.app.plotcanvas.translate_coords(event_pos)
        self.app.mouse_down = True

        # So it can receive key presses
        self.app.plotcanvas.native.setFocus()

        if self.app.grid_status():
            pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
        else:
            pos = (pos_canvas[0], pos_canvas[1])

        self.app.mouse_click_pos = [pos[0], pos[1]]

        try:
            if event.button == 1:
                # Reset here the relative coordinates so there is a new reference on the click position
                if self.app.rel_point1 is None:
                    self.app.rel_point1 = self.app.mouse_click_pos
                else:
                    self.app.rel_point2 = copy(self.app.rel_point1)
                    self.app.rel_point1 = self.app.mouse_click_pos

            self.on_mouse_move_over_plot(event, origin_click=True)
        except Exception as e:
            self.log.error("App.on_mouse_click_over_plot() --> Outside plot? --> %s" % str(e))

    def on_mouse_double_click_over_plot(self, event):
        if event.button == 1:
            self.app.doubleclick = True

    def on_mouse_move_over_plot(self, event, origin_click=None):
        """
        Callback for the mouse motion event over the plot.

        :param event:           Contains information about the event.
        :param origin_click:
        :return:                None
        """

        event_pos = event.pos
        pan_button = 2 if self.options.get("global_pan_button", self.defaults.get("global_pan_button", '2')) == '2' else 3
        self.app.event_is_dragging = self.app.mouse_down

        # So it can receive key presses but not when the Tcl Shell is active
        if not self.ui.shell_dock.isVisible():
            if not self.app.plotcanvas.native.hasFocus():
                self.app.plotcanvas.native.setFocus()

        self.app.pos_jump = event_pos
        self.ui.popMenu.mouse_is_panning = False

        if origin_click is None:
            # if the RMB is clicked and mouse is moving over plot then 'panning_action' is True
            if event.button == pan_button and self.app.event_is_dragging == 1:

                # if a popup menu is active don't change mouse_is_panning variable because is not True
                if self.ui.popMenu.popup_active:
                    self.ui.popMenu.popup_active = False
                    return
                self.ui.popMenu.mouse_is_panning = True
                # A pan keeps the same point under the cursor: no plugin work for each move.
                return

        self.on_plugin_mouse_move(pos=event_pos)

        if self.app.rel_point1 is None:
            return

        try:  # May fail in case mouse not within axes
            pos_canvas = self.app.plotcanvas.translate_coords(event_pos)
            if pos_canvas[0] is None or pos_canvas[1] is None:
                return

            if self.app.grid_status():
                pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])

                # Update cursor
                self.app.app_cursor.set_data(
                    np.asarray([(pos[0], pos[1])]),
                    symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                    edge_width=self.options.get("global_cursor_width", self.defaults.get("global_cursor_width", 2)),
                    size=self.options.get("global_cursor_size", self.defaults.get("global_cursor_size", 20))
                )
            else:
                pos = (pos_canvas[0], pos_canvas[1])

            self.app.dx = pos[0] - float(self.app.rel_point1[0])
            self.app.dy = pos[1] - float(self.app.rel_point1[1])

            self.ui.update_location_labels(self.app.dx, self.app.dy, pos[0], pos[1])
            self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, pos[0], pos[1])

            self.app.mouse_pos = [pos[0], pos[1]]

            if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)) is False:
                self.app.selection_type = None
                return

            # the object selection on canvas does not work for App Tools or for Editors
            if self.app.call_source != 'app':
                self.app.selection_type = None
                return

            # if the mouse is moved and the LMB is clicked then the action is a selection
            we_have_drag_selection = False
            # Calculate threshold based on visible area (~3 screen pixels worth)
            try:
                rect_width = self.app.plotcanvas.view.camera.rect.width
                canvas_width = self.app.plotcanvas.native.width()
                move_threshold = 3 * (rect_width / canvas_width)
            except (AttributeError, ZeroDivisionError):
                move_threshold = 0.01  # Fallback
            if (
                    self.app.event_is_dragging
                    and (abs(self.app.dx) > move_threshold or abs(self.app.dy) > move_threshold)
                    and event.button == 1
            ):
                we_have_drag_selection = True

            self.app.selection_type = None  # no selection while mouse is moving
            if we_have_drag_selection:
                is_alt_selection = self.app.dx < 0
                if is_alt_selection:
                    self.draw_moving_selection_shape(
                        self.app.mouse_click_pos,
                        self.app.mouse_pos,
                        color=self.options.get('global_alt_sel_line', self.defaults.get('global_alt_sel_line', '#006E20BF')),
                        face_color=self.options.get('global_alt_sel_fill', self.defaults.get('global_alt_sel_fill', '#BBF268BF'))
                    )
                else:
                    self.draw_moving_selection_shape(
                        self.app.mouse_click_pos,
                        self.app.mouse_pos
                    )

                self.app.selection_type = not is_alt_selection  # True for regular selection, False for alt selection

            # hover effect - enabled in Preferences -> General -> appGUI Settings
            if self.options.get('global_hover_shape', self.defaults.get('global_hover_shape', False)):
                selected = self.app.collection.get_selected()
                for obj in self.app.collection.get_list():
                    try:
                        # select the object(s) only if it is enabled (plotted)
                        if obj.obj_options['plot']:
                            if obj not in selected:
                                xmin, xmax = obj.obj_options['xmin'], obj.obj_options['xmax']
                                ymin, ymax = obj.obj_options['ymin'], obj.obj_options['ymax']
                                if min(xmin, xmax) < pos[0] < max(xmin, xmax) and \
                                        min(ymin, ymax) < pos[1] < max(ymin, ymax):
                                    if obj.isHovering is False:
                                        obj.isHovering = True
                                        obj.notHovering = True
                                        # create the selection box around the selected object
                                        self.draw_hover_shape(obj, color='#d1e0e0FF')
                                else:
                                    if obj.notHovering is True:
                                        obj.notHovering = False
                                        obj.isHovering = False
                                        self.delete_hover_shape()
                    except Exception:
                        # the Exception here will happen if we try to select on screen, and we have a
                        # newly (and empty) just created Geometry or Excellon object that do not have the
                        # xmin, xmax, ymin, ymax options.
                        # In this case poly_obj creation (see above) will fail
                        pass

        except Exception as e:
            self.log.error("App.on_mouse_move_over_plot() - rel_point1 is not None -> %s" % str(e))
            # self.ui.position_label.setText("")
            # self.ui.rel_position_label.setText("")
            self.ui.update_location_labels(0.0, 0.0, 0.0, 0.0)
            self.app.mouse_pos = [None, None]

    def on_mouse_click_release_over_plot(self, event):
        """
        Callback for the mouse click release over plot. This event is generated by VisPy
        and has been registered in ''self.__init__()''.
        :param event: contains information about the event.
        :return:
        """
        self.app.mouse_down = False

        event_pos = event.pos
        right_button = 2

        pos_canvas = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            try:
                pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
            except TypeError:
                return
        else:
            pos = (pos_canvas[0], pos_canvas[1])

        # if the released mouse button was RMB then test if it was a panning motion or not, if not it was a context
        # canvas menu
        if event.button == right_button and self.ui.popMenu.mouse_is_panning is False:  # right click
            self.on_mouse_context_menu()

        # if the released mouse button was LMB then test if we had a right-to-left selection or a left-to-right
        # selection and then select a type of selection ("enclosing" or "touching")

        if event.button == 1:  # left click
            key_modifier = QtWidgets.QApplication.keyboardModifiers()
            shift_modifier_key = Qt.KeyboardModifier.ShiftModifier
            ctrl_modifier_key = Qt.KeyboardModifier.ControlModifier
            ctrl_shift_modifier_key = ctrl_modifier_key | shift_modifier_key    # noqa

            # this will do click release action for the Plugins
            if key_modifier == shift_modifier_key or key_modifier == ctrl_shift_modifier_key:
                self.on_mouse_and_key_modifiers(position=self.app.mouse_click_pos, modifiers=key_modifier)
                self.on_plugin_mouse_click_release(pos=pos)
                self.app.mouse_click_pos = [pos[0], pos[1]]
                return
            else:
                self.on_plugin_mouse_click_release(pos=pos)

            # the object selection on canvas will not work for App Tools or for Editors
            if self.app.call_source != 'app':
                self.app.mouse_click_pos = [pos[0], pos[1]]
                return

            # it was a double click
            if self.app.doubleclick is True:
                self.app.doubleclick = False

                # The 1st mouse_release of the double click already ran select_objects()
                # which may have toggled the object OFF. Re-select so double-click
                # always leaves the object selected.
                if not self.app.collection.get_selected():
                    self.select_objects()

                if self.app.collection.get_selected():
                    self.ui.notebook.setCurrentWidget(self.ui.properties_tab)
                    if self.ui.splitter.sizes()[0] == 0:
                        self.ui.splitter.setSizes([1, 1])
                    try:
                        self.delete_hover_shape()
                    except Exception as e:
                        self.log.error("App.on_mouse_click_release_over_plot() double click --> Error: %s" % str(e))
                self.app.mouse_click_pos = [pos[0], pos[1]]
                return

            # End moving selection
            if self.app.selection_type is not None:
                # delete previous selection shape
                self.delete_selection_shape()

                try:
                    self.selection_area_handler(self.app.mouse_click_pos, pos, self.app.selection_type)
                    self.app.selection_type = None
                except Exception as e:
                    self.log.error("App.on_mouse_click_release_over_plot() select area --> Error: %s" % str(e))
                    self.app.mouse_click_pos = [pos[0], pos[1]]
                return

            if key_modifier == shift_modifier_key:
                mod_key = 'Shift'
            elif key_modifier == ctrl_modifier_key:
                mod_key = 'Control'
            else:
                mod_key = None

            try:
                if self.app.command_active is None:
                    if mod_key == self.options.get("global_mselect_key", self.defaults.get("global_mselect_key", 'Control')):
                        # If the modifier key is pressed when the LMB is clicked then if the object is selected it will
                        # deselect, and if it's not selected then it will be selected
                        self.select_objects(key='multisel')
                    else:
                        # If there is no active command (self.command_active is None) then we check if
                        # we clicked on an object by checking the bounding limits against mouse click position
                        self.select_objects()

                    self.delete_hover_shape()
            except Exception as e:
                self.log.error("App.on_mouse_click_release_over_plot() select click --> Error: %s" % str(e))
                self.app.mouse_click_pos = [pos[0], pos[1]]
                return

        self.app.mouse_click_pos = [pos[0], pos[1]]

    def on_mouse_and_key_modifiers(self, position, modifiers):
        """
        Called when the mouse is left-clicked on canvas and simultaneously a key modifier
        (Ctrl, AAlt, Shift) is pressed.

        :param position:        A tuple made of the clicked position x, y coordinates
        :param modifiers:       Key modifiers (Ctrl, Alt, Shift or a combination of them)
        :return:
        """

        ctrl_shift_mod = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier  # noqa

        # If the SHIFT key is pressed when LMB is clicked then the coordinates are copied to clipboard
        if modifiers == Qt.KeyboardModifier.ShiftModifier:
            # do not auto open the Project Tab
            self.app.click_noproject = True

            self.app.clipboard.setText(self.options.get("global_point_clipboard_format", self.defaults.get("global_point_clipboard_format", "(%.*f, %.*f)")) %
                                   (self.app.decimals, position[0], self.app.decimals, position[1]))
            self.inform.emit('[success] %s' % _("Copied to clipboard."))
        elif modifiers == ctrl_shift_mod:
            try:
                old_clipb = safe_eval(self.app.clipboard.text())
            except Exception:
                # self.log.error("App.on_mouse_and_key_modifiers() --> %s" % str(err))
                old_clipb = None

            clip_pos_val = (
                self.app.dec_format(position[0], self.app.decimals),
                self.app.dec_format(position[1], self.app.decimals)
            )
            clip_text = "(%s, %s)" % (str(clip_pos_val[0]), str(clip_pos_val[1]))

            if old_clipb is None or old_clipb == '':
                self.app.clipboard.setText(clip_text)
            else:
                if isinstance(old_clipb, list):
                    old_clipb.append(clip_pos_val)
                else:
                    old_clipb = [old_clipb, clip_pos_val]
                self.app.clipboard.setText(str(old_clipb))
            self.inform.emit('[success] %s' % _("Copied to clipboard."))

    def on_mouse_context_menu(self):
        """
        Display a context menu when mouse right-clicking on canvas.

        :return:
        """
        if self.app.inhibit_context_menu is False:
            self.app.cursor = QtGui.QCursor()
            self.app.populate_cmenu_grids()
            self.ui.popMenu.popup(self.app.cursor.pos())

            # if at least one object is Gerber or Excellon enable color changes
            sel_obj_list = self.app.collection.get_selected()
            self.ui.pop_menucolor.setDisabled(True)
            if sel_obj_list:
                self.ui.popmenu_copy.setDisabled(False)
                self.ui.popmenu_delete.setDisabled(False)
                self.ui.popmenu_edit.setDisabled(False)

                self.ui.popmenu_numeric_move.setDisabled(False)
                self.ui.popmenu_move2origin.setDisabled(False)
                self.ui.popmenu_move.setDisabled(False)
                for obj in sel_obj_list:
                    if obj.kind in ["gerber", "excellon"]:
                        self.ui.pop_menucolor.setDisabled(False)
                        break
            else:
                self.ui.popmenu_copy.setDisabled(True)
                self.ui.popmenu_delete.setDisabled(True)
                self.ui.popmenu_edit.setDisabled(True)

                self.ui.popmenu_numeric_move.setDisabled(True)
                self.ui.popmenu_move2origin.setDisabled(True)
                self.ui.popmenu_move.setDisabled(True)

    def selection_area_handler(self, start_pos, end_pos, sel_type):
        """
        Called when the mouse selects by dragging left mouse button on canvas.

        :param start_pos:   mouse position when the selection LMB click was done
        :param end_pos:     mouse position when the left mouse button is released
        :param sel_type:    if True it's a left to right selection (enclosure), if False it's a 'touch' selection
        :return:            None
        """

        # Force canvas update before heavy selection operation
        QtWidgets.QApplication.processEvents()

        poly_selection = Polygon([start_pos, (end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])

        sel_obj_list = []
        collection_list = self.app.collection.get_list()
        for idx, obj in enumerate(collection_list):
            is_plotted = obj.obj_options.get("plot", False)
            if not is_plotted:
                continue

            x_min = obj.obj_options.get("xmin", 0)
            x_max = obj.obj_options.get("xmax", 0)
            y_min = obj.obj_options.get("ymin", 0)
            y_max = obj.obj_options.get("ymax", 0)

            try:
                # it's a line without area
                if x_min == x_max or y_min == y_max:
                    poly_obj = unary_union(obj.solid_geometry).buffer(0.001)
                # it's a geometry with area
                else:
                    poly_obj = Polygon([
                        (x_min, y_min),
                        (x_max, y_min),
                        (x_max, y_max),
                        (x_min, y_max)
                    ])
                if poly_obj.is_empty or not poly_obj.is_valid:
                    continue

                is_selected = poly_obj.within(poly_selection) if sel_type else poly_selection.intersects(poly_obj)
                if is_selected:
                    sel_obj_list.append(idx)
            except Exception as e:
                # the Exception here will happen if we try to select on screen, and we have a newly (and empty)
                # just created Geometry or Excellon object that do not have the xmin, xmax, ymin, ymax options.
                # In this case poly_obj creation (see above) will fail
                self.log.error("App.selection_area_handler() --> %s" % str(e))

        # delete previous selection shape
        self.delete_selection_shape()

        for idx in sel_obj_list:
            sel_obj = collection_list[idx]
            if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)):
                self.draw_selection_shape(sel_obj)

        # make all objects inactive
        self.app.collection.set_all_inactive()
        for idx in sel_obj_list:
            sel_obj = collection_list[idx]
            self.app.collection.set_active(sel_obj.obj_options['name'])
            sel_obj.selection_shape_drawn = True

    def select_objects(self, key=None):
        """
        Will select objects clicked on canvas

        :param key:     a keyboard key. for future use in cumulative selection
        :return:        None
        """

        # list where we store the overlapped objects under our mouse left click position
        if key is None:
            self.app.objects_under_the_click_list = []

        # Populate the list with the overlapped objects on the click position
        curr_x, curr_y = self.app.mouse_click_pos

        try:
            for obj in self.app.all_objects_list:
                # ScriptObject and DocumentObject objects can't be selected
                if obj.kind == 'script' or obj.kind == 'document':
                    continue

                if key == 'multisel' and obj.obj_options['name'] in self.app.objects_under_the_click_list:
                    continue

                if (curr_x >= obj.obj_options['xmin']) and (curr_x <= obj.obj_options['xmax']) and \
                        (curr_y >= obj.obj_options['ymin']) and (curr_y <= obj.obj_options['ymax']):
                    if obj.obj_options['name'] not in self.app.objects_under_the_click_list:
                        if obj.obj_options['plot']:
                            # add objects to the objects_under_the_click list only if the object is plotted
                            # (active and not disabled)
                            self.app.objects_under_the_click_list.append(obj.obj_options['name'])
        except Exception as e:
            self.log.error(
                "Something went bad in App.select_objects(). Create a list of objects under click pos%s" % str(e))

        if self.app.objects_under_the_click_list:
            curr_sel_obj = self.app.collection.get_active()
            # case when there is only an object under the click, and we toggle it
            if len(self.app.objects_under_the_click_list) == 1:
                try:
                    if curr_sel_obj is None:
                        self.app.collection.set_active(self.app.objects_under_the_click_list[0])
                        curr_sel_obj = self.app.collection.get_active()

                        # create the selection box around the selected object
                        if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)) is True:
                            self.draw_selection_shape(curr_sel_obj)
                            curr_sel_obj.selection_shape_drawn = True
                    elif curr_sel_obj.obj_options['name'] not in self.app.objects_under_the_click_list:
                        self.app.collection.on_objects_selection(False)
                        self.delete_selection_shape()
                        curr_sel_obj.selection_shape_drawn = False

                        self.app.collection.set_active(self.app.objects_under_the_click_list[0])
                        curr_sel_obj = self.app.collection.get_active()
                        # create the selection box around the selected object
                        if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)) is True:
                            self.draw_selection_shape(curr_sel_obj)
                            curr_sel_obj.selection_shape_drawn = True
                        self.selected_message(curr_sel_obj=curr_sel_obj)
                    elif curr_sel_obj.selection_shape_drawn is False:
                        if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)) is True:
                            self.draw_selection_shape(curr_sel_obj)
                            curr_sel_obj.selection_shape_drawn = True
                    else:
                        self.app.collection.on_objects_selection(False)
                        self.delete_selection_shape()
                        if self.app.call_source != 'app':
                            self.app.call_source = 'app'
                    self.selected_message(curr_sel_obj=curr_sel_obj)
                except Exception as e:
                    self.log.error("Something went bad in App.select_objects(). Single click selection. %s" % str(e))
            else:
                # If there is no selected object
                try:
                    # make active the first element of the overlapped objects list
                    if self.app.collection.get_active() is None:
                        self.app.collection.set_active(self.app.objects_under_the_click_list[0])
                        self.app.collection.get_by_name(self.app.objects_under_the_click_list[0]).selection_shape_drawn = True

                    name_sel_obj = self.app.collection.get_active().obj_options['name']
                    # In case that there is a selected object, but it is not in the overlapped object list
                    # make that object inactive and activate the first element in the overlapped object list
                    if name_sel_obj not in self.app.objects_under_the_click_list:
                        self.app.collection.set_inactive(name_sel_obj)
                        name_sel_obj = self.app.objects_under_the_click_list[0]
                        self.app.collection.set_active(name_sel_obj)
                    else:
                        sel_idx = self.app.objects_under_the_click_list.index(name_sel_obj)
                        self.app.collection.set_all_inactive()
                        self.app.collection.set_active(
                            self.app.objects_under_the_click_list[(sel_idx + 1) % len(self.app.objects_under_the_click_list)])

                    curr_sel_obj = self.app.collection.get_active()
                    # delete the possible selection box around a possible selected object
                    self.delete_selection_shape()
                    curr_sel_obj.selection_shape_drawn = False

                    # create the selection box around the selected object
                    if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)) is True:
                        self.draw_selection_shape(curr_sel_obj)
                        curr_sel_obj.selection_shape_drawn = True
                    self.selected_message(curr_sel_obj=curr_sel_obj)

                except Exception as e:
                    self.log.error(
                        "Something went bad in App.select_objects(). Cycle the objects under cursor. %s" % str(e))
        else:
            try:
                # deselect everything
                self.app.collection.on_objects_selection(False)
                # delete the possible selection box around a possible selected object
                self.delete_selection_shape()

                for o in self.app.collection.get_list():
                    o.selection_shape_drawn = False

                # and as a convenience move the focus to the Project tab because Selected tab is now empty but
                # only when working on App
                if self.app.call_source == 'app':
                    if self.app.click_noproject is False:
                        # if the Tool Tab is in focus don't change focus to Project Tab
                        if not self.ui.notebook.currentWidget() is self.ui.plugin_tab:
                            self.ui.notebook.setCurrentWidget(self.ui.project_tab)
                    else:
                        # restore auto open the Project Tab
                        self.app.click_noproject = False

                    # delete any text in the status bar, implicitly the last object name that was selected
                    # self.inform.emit("")
                else:
                    self.app.call_source = 'app'
            except Exception as e:
                self.log.error("Something went bad in App.select_objects(). Deselect everything. %s" % str(e))

    def selected_message(self, curr_sel_obj):
        """
        Will print a colored message on status bar when the user selects an object on canvas.

        :param curr_sel_obj:    Application object that have geometry: Geometry, Gerber, Excellon, CNCJob
        :type curr_sel_obj:
        :return:
        :rtype:
        """
        if curr_sel_obj:
            if curr_sel_obj.kind == 'gerber':
                self.inform.emit('[selected] <span style="color:{color};">{name}</span> {tx}'.format(
                    color='green',
                    name=str(curr_sel_obj.obj_options['name']),
                    tx=_("selected"))
                )

            elif curr_sel_obj.kind == 'excellon':
                self.inform.emit('[selected] <span style="color:{color};">{name}</span> {tx}'.format(
                    color='brown',
                    name=str(curr_sel_obj.obj_options['name']),
                    tx=_("selected"))
                )

            elif curr_sel_obj.kind == 'cncjob':
                self.inform.emit('[selected] <span style="color:{color};">{name}</span> {tx}'.format(
                    color='blue',
                    name=str(curr_sel_obj.obj_options['name']),
                    tx=_("selected"))
                )

            elif curr_sel_obj.kind == 'geometry':
                self.inform.emit('[selected] <span style="color:{color};">{name}</span> {tx}'.format(
                    color='red',
                    name=str(curr_sel_obj.obj_options['name']),
                    tx=_("selected"))
                )

    def on_plugin_mouse_click_release(self, pos):
        """
        Handle specific tasks in the Plugins for the mouse click release

        :param pos:     mouse position
        :type pos:
        :return:
        """

        if self.ui.notebook.currentWidget().objectName() != "plugin_tab":
            return

        tab_idx = self.ui.notebook.currentIndex()
        for plugin in self.app.app_plugins:
            try:
                # execute this only for the current active plugin
                if self.ui.notebook.tabText(tab_idx) != plugin.pluginName:
                    continue
                try:
                    plugin.on_plugin_mouse_click_release(pos)
                except AttributeError:
                    # not all plugins have this implemented
                    # print("This does not have it", self.ui.notebook.tabText(tab_idx))
                    pass
            except AttributeError:
                pass

    def on_plugin_mouse_move(self, pos):
        """
        Handle specific tasks in the Plugins for the mouse move

        :param pos:     mouse position
        :return:
        :rtype:
        """

        if self.ui.notebook.currentWidget().objectName() != "plugin_tab":
            return

        tab_idx = self.ui.notebook.currentIndex()
        plugin_name = self.ui.notebook.tabText(tab_idx)
        for plugin in self.app.app_plugins:
            # execute this only for the current active plugin
            try:
                if plugin_name != plugin.pluginName:
                    continue
                try:
                    plugin.on_plugin_mouse_move(pos)
                except AttributeError:
                    # not all plugins have this implemented
                    # print("This does not have it", self.ui.notebook.tabText(tab_idx))
                    pass
            except AttributeError:
                pass

    def delete_hover_shape(self):
        self.app.hover_shapes.clear()
        self.app.hover_shapes.redraw()

    def draw_hover_shape(self, sel_obj, color=None):
        """

        :param sel_obj: The object for which the hover shape must be drawn
        :param color:   The color of the hover shape
        :return:        None
        """

        if sel_obj is None:
            return

        if color:
            face = color[:-2] + str(hex(int(0.2 * 255)))[2:]
            outline = color[:-2] + str(hex(int(0.8 * 255)))[2:]
        else:
            face = self.options.get('global_sel_fill', self.defaults.get('global_sel_fill', '#a5a5ffbf'))[:-2] + str(hex(int(0.2 * 255)))[2:]
            outline = self.options.get('global_sel_line', self.defaults.get('global_sel_line', '#0000ffbf'))

        if hasattr(self.app.hover_shapes, 'add_hover'):
            bounds = tuple(float(sel_obj.obj_options[key]) for key in ('xmin', 'ymin', 'xmax', 'ymax'))
            self.app.hover_shapes.add_hover(bounds, self.app.app_units, outline, face)
            return

        pt1 = (float(sel_obj.obj_options['xmin']), float(sel_obj.obj_options['ymin']))
        pt2 = (float(sel_obj.obj_options['xmax']), float(sel_obj.obj_options['ymin']))
        pt3 = (float(sel_obj.obj_options['xmax']), float(sel_obj.obj_options['ymax']))
        pt4 = (float(sel_obj.obj_options['xmin']), float(sel_obj.obj_options['ymax']))

        hover_rect = Polygon([pt1, pt2, pt3, pt4])
        if self.app.app_units.upper() == 'MM':
            hover_rect = hover_rect.buffer(-0.1)
            hover_rect = hover_rect.buffer(0.2)

        else:
            hover_rect = hover_rect.buffer(-0.00393)
            hover_rect = hover_rect.buffer(0.00787)

        self.app.hover_shapes.add(hover_rect, color=outline, face_color=face, update=True, layer=0, tolerance=None)

    def delete_selection_shape(self):
        self.app.sel_shapes.clear()
        self.app.sel_shapes.redraw()

    def draw_selection_shape(self, sel_obj, color=None):
        """
        Will draw a selection shape around the selected object.

        :param sel_obj: The object for which the selection shape must be drawn
        :param color:   The color for the selection shape.
        :return:        None
        """

        if sel_obj is None:
            return

        x_min = sel_obj.obj_options.get("xmin", 0.0)
        y_min = sel_obj.obj_options.get("ymin", 0.0)
        x_max = sel_obj.obj_options.get("xmax", 0.0)
        y_max = sel_obj.obj_options.get("ymax", 0.0)

        # it's a line without area
        if x_min == x_max or y_min == y_max:
            sel_rect = unary_union(sel_obj.solid_geometry).buffer(0.100001)
        # it's a geometry with area
        else:
            sel_rect = Polygon([
                (x_min, y_min),
                (x_max, y_min),
                (x_max, y_max),
                (x_min, y_max)
            ])

        b_sel_rect = None
        try:
            if self.app.app_units.upper() == 'MM':
                b_sel_rect = sel_rect.buffer(-0.1)
                b_sel_rect = b_sel_rect.buffer(0.2)
            else:
                b_sel_rect = sel_rect.buffer(-0.00393)
                b_sel_rect = b_sel_rect.buffer(0.00787)
        except Exception:
            pass

        if b_sel_rect is None or b_sel_rect.is_empty or not b_sel_rect.is_valid:
            b_sel_rect = sel_rect

        if self.options.get('global_selection_shape_as_line', self.defaults.get('global_selection_shape_as_line', False)) is True:
            b_sel_rect = b_sel_rect.exterior

        if color:
            face = color[:-2] + str(hex(int(0.2 * 255)))[2:]
            outline = color[:-2] + str(hex(int(0.8 * 255)))[2:]
        else:
            face = self.options.get('global_sel_fill', self.defaults.get('global_sel_fill', '#a5a5ffbf'))[:-2] + str(hex(int(0.2 * 255)))[2:]
            outline = self.options.get('global_sel_line', self.defaults.get('global_sel_line', '#0000ffbf'))[:-2] + str(hex(int(0.8 * 255)))[2:]

        self.app.sel_objects_list.append(
            self.app.sel_shapes.add(b_sel_rect, color=outline, face_color=face, update=True, layer=0, tolerance=None)
        )

    def draw_moving_selection_shape(self, old_coords, coords, **kwargs):
        """
        Will draw a selection shape when dragging mouse on canvas.

        :param old_coords:  Old coordinates
        :param coords:      New coordinates
        :param kwargs:      Keyword arguments
        :return:
        """

        if 'color' in kwargs:
            color = kwargs['color']
        else:
            color = self.options.get('global_sel_line', self.defaults.get('global_sel_line', '#0000ffbf'))

        if 'face_color' in kwargs:
            face_color = kwargs['face_color']
        else:
            face_color = self.options.get('global_sel_fill', self.defaults.get('global_sel_fill', '#a5a5ffbf'))

        if 'face_alpha' in kwargs:
            face_alpha = kwargs['face_alpha']
        else:
            face_alpha = 0.3

        line_only = self.options.get(
            'global_selection_shape_as_line', self.defaults.get('global_selection_shape_as_line', False)
        ) is True
        color_t = face_color[:-2] + str(hex(int(face_alpha * 255)))[2:]
        if hasattr(self.app.sel_shapes, 'set_rectangle'):
            self.app.sel_shapes.set_rectangle(old_coords, coords, color, None if line_only else color_t)
            return

        x0, y0 = old_coords
        x1, y1 = coords

        pt1 = (x0, y0)
        pt2 = (x1, y0)
        pt3 = (x1, y1)
        pt4 = (x0, y1)
        sel_rect = Polygon([pt1, pt2, pt3, pt4])

        if line_only:
            sel_rect = sel_rect.exterior

        # Replace the rubber band in one upload; object visuals use separate collections.
        self.app.sel_shapes.clear()
        self.app.sel_shapes.add(sel_rect, color=color, face_color=color_t, update=True, layer=0, tolerance=None)
