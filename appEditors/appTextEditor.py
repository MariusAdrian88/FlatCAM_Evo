# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/10/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtPrintSupport, QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCFileSaveDialog, FCTextAreaExtended, FCButton

from appEditors.text_editor.code_edit import CodeEdit
from appEditors.text_editor.find_bar import FindBar
from appEditors.text_editor.lexers import get_lexer, detect_language
from appEditors.text_editor.minimap import Minimap
from appEditors.text_editor.status_bar import EditorStatusBar

from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch, mm
from xml.sax.saxutils import escape

# from io import StringIO

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

# factory values of the text_editor_* preferences (used when the app has no such option, e.g. a stub app)
EDITOR_DEFAULTS = {
    'text_editor_font_family': '',
    'text_editor_font_size': 10,
    'text_editor_wrap': False,
    'text_editor_show_whitespace': False,
    'text_editor_minimap': False,
    'text_editor_tab_width': 4,
}


class AppTextEditor(QtWidgets.QWidget):

    def __init__(self, app, text=None, plain_text=None, color_dict=None, parent=None, language=None):
        super().__init__(parent=parent)

        if color_dict is None:
            color_dict = {}
        self.app = app
        self.plain_text = plain_text
        self.callback = lambda x: None
        self.eol = 'LF'

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding
        )

        # UI Layout
        self.main_editor_layout = QtWidgets.QVBoxLayout(self)
        self.main_editor_layout.setContentsMargins(0, 0, 0, 0)

        self.t_frame = QtWidgets.QFrame()
        self.t_frame.setContentsMargins(0, 0, 0, 0)
        self.main_editor_layout.addWidget(self.t_frame)

        self.work_editor_layout = QtWidgets.QGridLayout(self.t_frame)
        self.work_editor_layout.setContentsMargins(2, 2, 2, 2)
        self.work_editor_layout.setSpacing(2)
        self.t_frame.setLayout(self.work_editor_layout)

        # CODE Editor
        if self.plain_text:
            # without a language, it is detected from the first text loaded
            self.language = language
            self._detect_pending = language is None
            self.lexer = get_lexer(language, self._tcl_keywords())
            self.code_editor = CodeEdit(
                theme=self.app.options.get(
                    'global_theme',
                    self.app.defaults.get(
                        'global_theme',
                        self.app.defaults.factory_defaults.get('global_theme')
                    )
                ),
                lexer=self.lexer,
                tab_width=self._option('text_editor_tab_width')
            )
            self.code_editor.set_editor_font(self._option('text_editor_font_family'),
                                             self._option('text_editor_font_size'))
        else:
            self.language = None
            self._detect_pending = False
            self.lexer = None
            self.code_editor = FCTextAreaExtended()
            self.code_editor.setStyleSheet("""
                            QTextEdit { selection-background-color:yellow;
                                        selection-color:black;
                            }
                         """)

        if text:
            if self.plain_text:
                self._detect_language(text)
                self.code_editor.load(text)
            else:
                self.code_editor.setPlainText(text)

        # #############################################################################################################
        # UI SETUP
        # #############################################################################################################
        # row 0: the toolbar
        toolbar = QtWidgets.QWidget()
        control_lay = QtWidgets.QHBoxLayout(toolbar)
        control_lay.setContentsMargins(0, 0, 0, 0)
        self.work_editor_layout.addWidget(toolbar, 0, 0)

        # OPEN
        self.buttonOpen = QtWidgets.QToolButton()
        self.buttonOpen.setIcon(QtGui.QIcon(self.app.resource_location + '/folder32_bis.png'))
        self.buttonOpen.setToolTip(_("Will open a text file in the editor."))
        control_lay.addWidget(self.buttonOpen)

        # SAVE
        self.buttonSave = QtWidgets.QToolButton()
        self.buttonSave.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.buttonSave.setToolTip(_("Will save the text in the editor into a file."))
        control_lay.addWidget(self.buttonSave)

        # Update
        self.button_update_code = QtWidgets.QToolButton()
        self.button_update_code.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.button_update_code.setToolTip(_("Save changes internally."))
        self.button_update_code.hide()
        control_lay.addWidget(self.button_update_code)

        # Print PREVIEW
        self.buttonPreview = QtWidgets.QToolButton()
        self.buttonPreview.setIcon(QtGui.QIcon(self.app.resource_location + '/preview32.png'))
        self.buttonPreview.setToolTip(_("Open a OS standard Preview Print window."))
        control_lay.addWidget(self.buttonPreview)

        # PRINT
        self.buttonPrint = QtWidgets.QToolButton()
        self.buttonPrint.setIcon(QtGui.QIcon(self.app.resource_location + '/printer32.png'))
        self.buttonPrint.setToolTip(_("Open a OS standard Print window."))
        control_lay.addWidget(self.buttonPrint)

        # RUN
        self.buttonRun = FCButton(_('Run'))
        self.buttonRun.setToolTip(_("Will run the TCL commands found in the text file, one by one."))
        self.buttonRun.hide()
        control_lay.addWidget(self.buttonRun)

        control_lay.addStretch(1)

        # FIND
        self.button_find_toggle = QtWidgets.QToolButton()
        self.button_find_toggle.setIcon(QtGui.QIcon(self.app.resource_location + '/find32.png'))
        self.button_find_toggle.setToolTip(_("Find and replace") + ' (Ctrl+F)')
        control_lay.addWidget(self.button_find_toggle)

        # WRAP
        self.button_wrap = QtWidgets.QToolButton()
        self.button_wrap.setText(_("Wrap"))
        self.button_wrap.setCheckable(True)
        self.button_wrap.setToolTip(_("Word wrap"))
        control_lay.addWidget(self.button_wrap)

        # WHITESPACE
        self.button_whitespace = QtWidgets.QToolButton()
        self.button_whitespace.setText('¶')
        self.button_whitespace.setCheckable(True)
        self.button_whitespace.setToolTip(_("Show spaces and tabs"))
        control_lay.addWidget(self.button_whitespace)
        if not self.plain_text:
            self.button_whitespace.hide()

        # MINIMAP
        self.button_minimap = QtWidgets.QToolButton()
        self.button_minimap.setText(_("Map"))
        self.button_minimap.setCheckable(True)
        self.button_minimap.setToolTip(_("Minimap"))
        control_lay.addWidget(self.button_minimap)
        if not self.plain_text:
            self.button_minimap.hide()

        # row 1: more toolbar rows, added with add_toolbar_row()
        self.toolbar_rows = QtWidgets.QWidget()
        self.toolbar_rows_layout = QtWidgets.QVBoxLayout(self.toolbar_rows)
        self.toolbar_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_rows_layout.setSpacing(0)
        self.work_editor_layout.addWidget(self.toolbar_rows, 1, 0)

        # row 2: find bar; row 3: the editor; row 4: status bar
        self.find_bar = FindBar(self.code_editor)
        self.work_editor_layout.addWidget(self.find_bar, 2, 0)
        self.work_editor_layout.addWidget(self.code_editor, 3, 0)
        self.work_editor_layout.setRowStretch(3, 1)
        self.status_bar = None
        if self.plain_text:
            self.status_bar = EditorStatusBar(self.code_editor)
            self.status_bar.set_language(self.lexer.name)
            self.work_editor_layout.addWidget(self.status_bar, 4, 0)

        # the find bar through its old names
        self.entryFind = self.find_bar.entryFind
        self.entryReplace = self.find_bar.entryReplace
        self.sel_all_cb = self.find_bar.sel_all_cb
        self.buttonFind = self.find_bar.buttonFind
        self.buttonReplace = self.find_bar.buttonReplace

        # view options
        # (the rich text editor of the Document mode wraps by default and does not use the preference)
        wrap = bool(self._option('text_editor_wrap')) if self.plain_text else True
        self.button_wrap.setChecked(wrap)
        if self.plain_text:
            self.on_wrap_toggled(wrap)
        if self.plain_text:
            show = bool(self._option('text_editor_show_whitespace'))
            self.button_whitespace.setChecked(show)
            self.code_editor.set_show_whitespace(show)
            # (shown only for a text of 300 lines or more)
            self.minimap = Minimap(self.code_editor)
            show_map = bool(self._option('text_editor_minimap'))
            self.button_minimap.setChecked(show_map)
            self.minimap.set_wanted(show_map)
        else:
            self.minimap = None

        # #############################################################################################################
        # ################### SIGNALS #################################################################################
        # #############################################################################################################
        self.code_editor.textChanged.connect(self.handleTextChanged)
        self.buttonOpen.clicked.connect(self.handleOpen)
        self.buttonSave.clicked.connect(self.handleSaveGCode)
        self.buttonPrint.clicked.connect(self.handlePrint)
        self.buttonPreview.clicked.connect(self.handlePreview)
        self.button_find_toggle.clicked.connect(self.toggle_find_bar)
        self.button_wrap.toggled.connect(self.on_wrap_toggled)
        self.button_whitespace.toggled.connect(self.on_whitespace_toggled)
        self.button_minimap.toggled.connect(self.on_minimap_toggled)

        for keys, callback in (('Ctrl+F', lambda: self.find_bar.open_bar()),
                               ('Ctrl+H', lambda: self.find_bar.open_bar(replace=True)),
                               ('F3', self.find_bar.find_next),
                               ('Shift+F3', self.find_bar.find_previous)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)

        self.code_editor.set_model_data(self._tcl_keywords() or [])

        self.code_edited = ''

    def _option(self, key):
        """A text_editor_* preference; the factory value when the app has no usable one."""
        default = EDITOR_DEFAULTS[key]
        try:
            value = self.app.options.get(key, None)
        except AttributeError:
            return default
        if isinstance(default, bool):
            return value if isinstance(value, bool) else default
        if isinstance(default, int):
            return value if isinstance(value, int) and not isinstance(value, bool) else default
        return value if isinstance(value, str) else default

    def _tcl_keywords(self):
        """The Tcl command names of the app (a stub app may have none)."""
        return getattr(getattr(self.app, 'regFK', None), 'myKeywords', None)

    def set_language(self, language):
        """Sets the language of the code ('gcode', 'gerber', 'excellon', 'tcl', 'plain'); the shown lines are
        highlighted again, the others when they are shown."""
        if not self.plain_text:
            return
        self._detect_pending = False
        self.language = language
        self.lexer = get_lexer(language, self._tcl_keywords())
        self.code_editor.set_lexer(self.lexer)
        if self.status_bar is not None:
            self.status_bar.set_language(self.lexer.name)

    def _detect_language(self, text):
        """Sets the language from the first text loaded, when the caller gave none."""
        if not self._detect_pending or not text:
            return
        self._detect_pending = False
        self.language = detect_language(text)
        self.lexer = get_lexer(self.language, self._tcl_keywords())
        # (the text is loaded right after: the lexer is set without highlighting the old text again)
        self.code_editor.lexer = self.lexer
        self.code_editor.highlighter.set_lexer(self.lexer)
        if getattr(self, 'status_bar', None) is not None:
            self.status_bar.set_language(self.lexer.name)

    def _set_option(self, key, value):
        try:
            self.app.options[key] = value
        except (TypeError, AttributeError):
            pass

    def add_toolbar_row(self, widget):
        """Adds a widget as a row under the top toolbar."""
        self.toolbar_rows_layout.addWidget(widget)

    def toggle_find_bar(self):
        if self.find_bar.isVisible():
            self.find_bar.close_bar()
        else:
            self.find_bar.open_bar()

    def on_wrap_toggled(self, checked):
        if self.plain_text:
            self.code_editor.set_word_wrap(checked)
            self._set_option('text_editor_wrap', bool(checked))
        else:
            mode = QtWidgets.QTextEdit.LineWrapMode.WidgetWidth if checked else QtWidgets.QTextEdit.LineWrapMode.NoWrap
            self.code_editor.setLineWrapMode(mode)

    def on_whitespace_toggled(self, checked):
        if self.plain_text:
            self.code_editor.set_show_whitespace(checked)
        self._set_option('text_editor_show_whitespace', bool(checked))

    def on_minimap_toggled(self, checked):
        if self.minimap is not None:
            self.minimap.set_wanted(checked)
        self._set_option('text_editor_minimap', bool(checked))

    def set_callback(self, callback):
        self.callback = callback

    def handlePrint(self):
        dialog = QtPrintSupport.QPrintDialog()
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            # (the print() of the code editor highlights the lines never shown before it prints)
            self.code_editor.print(dialog.printer())

    def handlePreview(self):
        dialog = QtPrintSupport.QPrintPreviewDialog()
        dialog.paintRequested.connect(self.code_editor.print)
        dialog.exec()

    def handleTextChanged(self):
        # enable = not self.ui.code_editor.document().isEmpty()
        # self.ui.buttonPrint.setEnabled(enable)
        # self.ui.buttonPreview.setEnabled(enable)

        self.buttonSave.setStyleSheet("QToolButton {color: red;}")
        self.buttonSave.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as_red.png'))

    def _set_plain_text(self, text):
        """Replaces the text with a text read from a file; the editor then counts as edited (not saved)."""
        if self.plain_text:
            self._detect_language(text)
            self.code_editor.load(text)
            self.set_eol_from(text)
            self.handleTextChanged()
        else:
            self.code_editor.setPlainText(text)

    def set_eol_from(self, text):
        self.eol = 'CRLF' if '\r\n' in text[:200000] else 'LF'
        if self.status_bar is not None:
            self.status_bar.set_eol(self.eol)

    def load_text(self, text, move_to_start=False, move_to_end=False, clear_text=True, as_html=False):
        try:
            # setPlainText/setHtml replace the text; clearing first notifies source observers twice.
            self.code_editor.setReadOnly(False)
            if self.plain_text:
                # (the load() sends no textChanged: the editor shows a text that is saved)
                self._detect_language(text)
                self.code_editor.load(text)
                self.set_eol_from(text)
            else:
                try:
                    self.code_editor.textChanged.disconnect(self.handleTextChanged)
                except (AttributeError, TypeError):
                    pass
                try:
                    if as_html is False:
                        self.code_editor.setPlainText(text)
                    else:
                        self.code_editor.setHtml(text)
                finally:
                    self.code_editor.textChanged.connect(self.handleTextChanged)
        except Exception as err:
            self.app.log.error("AppTextEditor.load_text() --> %s." % str(err))
            return
        if move_to_start:
            self.code_editor.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
        elif move_to_end:
            self.code_editor.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def handleOpen(self, filt=None):
        self.app.defaults.report_usage("handleOpen()")

        if filt:
            _filter_ = filt
        else:
            _filter_ = "G-Code Files (*.nc);; G-Code Files (*.txt);; G-Code Files (*.tap);; G-Code Files (*.cnc);; " \
                       "All Files (*.*)"

        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            caption=_('Open file'), directory=self.app.get_last_folder(), filter=_filter_)

        if path:
            owner = getattr(self, 'script_owner', None)
            if owner is not None:
                try:
                    with open(path, 'r', encoding='utf-8-sig') as file:
                        text = file.read()
                except (OSError, UnicodeError) as error:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % error)
                    return 'fail'
                self._set_plain_text(text)
                owner.script_filename = path
                owner.mark_project_dirty()
                self.app.file_opened.emit('script', path)
                return
            file = QtCore.QFile(path)
            if file.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
                stream = QtCore.QTextStream(file)
                self.code_edited = stream.readAll()
                self._set_plain_text(self.code_edited)
                file.close()

    def handleSaveGCode(self, name=None, filt=None, callback=None):
        self.app.defaults.report_usage("handleSaveGCode()")

        if filt:
            _filter_ = filt
        else:
            _filter_ = "G-Code Files (*.nc);; G-Code Files (*.txt);; G-Code Files (*.tap);; G-Code Files (*.cnc);; " \
                       "PDF Files (*.pdf);;All Files (*.*)"

        owner = getattr(self, 'script_owner', None)
        if owner is not None:
            obj_name = owner.obj_options['name']
        elif name:
            obj_name = name
        else:
            try:
                obj_name = self.app.collection.get_active().obj_options['name']
            except AttributeError:
                obj_name = 'file'
                if filt is None:
                    _filter_ = "FlatConfig Files (*.FlatConfig);;PDF Files (*.pdf);;All Files (*.*)"

        try:
            filename = str(FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                directory=self.app.options.get(
                    "global_last_folder",
                    self.app.defaults.get(
                        "global_last_folder",
                        self.app.defaults.factory_defaults.get("global_last_folder")
                    )
                ) + '/' + str(obj_name),
                ext_filter=_filter_
            )[0])
        except TypeError:
            filename = str(FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                ext_filter=_filter_)[0])

        if filename == "":
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            try:
                my_gcode = self.code_editor.toPlainText()
                if owner is not None:
                    owner.on_source_changed()
                    if owner.save_file(filename) == 'fail':
                        return 'fail'
                elif filename.rpartition('.')[2].lower() == 'pdf':
                    page_size = (
                        self.app.plotcanvas.pagesize_dict[self.app.options.get(
                            'global_workspaceT',
                            self.app.defaults.get(
                                'global_workspaceT',
                                self.app.defaults.factory_defaults.get('global_workspaceT')
                            )
                        )][0] * mm,
                        self.app.plotcanvas.pagesize_dict[self.app.options.get(
                            'global_workspaceT',
                            self.app.defaults.get(
                                'global_workspaceT',
                                self.app.defaults.factory_defaults.get('global_workspaceT')
                            )
                        )][1] * mm
                    )

                    # add new line after each line
                    # Paragraph reads its text as markup: the '<', '>' and '&' of the text ('a<b' in a Tcl script,
                    # a '<T1>' comment) are escaped first, or the PDF fails or loses text; the '<br />' come after
                    lined_gcode = escape(my_gcode).replace("\n", "<br />")

                    styles = getSampleStyleSheet()
                    styleN = styles['Normal']
                    # styleH = styles['Heading1']
                    story = []

                    if self.app.app_units.lower() == 'mm':
                        bmargin = self.app.options.get(
                            'global_tpdf_bmargin',
                            self.app.defaults.get(
                                'global_tpdf_bmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_bmargin')
                            )
                        ) * mm
                        tmargin = self.app.options.get(
                            'global_tpdf_tmargin',
                            self.app.defaults.get(
                                'global_tpdf_tmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_tmargin')
                            )
                        ) * mm
                        rmargin = self.app.options.get(
                            'global_tpdf_rmargin',
                            self.app.defaults.get(
                                'global_tpdf_rmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_rmargin')
                            )
                        ) * mm
                        lmargin = self.app.options.get(
                            'global_tpdf_lmargin',
                            self.app.defaults.get(
                                'global_tpdf_lmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_lmargin')
                            )
                        ) * mm
                    else:
                        bmargin = self.app.options.get(
                            'global_tpdf_bmargin',
                            self.app.defaults.get(
                                'global_tpdf_bmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_bmargin')
                            )
                        ) * inch
                        tmargin = self.app.options.get(
                            'global_tpdf_tmargin',
                            self.app.defaults.get(
                                'global_tpdf_tmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_tmargin')
                            )
                        ) * inch
                        rmargin = self.app.options.get(
                            'global_tpdf_rmargin',
                            self.app.defaults.get(
                                'global_tpdf_rmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_rmargin')
                            )
                        ) * inch
                        lmargin = self.app.options.get(
                            'global_tpdf_lmargin',
                            self.app.defaults.get(
                                'global_tpdf_lmargin',
                                self.app.defaults.factory_defaults.get('global_tpdf_lmargin')
                            )
                        ) * inch

                    doc = SimpleDocTemplate(
                        filename,
                        pagesize=page_size,
                        bottomMargin=bmargin,
                        topMargin=tmargin,
                        rightMargin=rmargin,
                        leftMargin=lmargin)

                    P = Paragraph(lined_gcode, styleN)
                    story.append(P)

                    doc.build(
                        story,
                    )
                else:
                    with open(filename, 'w') as f:
                        f.write(my_gcode)
                self.buttonSave.setStyleSheet("")
                self.buttonSave.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
            except FileNotFoundError:
                self.app.inform.emit('[WARNING] %s' % _("No such file or directory"))
                return
            except PermissionError:
                self.app.inform.emit('[WARNING] %s' %
                                     _("Permission denied, saving not possible.\n"
                                       "Most likely another app is holding the file open and not accessible."))
                return
            except Exception as err:
                # e.g. an error of reportlab while the PDF is made: a message, not the Critical Error dialog
                self.app.log.error("AppTextEditor.handleSaveGCode() --> %s" % str(err))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not save the file"), str(err)))
                return

        # Just for adding it to the recent files list.
        if self.app.options.get(
                "global_open_style",
                self.app.defaults.get(
                    "global_open_style",
                    self.app.defaults.factory_defaults.get("global_open_style")
                )) is False:
            self.app.file_opened.emit('script' if owner is not None else 'cncjob', filename)
        self.app.file_saved.emit('script' if owner is not None else 'cncjob', filename)
        self.app.inform.emit('%s: %s' % (_("Saved to"), str(filename)))

        if callback is not None:
            callback()

    def handleFindGCode(self):
        """Selects the next match of the Find box (wraps around at the end of the text, no dialog)."""
        self.find_bar.find_next()

    def handleReplaceGCode(self):
        """Replace All when the 'All' box is checked, else replaces the selected match and goes to the next one."""
        if self.sel_all_cb.isChecked():
            self.find_bar.replace_all()
        else:
            self.find_bar.replace_current()

    def highlight_find_text(self):
        self.find_bar.update_results()
