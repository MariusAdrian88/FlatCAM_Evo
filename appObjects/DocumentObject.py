# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# https://github.com/MariusAdrian88/FlatCAM_Evo            #
# Author: Marius Stanciu (c)                               #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File modified by: Marius Stanciu                         #
# ##########################################################

from copy import deepcopy
import base64
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QBuffer
from PyQt6 import QtPdf, QtGui, QtCore, QtWidgets

from appEditors.appTextEditor import AppTextEditor
from appGUI.GUIElements import FCFileSaveDialog
from appEditors.text_editor.rich_toolbar import RichTextToolbar
# from appObjects.AppObjectTemplate import *
from appGUI.ObjectUI import DocumentObjectUI

from appObjects.AppObjectTemplate import FlatCAMObj

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

    # the editor text is classified once, a moment after the typing stops; reading source_file flushes it
    _source_file = ''
    _text_dirty = False
    _classify_timer = None

    @property
    def source_file(self):
        self._flush_text()
        return self._source_file

    @source_file.setter
    def source_file(self, value):
        self._source_file = value
        self._text_dirty = False

    def __init__(self, name, app):
        self.app = app
        self.decimals = self.app.decimals

        self.app.log.debug("Creating a Document object...")
        FlatCAMObj.__init__(self, name, app)

        self.kind = "document"
        self.units = ''

        self.ser_attrs = ['obj_options', 'kind', 'source_file', 'document_type']
        self.document_type = 'text'
        self.source_file = ''
        self.doc_code = ''

        self.font_name = None
        self.font_italic = None
        self.font_bold = None
        self.font_underline = None

        self.document_editor_tab = None
        self.rich_toolbar = None

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
        # noinspection PyUnresolvedReferences
        self.document_type = getattr(src, 'document_type', 'html' if Qt.mightBeRichText(self.source_file) else 'text')

    @property
    def pdf_data(self):
        return base64.b64decode(self.source_file, validate=True)

    def set_pdf(self, data):
        """Validate before replacing the retained PDF; base64 keeps project JSON lossless."""
        document = QtPdf.QPdfDocument(None)
        buffer = QBuffer(document)
        buffer.setData(data)
        buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
        try:
            document.load(buffer)
            if document.status() != QtPdf.QPdfDocument.Status.Ready or not document.pageCount():
                raise ValueError(_('Invalid or unsupported PDF document.'))
        finally:
            document.close()
            buffer.close()
        self.source_file = base64.b64encode(data).decode('ascii')
        self.document_type = 'pdf'

    def parse_file(self, filename):
        try:
            path = Path(filename)
            if path.suffix.lower() == '.pdf':
                self.set_pdf(path.read_bytes())
            else:
                content = path.read_text(encoding='utf-8-sig')
                if '\x00' in content:
                    raise ValueError(_('The file is not a text document.'))
                self.source_file = content
                self.document_type = 'html' if path.suffix.lower() in ('.html', '.htm', '.flatdoc') else 'text'
        except (OSError, ValueError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Failed to open Document.'), error))
            return 'fail'

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

        self.ui.type_label.setText('%s: %s' % (_('Type'), self.document_type.upper()))
        if self.document_type == 'pdf':
            for index in range(self.ui.custom_box.count()):
                widget = self.ui.custom_box.itemAt(index).widget()
                if widget is not None and widget is not self.ui.type_label:
                    widget.hide()
            self.build_ui()
            return

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

        # both cases: the file dialogs of Linux match the patterns case-sensitively
        flt = _("Documents (*.txt *.TXT *.html *.HTML *.htm *.HTM *.FlatDoc *.flatdoc *.FLATDOC);;"
                "Text Files (*.txt *.TXT);;HTML Files (*.html *.HTML *.htm *.HTM);;"
                "FlatCAM Docs (*.FlatDoc *.flatdoc *.FLATDOC);;All Files (*.*)")
        # ######################################################################
        # ######################## SIGNALS #####################################
        # ######################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.document_editor_tab.set_file_filter(flt)
        self.document_editor_tab.set_save_handler(self.save_document)
        self.document_editor_tab.set_open_handler(self.open_file_in_editor)

        self.ui.font_type_cb.currentFontChanged.connect(self.apply_default_font)
        self.ui.font_size_cb.activated.connect(self.apply_default_font)

        self.ui.sel_color_entry.value_changed.connect(self.on_selection_color_entry)

        self.ui.autocomplete_cb.stateChanged.connect(self.on_autocomplete_changed)
        self.ui.tab_size_spinner.returnPressed.connect(self.on_tab_size_change)
        # #######################################################################

        self.document_editor_tab.load_text(self.source_file, move_to_start=True, clear_text=True,
                                           as_html=self.document_type == 'html')
        self.document_editor_tab.code_editor.textChanged.connect(self.on_text_changed)

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
        if getattr(self, '_load_batch', None) is not None:
            return
        FlatCAMObj.build_ui(self)
        if self.document_type == 'pdf':
            return self.app.pdf_viewer.open_document(self)
        editor = self.document_editor_tab
        if editor is None:
            return
        area = self.app.ui.plot_tab_area
        self.app.pdf_viewer.reattach(editor)
        if area.indexOf(editor) < 0:
            area.addTab(editor, self.obj_options['name'])
        area.setTabText(area.indexOf(editor), self.obj_options['name'])
        editor.setObjectName(self.obj_options['name'] + '_editor_tab')
        area.setCurrentWidget(editor)

    def close_editor(self):
        self._flush_text()
        if self._classify_timer is not None:
            self._classify_timer.stop()
        self.app.pdf_viewer.close_owned_tabs(self)
        editor = self.document_editor_tab
        if editor is not None:
            self.app.pdf_viewer.reattach(editor)
            area = self.app.ui.plot_tab_area
            index = area.indexOf(editor)
            if index >= 0:
                area.removeTab(index)
            editor.deleteLater()
            self.document_editor_tab = None
            self.rich_toolbar = None

    def delete(self):
        self.close_editor()
        FlatCAMObj.delete(self)

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
        self._text_dirty = True
        if self._classify_timer is None:
            self._classify_timer = QTimer()
            self._classify_timer.setSingleShot(True)
            self._classify_timer.setInterval(300)
            self._classify_timer.timeout.connect(self._flush_text)
        self._classify_timer.start()
        self.mark_project_dirty()

    def mark_project_dirty(self):
        collection =getattr(self, '_document_collection', None)
        if collection is not None and collection is not self.app.collection:
            for context in getattr(getattr(self.app, 'projects', None), 'contexts', []):
                if context.collection is collection:
                    context.should_we_save = True
                    return
        else:
            self.app.should_we_save = True

    def open_file_in_editor(self, path):
        """The Open button of the text editor: .html/.htm/.FlatDoc load as HTML, other files as plain text."""
        suffix = Path(path).suffix.lower()
        if suffix == '.pdf':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _('A PDF opens with File > Open > Open Document.'))
            return 'fail'
        try:
            text = Path(path).read_text(encoding='utf-8-sig')
            if '\x00' in text:
                raise ValueError(_('The file is not a text document.'))
        except (OSError, ValueError) as error:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Failed to open Document.'), error))
            return 'fail'
        as_html = suffix in ('.html', '.htm', '.flatdoc')
        self.document_editor_tab.load_text(text, move_to_start=True, clear_text=True, as_html=as_html)
        self._text_dirty = False
        self._source_file = text
        self.document_type = 'html' if as_html else 'text'
        self.ui.type_label.setText('%s: %s' % (_('Type'), self.document_type.upper()))
        self.mark_project_dirty()

    def _flush_text(self):
        """Take the text of the editor into source_file and document_type (html when it has formatting)."""
        if not self._text_dirty:
            return
        self._text_dirty = False
        if self._classify_timer is not None:
            self._classify_timer.stop()
        if self.document_editor_tab is None:
            return
        editor = self.document_editor_tab.code_editor
        plain = QtGui.QTextDocument()
        plain.setDefaultFont(editor.document().defaultFont())
        plain.setPlainText(editor.toPlainText())
        html_text = editor.toHtml()
        formatted = html_text != plain.toHtml()
        self.document_type = 'html' if formatted else 'text'
        self._source_file = html_text if formatted else editor.toPlainText()
        self.ui.type_label.setText('%s: %s' % (_('Type'), self.document_type.upper()))

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
        """Export retained PDF bytes, plain text, or formatted HTML without changing the project object."""
        self._flush_text()
        own = {'pdf': 'pdf', 'html': 'html', 'text': 'txt'}[self.document_type]
        # (extension to append, filter text) per format; the own type is offered first
        formats = {'txt': ('.txt', _('Text Files (*.txt)')), 'html': ('.html', _('HTML Files (*.html *.htm)')),
                   'flatdoc': ('.FlatDoc', _('FlatCAM Docs (*.FlatDoc)')), 'pdf': ('.pdf', _('PDF Files (*.pdf)'))}
        offered = ['pdf'] if own == 'pdf' else [own] + [key for key in formats if key != own]
        by_extension = {'.txt': 'txt', '.html': 'html', '.htm': 'html', '.flatdoc': 'flatdoc', '.pdf': 'pdf'}
        filename, selected = FCFileSaveDialog.get_saved_filename(
            caption=_('Save Document'),
            directory=str(Path(self.app.get_last_save_folder()) / self.obj_options['name']),
            ext_filter=filt or ';;'.join(formats[key][1] for key in offered), append_extension=False)
        if not filename:
            return False
        chosen = filename
        fmt = by_extension.get(Path(filename).suffix.lower())
        if fmt not in offered:
            fmt = next((key for key in offered if formats[key][1] == selected), own)
            filename += formats[fmt][0]
        if filename != chosen and Path(filename).exists() and QtWidgets.QMessageBox.question(
                self.app.ui, _('Overwrite file?'), _('The file already exists. Replace it?')) != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return False
        try:
            output = QtCore.QSaveFile(filename)
            if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if fmt == 'pdf' and own != 'pdf':
                writer = QtGui.QPdfWriter(output)
                writer.setPageLayout(self._pdf_page_layout())
                self._export_qdocument().print(writer)
                del writer
                if output.error() != QtCore.QFileDevice.FileError.NoError:
                    output.cancelWriting()
                    raise OSError(output.errorString())
            else:
                data = self._export_bytes(fmt)
                if output.write(data) != len(data):
                    output.cancelWriting()
                    raise OSError(output.errorString())
            if not output.commit():
                raise OSError(output.errorString())
        except (OSError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not save the file"), str(err)))
            return False

        if self.document_editor_tab is not None:
            self.document_editor_tab.buttonSave.setStyleSheet("")
            self.document_editor_tab.buttonSave.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.app.file_saved.emit('document', filename)
        self.app.inform.emit('%s: %s' % (_("Saved to"), filename))
        return True

    def _export_qdocument(self):
        """The formatted document: the one of the editor (keeps the fonts), else built from source_file."""
        if self.document_editor_tab is not None:
            return self.document_editor_tab.code_editor.document()
        doc = QtGui.QTextDocument()
        if self.document_type == 'html':
            doc.setHtml(self.source_file)
        else:
            doc.setPlainText(self.source_file)
        return doc

    def _export_bytes(self, fmt):
        """The bytes to write for fmt 'txt' or 'html'/'flatdoc' (the retained bytes for a PDF Document)."""
        if self.document_type == 'pdf':
            return self.pdf_data
        if fmt == 'txt':
            text = self.source_file if self.document_type == 'text' else self._export_qdocument().toPlainText()
        elif self.document_type == 'html':
            text = self.source_file
        else:
            doc = QtGui.QTextDocument()
            doc.setPlainText(self.source_file)
            text = doc.toHtml()
        return text.encode('utf-8')

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

    def bounds(self, flatten=False):    # noqa
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
        if 'document_type' not in d:
            # noinspection PyUnresolvedReferences
            self.document_type = 'html' if Qt.mightBeRichText(self.source_file) else 'text'
