# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/10/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtPrintSupport, QtWidgets, QtCore, QtGui
from appGUI.GUIElements import FCFileSaveDialog, FCEntry, FCTextAreaExtended, FCTextAreaLineNumber, FCButton, \
    FCCheckBox, FCMessageBox

from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch, mm

# from io import StringIO

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AppTextEditor(QtWidgets.QWidget):

    def __init__(self, app, text=None, plain_text=None, color_dict=None, parent=None):
        super().__init__(parent=parent)

        if color_dict is None:
            color_dict = {}
        self.app = app
        self.plain_text = plain_text
        self.callback = lambda x: None

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
        self.t_frame.setLayout(self.work_editor_layout)

        # CODE Editor
        if self.plain_text:
            self.editor_class = FCTextAreaLineNumber(
                color_dict=color_dict,
                theme=self.app.options.get(
                    'global_theme',
                    self.app.defaults.get(
                        'global_theme',
                        self.app.defaults.factory_defaults.get('global_theme')
                    )
                )
            )
            self.code_editor = self.editor_class.edit

            sel_color = 'black'
            stylesheet = """
                            QPlainTextEdit { selection-background-color:yellow;
                                             selection-color:%s;
                            }
                         """ % sel_color
            self.work_editor_layout.addWidget(self.editor_class, 0, 0, 1, 5)
        else:
            self.code_editor = FCTextAreaExtended()

            sel_color = 'black'
            stylesheet = """
                            QTextEdit { selection-background-color:yellow;
                                        selection-color:%s;
                            }
                         """ % sel_color
            self.work_editor_layout.addWidget(self.code_editor, 0, 0, 1, 5)

        self.code_editor.setStyleSheet(stylesheet)

        if text:
            self.code_editor.setPlainText(text)

        # #############################################################################################################
        # UI SETUP
        # #############################################################################################################
        control_lay = QtWidgets.QHBoxLayout()
        self.work_editor_layout.addLayout(control_lay, 1, 0, 1, 5)

        # FIND
        self.buttonFind = FCButton(_('Find'))
        self.buttonFind.setIcon(QtGui.QIcon(self.app.resource_location + '/find32.png'))
        self.buttonFind.setToolTip(_("Will search and highlight in yellow the string in the Find box."))
        control_lay.addWidget(self.buttonFind)

        # Entry FIND
        self.entryFind = FCEntry()
        self.entryFind.setPlaceholderText(_("Find box. Enter here the strings to be searched in the text."))
        self.entryFind.setToolTip(_("Find box. Enter here the strings to be searched in the text."))
        control_lay.addWidget(self.entryFind)

        # REPLACE
        self.buttonReplace = FCButton(_('Replace With'))
        self.buttonReplace.setIcon(QtGui.QIcon(self.app.resource_location + '/replace32.png'))
        self.buttonReplace.setToolTip(_("Will replace the string from the Find box with the one in the Replace box."))
        control_lay.addWidget(self.buttonReplace)

        # Entry REPLACE
        self.entryReplace = FCEntry()
        self.entryReplace.setPlaceholderText(_("String to replace the one in the Find box throughout the text."))
        self.entryReplace.setToolTip(_("String to replace the one in the Find box throughout the text."))
        control_lay.addWidget(self.entryReplace)

        # Select All
        self.sel_all_cb = FCCheckBox(_('All'))
        self.sel_all_cb.setToolTip(_("When checked it will replace all instances in the 'Find' box\n"
                                     "with the text in the 'Replace' box.."))
        control_lay.addWidget(self.sel_all_cb)

        # COPY All
        # self.button_copy_all = FCButton(_('Copy All'))
        # self.button_copy_all.setIcon(QtGui.QIcon(self.app.resource_location + '/copy_file32.png'))
        # self.button_copy_all.setToolTip(_("Will copy all the text in the Code Editor to the clipboard."))
        # control_lay.addWidget(self.button_copy_all)

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

        # RUN
        self.buttonRun = FCButton(_('Run'))
        self.buttonRun.setToolTip(_("Will run the TCL commands found in the text file, one by one."))
        self.buttonRun.hide()
        control_lay.addWidget(self.buttonRun)

        # #############################################################################################################
        # ################### SIGNALS #################################################################################
        # #############################################################################################################
        self.code_editor.textChanged.connect(self.handleTextChanged)
        self.buttonOpen.clicked.connect(self.handleOpen)
        self.buttonSave.clicked.connect(self.handleSaveGCode)
        self.buttonPrint.clicked.connect(self.handlePrint)
        self.buttonPreview.clicked.connect(self.handlePreview)
        self.buttonFind.clicked.connect(self.handleFindGCode)
        self.entryFind.returnPressed.connect(self.handleFindGCode)
        self.buttonReplace.clicked.connect(self.handleReplaceGCode)

        self._find_timer = QtCore.QTimer(self)
        self._find_timer.setSingleShot(True)
        self._find_timer.timeout.connect(self.highlight_find_text)
        self.entryFind.textChanged.connect(self.on_text_changed)

        # self.button_copy_all.clicked.connect(self.handleCopyAll)

        self.code_editor.set_model_data(self.app.regFK.myKeywords)

        self.code_edited = ''

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

    def load_text(self, text, move_to_start=False, move_to_end=False, clear_text=True, as_html=False):
        try:
            self.code_editor.textChanged.disconnect(self.handleTextChanged)
        except (AttributeError, TypeError):
            pass
        try:
            # setPlainText/setHtml replace the text; clearing first notifies source observers twice.
            self.code_editor.setReadOnly(False)
            if as_html is False:
                self.code_editor.setPlainText(text)
            else:
                if isinstance(self.code_editor, QtWidgets.QTextEdit):
                    self.code_editor.setHtml(text)
                else:
                    self.code_editor.setPlainText(text)
        except Exception as err:
            self.app.log.error("AppTextEditor.load_text() --> %s." % str(err))
            return
        finally:
            self.code_editor.textChanged.connect(self.handleTextChanged)
        if self.plain_text:
            self.editor_class.number_bar.adjustWidth(self.code_editor.blockCount())
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
                self.code_editor.setPlainText(text)
                owner.script_filename = path
                owner.mark_project_dirty()
                self.app.file_opened.emit('script', path)
                return
            file = QtCore.QFile(path)
            if file.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
                stream = QtCore.QTextStream(file)
                self.code_edited = stream.readAll()
                self.code_editor.setPlainText(self.code_edited)
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
                    lined_gcode = my_gcode.replace("\n", "<br />")

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

        flags = QtGui.QTextDocument.FindFlag.FindCaseSensitively
        text_to_be_found = self.entryFind.get_value()

        r = self.code_editor.find(str(text_to_be_found), flags)

        if r is False:
            msgbox = FCMessageBox(parent=self.app.ui)
            title = _("End of document.")
            txt = '%s' % _("Start from beginning?")
            msgbox.setWindowTitle(_('Find'))  # taskbar still shows it
            msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
            msgbox.setText('<b>%s</b>' % title)
            msgbox.setInformativeText(txt)
            msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/find32.png'))

            bt_ok = msgbox.addButton(_('Ok'), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            bt_cancel = msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)

            msgbox.setDefaultButton(bt_ok)
            msgbox.exec()
            response = msgbox.clickedButton()

            if response == bt_ok:
                self.code_editor.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
                self.code_editor.find(str(text_to_be_found), flags)

    def handleReplaceGCode(self):

        old = self.entryFind.get_value()
        new = self.entryReplace.get_value()

        if self.sel_all_cb.isChecked():
            if str(old) == '':
                return
            # Search the document from its start (the find() of the editor starts after its cursor and selection, so
            # the matches above them were skipped) and make every replacement in ONE edit block: one Undo step, and
            # no edit block is left open (the document then sent no more change signals).
            doc = self.code_editor.document()
            flags = QtGui.QTextDocument.FindFlag.FindCaseSensitively
            cursor = QtGui.QTextCursor(doc)
            cursor.beginEditBlock()
            found = QtGui.QTextCursor(doc)
            while True:
                found = doc.find(str(old), found, flags)
                if found.isNull():
                    break
                # the cursor is left after the new text: a find text inside the new text is not found again
                found.insertText(new)
            # Mark end of undo block
            cursor.endEditBlock()
            self.code_editor.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
        else:
            cursor = self.code_editor.textCursor()
            cursor.beginEditBlock()
            qc = self.code_editor.textCursor()
            if qc.hasSelection():
                qc.insertText(new)
            # Mark end of undo block
            cursor.endEditBlock()

    def on_text_changed(self, txt):
        self._find_timer.start(150)

    def highlight_find_text(self):
        extra_sel_list = []
        flags = QtGui.QTextDocument.FindFlag.FindCaseSensitively
        txt = self.entryFind.get_value()
        document = self.code_editor.document()
        cursor = QtGui.QTextCursor(document)
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtCore.Qt.GlobalColor.yellow)
        if txt:
            while True:
                cursor = document.find(str(txt), cursor, flags)
                if cursor.isNull():
                    break
                extra_sel = QtWidgets.QTextEdit.ExtraSelection()
                extra_sel.cursor = cursor
                extra_sel.format = fmt
                extra_sel_list.append(extra_sel)
        self.code_editor.setExtraSelections(extra_sel_list)

    # def handleCopyAll(self):
    #     text = self.code_editor.toPlainText()
    #     self.app.clipboard.setText(text)
    #     self.app.inform.emit(_("Content copied to clipboard ..."))

    # def closeEvent(self, QCloseEvent):
    #     super().closeEvent(QCloseEvent)
