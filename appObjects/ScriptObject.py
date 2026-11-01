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

from copy import deepcopy

from PyQt6 import QtCore, QtGui, QtWidgets

from appEditors.appTextEditor import AppTextEditor
from appObjects.AppObjectTemplate import FlatCAMObj
from appGUI.ObjectUI import ScriptObjectUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ScriptObject(FlatCAMObj):
    """
    Represents a TCL script object.
    """
    # Dispatch metadata, read by AppObject, ObjectCollection and appPlotManager instead of kind chains.
    # status_color: color of the object name in the status bar message (None: no message).
    status_color = 'orange'
    # tool_option_prefix: extra application options (with this prefix) copied into obj_options on creation.
    tool_option_prefix = None
    # inherits_mill_options: the tools_mill_* and tools_* options are copied into obj_options on creation.
    inherits_mill_options = False
    # has_plot_colors: the object has user settable fill / outline plot colors.
    has_plot_colors = False
    # plot_default_fill_option / plot_default_line_option: option keys of the colors restored by "Default".
    plot_default_fill_option = None
    plot_default_line_option = None
    # alpha_fill_option: option key whose color alpha is the default alpha level.
    # default_alpha_level: fixed default alpha level when alpha_fill_option is None.
    alpha_fill_option = None
    default_alpha_level = None
    ui_type = ScriptObjectUI

    def __init__(self, name, app):
        self.app = app
        self.decimals = self.app.decimals

        self.app.log.debug("Creating a ScriptObject object...")
        FlatCAMObj.__init__(self, name, app=app)

        self.kind = "script"

        self.obj_options.update({
            "plot": True,
            "type": 'Script',
            "source_file": '',
        })

        self.units = ''

        self.script_editor_tab = None

        self.ser_attrs = ['obj_options', 'kind', 'source_file', 'script_filename']
        self.source_file = ''
        self.script_code = ''
        self.script_filename = ''

        self.units_found = self.app.app_units

    def copy_from(self, src):
        """
        Make this (new, empty) ScriptObject a copy of ``src``: the text (source_file). Used by ``on_copy_command``.
        Not copied, on purpose: obj_options (the new object takes the current app defaults) and the editor state.

        :param src: the ScriptObject to copy
        :return:    None
        """
        self.source_file = deepcopy(getattr(src, 'source_file', self.source_file))

    def set_ui(self, ui):
        """
        Sets the Object UI in Selected Tab for the FlatCAM Script type of object.
        :param ui:
        :return:
        """
        FlatCAMObj.set_ui(self, ui)
        self.app.log.debug("ScriptObject.set_ui()")

        assert isinstance(self.ui, ScriptObjectUI), \
            "Expected a ScriptObjectUI, got %s" % type(self.ui)

        self.units = self.app.app_units.upper()
        self.units_found = self.app.app_units

        # Fill form fields only on object create
        self.to_form()

        # Show/Hide Advanced Options
        app_mode = self._app_option("global_app_level")
        self.change_level(app_mode)

        self.script_editor_tab = AppTextEditor(app=self.app, plain_text=True, parent=self.app.ui, language='tcl')
        self.script_editor_tab.script_owner = self
        self.script_editor_tab.code_editor.textChanged.connect(self.on_source_changed)

        # tab_here = False
        # # try to not add too many times a tab that it is already installed
        # for idx in range(self.app.ui.plot_tab_area.count()):
        #     if self.app.ui.plot_tab_area.widget(idx).objectName() == self.obj_options['name']:
        #         tab_here = True
        #         break
        #
        # # add the tab if it is not already added
        # if tab_here is False:
        #     self.app.ui.plot_tab_area.addTab(self.script_editor_tab, '%s' % _("Script Editor"))
        #     self.script_editor_tab.setObjectName(self.obj_options['name'])

        # self.app.ui.plot_tab_area.addTab(self.script_editor_tab, '%s' % _("Script Editor"))
        # self.script_editor_tab.setObjectName(self.obj_options['name'])

        # first clear previous text in text editor (if any)
        # self.script_editor_tab.code_editor.clear()
        # self.script_editor_tab.code_editor.setReadOnly(False)

        self.ui.autocomplete_cb.set_value(self._app_option('script_autocompleter'))
        self.on_autocomplete_changed(state=self._app_option('script_autocompleter'))

        self.script_editor_tab.buttonRun.show()

        # Switch plot_area to Script Editor tab
        self.app.ui.plot_tab_area.setCurrentWidget(self.script_editor_tab)

        flt = "FlatCAM Scripts (*.FlatScript);;All Files (*.*)"

        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.script_editor_tab.set_file_filter(flt)
        self.script_editor_tab.set_save_handler(self.handle_save)
        save_menu = QtWidgets.QMenu(self.script_editor_tab.buttonSave)
        save_menu.addAction(_('Save As'), lambda: self.script_editor_tab.handleSaveGCode(filt=flt))
        self.script_editor_tab.buttonSave.setMenu(save_menu)
        self.script_editor_tab.buttonSave.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        self.script_editor_tab.buttonRun.clicked.connect(self.handle_run_code)
        self.script_editor_tab.handleTextChanged()

        self.ui.autocomplete_cb.stateChanged.connect(self.on_autocomplete_changed)

        self.ser_attrs = ['obj_options', 'kind', 'source_file', 'script_filename']

        # ---------------------------------------------------- #
        # ----------- LOAD THE TEXT SOURCE FILE -------------- #
        # ---------------------------------------------------- #
        self.app.proc_container.view.set_busy('%s...' % _("Loading"))
        self.script_editor_tab.t_frame.hide()

        try:
            # self.script_editor_tab.code_editor.setPlainText(self.source_file)
            self.script_editor_tab.load_text(self.source_file, move_to_end=True)
        except Exception as e:
            self.app.log.error("ScriptObject.set_ui() --> %s" % str(e))

        self.script_editor_tab.t_frame.show()

        self.app.proc_container.view.set_idle()
        self.build_ui()

    def build_ui(self):
        if self.ui is None:
            self.set_ui(self.ui_type(app=self.app))
            return
        FlatCAMObj.build_ui(self)

        tab_here = False
        # try to not add too many times a tab that it is already installed
        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.widget(idx) is self.script_editor_tab:
                tab_here = True
                break

        # add the tab if it is not already added
        if tab_here is False:
            self.app.ui.plot_tab_area.addTab(self.script_editor_tab, '%s' % _("Script Editor"))
            self.script_editor_tab.setObjectName(self.obj_options['name'] + "_editor_tab")
            self.app.ui.plot_tab_area.setCurrentWidget(self.script_editor_tab)

    def on_level_changed(self, checked):
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: green;
                                                }
                                                """)

        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: red;
                                                }
                                                """)

    def parse_file(self, filename):
        """
        Will set an attribute of the object, self.source_file, with the parsed data.

        :param filename:    Tcl Script file to parse
        :return:            None
        """
        with open(filename, "r", encoding='utf-8-sig') as opened_script:
            script_content = opened_script.read()

        self.source_file = script_content
        self.script_filename = filename

    def handle_run_code(self):
        self.on_source_changed()
        result = self.app.script_processing(self.source_file, origin=self.script_filename, owner=self)
        line = getattr(self.app, 'script_error_line', None)
        if line is not None and self.script_editor_tab is not None:
            edit = self.script_editor_tab.code_editor
            block = edit.document().findBlockByNumber(line - 1)
            edit.setTextCursor(QtGui.QTextCursor(block))
            edit.ensureCursorVisible()
        return result

    def on_source_changed(self):
        text = self.script_editor_tab.code_editor.toPlainText()
        if text != self.source_file:
            self.source_file = text
            self.mark_project_dirty()

    def mark_project_dirty(self):
        collection = getattr(self, '_script_collection', None)
        if collection is not None and collection is not getattr(self.app, 'collection', None):
            for context in getattr(getattr(self.app, 'projects', None), 'contexts', []):
                if context.collection is collection:
                    context.should_we_save = True
                    return
        else:
            self.app.should_we_save = True

    def handle_save(self):
        if not self.script_filename:
            return self.script_editor_tab.handleSaveGCode(filt='FlatCAM Scripts (*.FlatScript);;All Files (*.*)')
        self.on_source_changed()
        if self.save_file() == 'fail':
            return 'fail'
        self.script_editor_tab.buttonSave.setStyleSheet('')
        self.script_editor_tab.buttonSave.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        if self._app_option('global_open_style') is False:
            self.app.file_opened.emit('script', self.script_filename)
        self.app.file_saved.emit('script', self.script_filename)
        self.app.inform.emit('%s: %s' % (_('Saved to'), self.script_filename))

    def save_file(self, filename=None):
        """Atomically save canonical text, without accessing an editor from a save worker."""
        filename = filename or self.script_filename
        if not filename:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('Choose a script filename before saving.'))
            return 'fail'
        file = QtCore.QSaveFile(filename)
        file.setDirectWriteFallback(False)
        data = self.source_file.encode('utf-8')
        if not file.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
            self.app.inform.emit('[ERROR_NOTCL] %s' % file.errorString())
            return 'fail'
        if file.write(data) != len(data):
            message = file.errorString()
            file.cancelWriting()
            self.app.inform.emit('[ERROR_NOTCL] %s' % message)
            return 'fail'
        if not file.commit():
            self.app.inform.emit('[ERROR_NOTCL] %s' % file.errorString())
            return 'fail'
        if self.script_filename != filename:
            self.script_filename = filename
            self.mark_project_dirty()
        return None

    def on_autocomplete_changed(self, state):
        if state:
            self.script_editor_tab.code_editor.completer_enable = True
        else:
            self.script_editor_tab.code_editor.completer_enable = False

    def mirror(self, axis, point):
        pass

    def offset(self, vect):
        pass

    def rotate(self, angle, point):
        pass

    def scale(self, xfactor, yfactor=None, point=None):
        pass

    def skew(self, angle_x, angle_y, point):
        pass

    def buffer(self, distance, join, factor=None):
        pass

    def bounds(self, flatten=False):
        return None, None, None, None

    def to_dict(self):
        """
        Returns a representation of the object as a dictionary.
        Attributes to include are listed in ``self.ser_attrs``.

        :return: A dictionary-encoded copy of the object.
        :rtype: dict
        """
        d = {}
        for attr in self.ser_attrs:
            d[attr] = getattr(self, attr)
        return d

    def from_dict(self, d):
        """
        Sets object's attributes from a dictionary.
        Attributes to include are listed in ``self.ser_attrs``.
        This method will look only for only and all the
        attributes in ``self.ser_attrs``. They must all
        be present. Use only for deserializing saved
        objects.

        :param d: Dictionary of attributes to set in the object.
        :type d: dict
        :return: None
        """
        for attr in self.ser_attrs:
            # an older file may not have all the attributes
            if attr in d:
                setattr(self, attr, d[attr])
