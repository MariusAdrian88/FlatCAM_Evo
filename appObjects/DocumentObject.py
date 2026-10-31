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

from PyQt6.QtCore import Qt

from appEditors.appTextEditor import AppTextEditor
from appGUI.GUIElements import FCFileSaveDialog
from appEditors.text_editor.rich_toolbar import RichTextToolbar
from appObjects.AppObjectTemplate import *
from appGUI.ObjectUI import DocumentObjectUI

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class DocumentObject(FlatCAMObj):
    """
    Represents a Document object.
    """
    # Dispatch metadata, read by AppObject, ObjectCollection and appPlotManager instead of kind chains.
    # status_color: color of the object name in the status bar message (None: no message).
    status_color = 'darkCyan'
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
    ui_type = DocumentObjectUI

    def __init__(self, name, app):
        self.app = app
        self.decimals = self.app.decimals

        self.app.log.debug("Creating a Document object...")
        FlatCAMObj.__init__(self, name, app)

        self.kind = "document"
        self.units = ''

        self.ser_attrs = ['obj_options', 'kind', 'source_file']
        self.source_file = ''
        self.doc_code = ''

        self.font_name = None
        self.font_italic = None
        self.font_bold = None
        self.font_underline = None

        self.document_editor_tab = None

        self._read_only = False
        self.units_found = self.app.app_units

    def copy_from(self, src):
        """
        Make this (new, empty) DocumentObject a copy of ``src``: the text (source_file). Used by ``on_copy_command``.
        Not copied, on purpose: obj_options (the new object takes the current app defaults) and the editor state.

        :param src: the DocumentObject to copy
        :return:    None
        """
        self.source_file = deepcopy(getattr(src, 'source_file', self.source_file))

    def set_ui(self, ui):
        FlatCAMObj.set_ui(self, ui)
        self.app.log.debug("DocumentObject.set_ui()")

        assert isinstance(self.ui, DocumentObjectUI), \
            "Expected a DocumentObjectUI, got %s" % type(self.ui)

        self.units = self.app.app_units.upper()
        self.units_found = self.app.app_units

        # Fill form fields only on object create
        self.to_form()

        # Show/Hide Advanced Options
        app_mode = self._app_option("global_app_level")
        self.change_level(app_mode)

        self.document_editor_tab = AppTextEditor(app=self.app)

        self.document_editor_tab.buttonRun.hide()

        self.ui.autocomplete_cb.set_value(self._app_option('document_autocompleter'))
        self.on_autocomplete_changed(state=self._app_option('document_autocompleter'))
        self.on_tab_size_change(val=self._app_option('document_tab_size'))

        self.ui.sel_color_entry.set_value(self._app_option('document_sel_color'))

        # the sizes first, then the saved size: an editable combo shows the size even if it is not in the list
        font_sizes = self._app_option('document_font_sizes')
        self.ui.font_size_cb.addItems(font_sizes)
        self.ui.font_size_cb.setCurrentText(str(self._app_option('document_font_size')))

        self.rich_toolbar = RichTextToolbar(
            self.document_editor_tab.code_editor, font_sizes=font_sizes, resource_location=self.ui.resource_loc)
        self.document_editor_tab.add_toolbar_row(self.rich_toolbar)

        flt = "FlatCAM Docs (*.FlatDoc);;HTML Files (*.html *.htm);;Text Files (*.txt);;PDF Files (*.pdf);;" \
              "All Files (*.*)"
        # ######################################################################
        # ######################## SIGNALS #####################################
        # ######################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.document_editor_tab.buttonOpen.clicked.disconnect()
        self.document_editor_tab.buttonOpen.clicked.connect(lambda: self.document_editor_tab.handleOpen(filt=flt))
        self.document_editor_tab.buttonSave.clicked.disconnect()
        self.document_editor_tab.buttonSave.clicked.connect(lambda: self.save_document(filt=flt))

        self.document_editor_tab.code_editor.textChanged.connect(self.on_text_changed)

        self.ui.font_type_cb.currentFontChanged.connect(self.apply_default_font)
        self.ui.font_size_cb.activated.connect(self.apply_default_font)

        self.ui.sel_color_entry.value_changed.connect(self.on_selection_color_entry)

        self.ui.autocomplete_cb.stateChanged.connect(self.on_autocomplete_changed)
        self.ui.tab_size_spinner.returnPressed.connect(self.on_tab_size_change)
        # #######################################################################

        # self.document_editor_tab.handleTextChanged()

        try:
            if QtGui.Qt.mightBeRichText(self.source_file):
                # self.document_editor_tab.code_editor.setHtml(self.source_file)
                self.document_editor_tab.load_text(self.source_file, move_to_start=True, clear_text=True, as_html=True)
            else:
                # for line in self.source_file.splitlines():
                #     self.document_editor_tab.code_editor.append(line)
                self.document_editor_tab.load_text(self.source_file, move_to_start=True, clear_text=True, as_html=False)
        except AttributeError:
            self.document_editor_tab.load_text(self.source_file, move_to_start=True, clear_text=True, as_html=True)

        self.on_selection_color_entry(self._app_option("document_sel_color"))
        # self.on_font_color_entry(self.app.options["document_font_color"])

        self.build_ui()

        # TODO does not work, why?
        # switch the notebook area to Properties Tab
        # self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

    @property
    def read_only(self):
        return self._read_only

    @read_only.setter
    def read_only(self, val):
        if val:
            self._read_only = True
        else:
            self._read_only = False

    def build_ui(self):
        FlatCAMObj.build_ui(self)
        tab_here = False

        # try to not add too many times a tab that it is already installed
        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.widget(idx).objectName() == self.obj_options['name'] + "_editor_tab":
                tab_here = True
                break

        # add the tab if it is not already added
        if tab_here is False:
            self.app.ui.plot_tab_area.addTab(self.document_editor_tab, '%s' % _("Document Editor"))
            self.document_editor_tab.setObjectName(self.obj_options['name'] + "_editor_tab")

        # Switch plot_area to CNCJob tab
        self.app.ui.plot_tab_area.setCurrentWidget(self.document_editor_tab)

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

    def on_autocomplete_changed(self, state):
        if state:
            self.document_editor_tab.code_editor.completer_enable = True
        else:
            self.document_editor_tab.code_editor.completer_enable = False

    def on_tab_size_change(self, val=None):
        try:
            self.ui.tab_size_spinner.returnPressed.disconnect(self.on_tab_size_change)
        except TypeError:
            pass

        if val:
            self.ui.tab_size_spinner.set_value(val)

        tab_balue = int(self.ui.tab_size_spinner.get_value())
        self.document_editor_tab.code_editor.setTabStopDistance(tab_balue)
        self.app.options['document_tab_size'] = tab_balue

        self.ui.tab_size_spinner.returnPressed.connect(self.on_tab_size_change)

    def on_text_changed(self):
        self.source_file = self.document_editor_tab.code_editor.toHtml()
        # print(self.source_file)

    def apply_default_font(self, *_args):
        """Apply the font and size of the left panel to the whole document, in one Undo step."""
        try:
            size = float(str(self.ui.font_size_cb.get_value()).replace(',', '.'))
        except ValueError:
            return
        if size <= 0:
            return
        family = self.ui.font_type_cb.currentFont().family()
        self.font_name = family
        self.app.options['document_font_size'] = int(size) if size == int(size) else size

        editor = self.document_editor_tab.code_editor
        font = QtGui.QFont(family)
        font.setPointSizeF(size)
        editor.document().setDefaultFont(font)
        fmt = QtGui.QTextCharFormat()
        fmt.setFontFamilies([family])
        fmt.setFontPointSize(size)
        cursor = QtGui.QTextCursor(editor.document())
        cursor.select(QtGui.QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(fmt)
        self.rich_toolbar.sync()

    def save_document(self, filt=None):
        """Save by the extension: .FlatDoc / .html keep the formatting, .txt is plain text, .pdf is printed."""
        editor = self.document_editor_tab.code_editor
        try:
            filename = str(FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                directory=str(self.app.options.get('global_last_folder', '')) + '/' + str(self.obj_options['name']),
                ext_filter=filt or "FlatCAM Docs (*.FlatDoc);;All Files (*.*)")[0])
        except TypeError:
            filename = str(FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."), ext_filter=filt or "FlatCAM Docs (*.FlatDoc);;All Files (*.*)")[0])
        if not filename:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return

        ext = filename.rpartition('.')[2].lower()
        try:
            if ext == 'pdf':
                writer = QtGui.QPdfWriter(filename)
                writer.setPageLayout(self._pdf_page_layout())
                editor.document().print(writer)
            elif ext == 'txt':
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(editor.toPlainText())
            else:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(editor.toHtml())
        except OSError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not save the file"), str(err)))
            return

        self.document_editor_tab.buttonSave.setStyleSheet("")
        self.document_editor_tab.buttonSave.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.app.file_saved.emit('document', filename)
        self.app.inform.emit('%s: %s' % (_("Saved to"), filename))

    def _pdf_page_layout(self):
        """The page of the workspace (mm) and the PDF margins of the Preferences (in the app units)."""
        page = self.app.plotcanvas.pagesize_dict.get(self._app_option('global_workspaceT'))
        if page:
            page_size = QtGui.QPageSize(QtCore.QSizeF(page[0], page[1]), QtGui.QPageSize.Unit.Millimeter)
        else:
            page_size = QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4)
        to_mm = 1.0 if str(self.app.app_units).lower() == 'mm' else 25.4
        margins = QtCore.QMarginsF(*(float(self._app_option('global_tpdf_%smargin' % side)) * to_mm
                                     for side in ('l', 't', 'r', 'b')))
        return QtGui.QPageLayout(page_size, QtGui.QPageLayout.Orientation.Portrait, margins,
                                 QtGui.QPageLayout.Unit.Millimeter)

    # Setting selection colors handlers
    def on_selection_color_entry(self, val):
        self.app.options['document_sel_color'] = val
        # p = QtGui.QPalette()
        # p.setColor(QtGui.QPalette.ColorRole.Highlight, sel_color)
        # p.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor('white'))
        # self.document_editor_tab.code_editor.setPalette(p)

        self.document_editor_tab.code_editor.setStyleSheet(
            """
                QTextEdit {selection-background-color:%s;
                           selection-color:white;
                }
            """ % QtGui.QColor(val[:-2]).name()
        )

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
