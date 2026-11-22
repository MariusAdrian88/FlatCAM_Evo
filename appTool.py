# ########################################################## ##
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ########################################################## ##

from PyQt6 import QtGui, QtWidgets, QtCore
from shapely import Polygon, LineString
from appGUI.GUIElements import FCCheckBox, FCFrame, FCLabel, GLay

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AppTool(QtWidgets.QWidget):

    pluginName = "FlatCAM Generic Tool"

    def __init__(self, app, parent=None):
        """

        :param app:         The application this tool will run in.
        :type app:          appMain.App
        :param parent:      Qt Parent
        :return:            AppTool
        """
        QtWidgets.QWidget.__init__(self, parent)

        self.app = app
        self.decimals = self.app.decimals

        # self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum, QtWidgets.QSizePolicy.Policy.Maximum)

        self.layout = QtWidgets.QVBoxLayout()
        self.setLayout(self.layout)

        self.menuAction = None

    def connect_selection_signal(self):
        """
        Connect on_object_selection_changed() to app.proj_selection_changed, exactly 1 time. The close of the
        plugin tab disconnects every slot of that signal, so a plugin calls this at each run: a slot that is still
        connected is removed first, a second call makes no second connection.
        """
        signal = self.app.proj_selection_changed
        try:
            signal.disconnect(self.on_object_selection_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        signal.connect(self.on_object_selection_changed)

    def uninstall(self):
        """
        Release what this plugin connected outside of itself. App.remove_tools() calls it for each installed plugin
        before init_tools() creates the new instances (New Project, project open, Close All). A plugin that
        connects a lambda that captures self, or registers itself in a global registry, overrides it and calls
        AppTool.uninstall(self).
        """
        try:
            self.app.proj_selection_changed.disconnect(self.on_object_selection_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass  # not connected, or the plugin has no selection slot

    def set_project_collection(self, old_collection, collection):
        """Move project selectors without rebuilding the plugin. run() resets the tool's form when opened."""
        if old_collection is collection:
            return
        for combo in self.findChildren(QtWidgets.QComboBox):
            if combo.model() is not old_collection:
                continue
            row = combo.rootModelIndex().row()
            blocker = QtCore.QSignalBlocker(combo)
            follows_insertions = getattr(combo, 'is_last', False)
            if follows_insertions:
                old_collection.rowsInserted.disconnect(combo.on_model_changed)
            combo.setModel(collection)
            if row >= 0:
                combo.setRootModelIndex(collection.index(row, 0, QtCore.QModelIndex()))
            # Never carry a selection (or an object-change callback) from the previous project.
            combo.setCurrentIndex(-1)
            if follows_insertions:
                collection.rowsInserted.connect(combo.on_model_changed)
            del blocker

    def add_object_creation_controls(self, has_source=True):
        """Initialize plugin-local creation options from the application preferences."""
        label = getattr(self.ui, 'gen_param_label', None)
        if label is not None:
            layout = label.parentWidget().layout()
            grid = layout.itemAt(layout.indexOf(label) + 1).widget().layout()
        else:
            self.ui.gen_param_label = FCLabel(_("Common Parameters"), color='indigo', bold=True)
            frame = FCFrame()
            grid = GLay(v_spacing=5, h_spacing=3)
            frame.setLayout(grid)
            index = self.layout.count()
            if index and self.layout.itemAt(index - 1).spacerItem() is not None:
                index -= 1
            self.layout.insertWidget(index, self.ui.gen_param_label)
            self.layout.insertWidget(index + 1, frame)

        # AutoHide and Autoswitch share one row
        row_lay = QtWidgets.QHBoxLayout()
        row_lay.setContentsMargins(0, 0, 0, 0)
        if has_source:
            self.ui.object_autohide_cb = FCCheckBox(_("Object AutoHide"))
            self.ui.object_autohide_cb.setToolTip(
                _("When checked, once a new object is generated the source object is hidden on canvas."))
            self.ui.object_autohide_cb.toggled.connect(
                lambda value: setattr(self, 'object_autohide', value))
            row_lay.addWidget(self.ui.object_autohide_cb)

        self.ui.object_autoswitch_cb = FCCheckBox(_("Object Autoswitch"))
        self.ui.object_autoswitch_cb.setToolTip(
            _("When checked, on new object creation, switch to Properties."))
        self.ui.object_autoswitch_cb.toggled.connect(
            lambda value: setattr(self, 'object_autoswitch', value))
        row_lay.addWidget(self.ui.object_autoswitch_cb)
        row_lay.addStretch()
        grid.addLayout(row_lay, grid.rowCount(), 0, 1, 2)
        self.reset_object_creation_controls()

    def reset_object_creation_controls(self):
        self.object_autohide = False
        for name in ('object_autohide', 'object_autoswitch'):
            checkbox = getattr(self.ui, name + '_cb', None)
            if checkbox is not None:
                key = 'global_' + name
                value = self.app.options.get(key, self.app.defaults.get(key, True))
                setattr(self, name, value)
                checkbox.set_value(value)

    def new_object(self, kind, name, initialize, source=None, from_tcl=False, **kwargs):
        """Carry this generator's source and options to GUI-thread completion."""
        if from_tcl or 'object_autoswitch' not in self.__dict__:
            return self.app.app_obj.new_object(kind, name, initialize, **kwargs)
        context = (source, self.object_autohide, self.object_autoswitch)

        def plugin_initialize(obj, app):
            result = initialize(obj, app)
            obj._plugin_creation_context = context
            return result

        return self.app.app_obj.new_object(kind, name, plugin_initialize, **kwargs)

    def install(self, icon=None, separator=None, shortcut=None, **kwargs):
        before = None

        # 'pos' is the menu where the Action has to be installed
        # if no 'pos' kwarg is provided then by default our Action will be installed in the menu_plugins
        # as it previously was
        if 'pos' in kwargs:
            pos = kwargs['pos']
        else:
            pos = self.app.ui.menu_plugins

        # 'before' is the Action in the menu stated by 'pos' kwarg, before which we want our Action to be installed
        # if 'before' kwarg is not provided, by default our Action will be added in the last place.
        if 'before' in kwargs:
            before = (kwargs['before'])

        # create the new Action
        self.menuAction = QtGui.QAction(self)
        # if provided, add an icon to this Action
        if icon is not None:
            self.menuAction.setIcon(icon)

        # set the text name of the Action, which will be displayed in the menu
        if shortcut is None:
            self.menuAction.setText(self.pluginName)
        else:
            self.menuAction.setText(self.pluginName + '\t%s' % shortcut)

        # add a ToolTip to the new Action
        # self.menuAction.setToolTip(self.toolTip) # currently not available

        # insert the action in the position specified by 'before' and 'pos' kwargs
        pos.insertAction(before, self.menuAction)
        # App.remove_tools() removes it at the next init_tools(), also from a menu that is not the Plugins menu
        self.app.plugin_menu_actions.append((pos, self.menuAction))

        # if separator parameter is True add a Separator after the newly created Action
        if separator is True:
            self.app.plugin_menu_actions.append((pos, pos.addSeparator()))

        self.menuAction.triggered.connect(lambda: self.run(toggle=True))

    @staticmethod
    def release_plugin_panel(app, keep=None):
        """
        Take the widget out of the Plugin tab. Every path that replaces or empties the panel goes through here: the
        plugin that owned it (cleans up when another one replaces it) is told with on_panel_replaced().

        :return: the widget that was taken out, or None.
        """
        previous = app.ui.plugin_scroll_area.takeWidget()
        if previous is not None and previous is not keep:
            on_replaced = getattr(previous, 'on_panel_replaced', None)
            if callable(on_replaced):
                on_replaced()
        return previous

    def confirm_panel_release(self, reason):
        """The panel of this plugin is about to close or be replaced: False cancels the action."""
        return True

    @staticmethod
    def confirm_panel_change(app, keep=None, reason='panel'):
        """Ask the plugin that owns the Plugin tab (a connected GCode sender) before another one replaces it."""
        panel = app.ui.plugin_scroll_area.widget()
        if isinstance(panel, AppTool) and panel is not keep:
            return panel.confirm_panel_release(reason)
        return True

    def run(self):

        if self.app.plugin_tab_locked is True:
            return
        if not self.confirm_panel_change(self.app, keep=self):
            return
        # Remove anything else in the appGUI; a plugin that cleans up when another one replaces it is told
        self.release_plugin_panel(self.app, keep=self)

        # Put ourselves in the appGUI
        self.app.ui.plugin_scroll_area.setWidget(self)

        # Switch notebook to tool page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

        # Set the tool name as the widget object name
        self.app.ui.plugin_scroll_area.widget().setObjectName(self.pluginName)

        self.show()

    def clear_ui(self, layout):
        # for item in reversed(range(layout.count())):
        #     lay_item = layout.itemAt(item)
        #
        #     # in case that the widget is None
        #     try:
        #         widget = lay_item.widget()
        #         widget.setParent(None)
        #     except AttributeError:
        #         pass

        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                else:
                    self.clear_ui(item.layout())

    def draw_tool_selection_shape(self, old_coords, coords, **kwargs):
        """

        :param old_coords: old coordinates
        :param coords: new coordinates
        :param kwargs:
        :return:
        """

        if 'shapes_storage' in kwargs:
            s_storage = kwargs['shapes_storage']
        else:
            s_storage = self.app.tool_shapes

        if 'color' in kwargs:
            color = kwargs['color']
        else:
            color = self.app.options.get(
                'global_sel_line',
                self.app.defaults.get('global_sel_line', self.app.defaults.factory_defaults.get('global_sel_line'))
            )

        if 'face_color' in kwargs:
            face_color = kwargs['face_color']
        else:
            face_color = self.app.options.get(
                'global_sel_fill',
                self.app.defaults.get('global_sel_fill', self.app.defaults.factory_defaults.get('global_sel_fill'))
            )

        if 'face_alpha' in kwargs:
            face_alpha = kwargs['face_alpha']
        else:
            face_alpha = 0.3

        x0, y0 = old_coords
        x1, y1 = coords

        pt1 = (x0, y0)
        pt2 = (x1, y0)
        pt3 = (x1, y1)
        pt4 = (x0, y1)
        sel_rect = Polygon([pt1, pt2, pt3, pt4])

        # color_t = Color(face_color)
        # color_t.alpha = face_alpha

        color_t = face_color[:-2] + str(hex(int(face_alpha * 255)))[2:]

        s_storage.add(sel_rect, color=color, face_color=color_t, update=True, layer=0, tolerance=None)
        s_storage.redraw()

    def draw_selection_shape_polygon(self, points, **kwargs):
        """

        :param points: a list of points from which to create a Polygon
        :param kwargs:
        :return:
        """

        if 'shapes_storage' in kwargs:
            s_storage = kwargs['shapes_storage']
        else:
            s_storage = self.app.tool_shapes

        if 'color' in kwargs:
            color = kwargs['color']
        else:
            color = self.app.options.get(
                'global_sel_line',
                self.app.defaults.get('global_sel_line', self.app.defaults.factory_defaults.get('global_sel_line'))
            )

        if 'face_color' in kwargs:
            face_color = kwargs['face_color']
        else:
            face_color = self.app.options.get(
                'global_sel_fill',
                self.app.defaults.get('global_sel_fill', self.app.defaults.factory_defaults.get('global_sel_fill'))
            )

        if 'face_alpha' in kwargs:
            face_alpha = kwargs['face_alpha']
        else:
            face_alpha = 0.3

        if len(points) < 3:
            sel_rect = LineString(points)
        else:
            sel_rect = Polygon(points)

        # color_t = Color(face_color)
        # color_t.alpha = face_alpha

        color_t = face_color[:-2] + str(hex(int(face_alpha * 255)))[2:]

        s_storage.add(sel_rect, color=color, face_color=color_t, update=True, layer=0, tolerance=None)
        s_storage.redraw()

    def delete_tool_selection_shape(self, **kwargs):
        """

        :param kwargs:
        :return:
        """

        if 'shapes_storage' in kwargs:
            s_storage = kwargs['shapes_storage']
        else:
            s_storage = self.app.tool_shapes

        s_storage.clear()
        s_storage.redraw()

    def draw_moving_selection_shape_poly(self, points, data, **kwargs):
        """

        :param points:
        :param data:
        :param kwargs:
        :return:
        """

        if 'shapes_storage' in kwargs:
            s_storage = kwargs['shapes_storage']
        else:
            s_storage = self.app.sel_shapes

        s_storage.clear()

        if 'color' in kwargs:
            color = kwargs['color']
        else:
            color = self.app.options.get(
                'global_sel_line',
                self.app.defaults.get('global_sel_line', self.app.defaults.factory_defaults.get('global_sel_line'))
            )

        if 'face_color' in kwargs:
            face_color = kwargs['face_color']
        else:
            face_color = self.app.options.get(
                'global_sel_fill',
                self.app.defaults.get('global_sel_fill', self.app.defaults.factory_defaults.get('global_sel_fill'))
            )

        if 'face_alpha' in kwargs:
            face_alpha = kwargs['face_alpha']
        else:
            face_alpha = 0.3

        temp_points = [x for x in points]
        try:
            if data != temp_points[-1]:
                temp_points.append(data)
        except IndexError:
            s_storage.redraw()
            return

        l_points = len(temp_points)
        if l_points == 2:
            geo = LineString(temp_points)
        elif l_points > 2:
            geo = Polygon(temp_points)
        else:
            s_storage.redraw()
            return

        color_t = face_color[:-2] + str(hex(int(face_alpha * 255)))[2:]
        color_t_error = "#00000000"

        if geo.is_valid and not geo.is_empty:
            s_storage.add(geo, color=color, face_color=color_t, update=False, layer=0, tolerance=None)
        elif not geo.is_valid:
            s_storage.add(geo, color="red", face_color=color_t_error, update=False, layer=0, tolerance=None)

        s_storage.redraw()

    def delete_moving_selection_shape(self, **kwargs):
        """

        :param kwargs:
        :return:
        """

        if 'shapes_storage' in kwargs:
            s_storage = kwargs['shapes_storage']
        else:
            s_storage = self.app.sel_shapes

        s_storage.clear()
        s_storage.redraw()

    def on_plugin_mouse_click_release(self, pos):
        # this should be implemented in the descendants, the Plugin classes
        pass

    def on_plugin_mouse_move(self, pos):
        # this should be implemented in the descendants, the Plugin classes
        pass

    def on_plugin_cleanup(self):
        # this should be implemented in the descendants, the Plugin classes
        pass

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

    def sizeHint(self):
        """
        I've overloaded this just in case I will need to make changes in the future to enforce dimensions
        :return:
        """
        default_hint_size = super(AppTool, self).sizeHint()
        return QtCore.QSize(default_hint_size.width(), default_hint_size.height())


class AppToolEditor(AppTool):

    def run(self):
        super(AppToolEditor, self).run()

        # TODO Hack, should find the root cause and fix
        # for whatever reason the stylesheet for dark mode is lost at some point here, so we should reapply it for the
        # QWidget
        # Not with the native styling: a 'QWidget' rule makes the windows11 style draw no checked radio indicator.
        if self.app.options.get('global_styling', self.app.defaults.get('global_styling')) == 'default':
            return
        if self.app.options.get(
                'global_theme',
                self.app.defaults.get('global_theme', self.app.defaults.factory_defaults.get('global_theme'))
        ) not in ['default', 'light']:
            super(AppTool, self).setStyleSheet(
                '''
                QWidget {
                    background-color: rgba(32.000, 33.000, 36.000, 1.000);
                    color: rgba(170.000, 170.000, 170.000, 1.000);
                    selection-background-color: rgba(138.000, 180.000, 247.000, 1.000);
                    selection-color: rgba(32.000, 33.000, 36.000, 1.000);
                }
                '''
            )
