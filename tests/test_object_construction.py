"""Every project object kind can be constructed.

FlatCAMObj.__init__ forwarded the 'app' keyword to the next class in the MRO. For ScriptObject and DocumentObject
no camlib base follows QObject, so PyQt6 raised "TypeError: 'app' is an unknown keyword argument" and the app could
not create a Script or a Document object.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from PyQt6 import QtWidgets

from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject
from tests.helpers_objects import make_app_0b as make_app, no_shape_collection  # noqa: F401,E402

KINDS = {
    GerberObject: 'gerber', ExcellonObject: 'excellon', GeometryObject: 'geometry', CNCJobObject: 'cncjob',
    ScriptObject: 'script', DocumentObject: 'document',
}


@pytest.mark.parametrize('cls', [GerberObject, ScriptObject, DocumentObject], ids=lambda c: c.__name__)
def test_object_kind_can_be_constructed(cls, no_shape_collection):  # noqa: F811
    app = make_app()
    obj = cls('obj', app)
    assert obj.kind == KINDS[cls]
    assert obj.obj_options['name'] == 'obj'
    assert obj.app is app


def test_script_edits_survive_load_serialization_and_copy(tmp_path, monkeypatch, no_shape_collection):
    from tests.helpers_objects import make_app as editor_app
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = editor_app()
    app.resource_location = 'assets/resources'
    app.regFK = SimpleNamespace(myKeywords=[])
    app.ui = QtWidgets.QWidget()
    app.ui.plot_tab_area = QtWidgets.QTabWidget(app.ui)
    app.ui.properties_scroll_area = QtWidgets.QScrollArea(app.ui)
    obj = ScriptObject('script', app)
    obj.source_file = 'set a 1'
    obj.build_ui()
    edit = obj.script_editor_tab.code_editor
    obj.script_editor_tab.load_text('set a 1')
    large = 'set a 2\n' * 1000
    edit.setPlainText(large)
    assert obj.source_file == large
    edit.setPlainText('set a 2')
    assert obj.source_file == 'set a 2'
    assert app.should_we_save
    restored = ScriptObject('restored', app)
    restored.from_dict(obj.to_dict())
    copied = ScriptObject('copy', app)
    copied.copy_from(obj)
    assert restored.source_file == copied.source_file == 'set a 2'
    inactive = object()
    context = SimpleNamespace(collection=inactive, should_we_save=False)
    app.collection = object()
    app.projects = SimpleNamespace(contexts=[context])
    obj._script_collection = inactive
    app.should_we_save = False
    edit.setPlainText('set a 3')
    assert context.should_we_save
    assert not app.should_we_save
    output = tmp_path / 'editor.tcl'
    monkeypatch.setattr('appEditors.appTextEditor.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output), ''))
    obj.script_editor_tab.handleSaveGCode(filt='FlatCAM Scripts (*.FlatScript)')
    assert output.read_text(encoding='utf-8') == 'set a 3'
    app.file_saved.emit.assert_called_once_with('script', str(output))
    edit.setPlainText('set a 4')
    monkeypatch.setattr('appEditors.appTextEditor.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(tmp_path / 'unexpected.tcl'), ''))
    obj.script_editor_tab.buttonSave.click()
    assert output.read_text(encoding='utf-8') == 'set a 4'
    app.ui.close()


def _document_with_ui(no_shape_collection_unused=None, **options):
    from appGUI.ObjectUI import DocumentObjectUI
    from tests.helpers_objects import make_app as editor_app
    app = editor_app()
    app.options.update(options)
    app.inform = MagicMock()
    app.resource_location = 'assets/resources'
    app.regFK = SimpleNamespace(myKeywords=[])
    app.ui = QtWidgets.QWidget()
    app.ui.plot_tab_area = QtWidgets.QTabWidget(app.ui)
    app.ui.properties_scroll_area = QtWidgets.QScrollArea(app.ui)
    doc = DocumentObject('notes', app)
    doc.set_ui(DocumentObjectUI(app=app))
    return app, doc


def test_document_saves_flatdoc_with_formatting_and_the_toolbar_follows_the_cursor(tmp_path, monkeypatch,
                                                                                    no_shape_collection):  # noqa: F811
    from PyQt6 import QtGui
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    app, doc = _document_with_ui()
    edit = doc.document_editor_tab.code_editor
    cursor = edit.textCursor()
    cursor.insertText('plain ')
    bold = QtGui.QTextCharFormat()
    bold.setFontWeight(QtGui.QFont.Weight.Bold.value)
    cursor.insertText('heavy', bold)
    cursor.insertText(' tail', QtGui.QTextCharFormat())

    toolbar = doc.rich_toolbar
    cursor.setPosition(9)  # inside "heavy"
    edit.setTextCursor(cursor)
    assert toolbar.bold_act.isChecked()
    cursor.setPosition(2)
    edit.setTextCursor(cursor)
    assert not toolbar.bold_act.isChecked()

    output = tmp_path / 'note.FlatDoc'
    monkeypatch.setattr('appObjects.DocumentObject.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output), ''))
    doc.document_editor_tab.buttonSave.click()

    app2, doc2 = _document_with_ui()
    doc2.document_editor_tab.load_text(output.read_text(encoding='utf-8'), as_html=True)
    edit2 = doc2.document_editor_tab.code_editor
    assert edit2.toPlainText() == 'plain heavy tail'
    probe = edit2.textCursor()
    probe.setPosition(10)
    edit2.setTextCursor(probe)
    assert doc2.rich_toolbar.bold_act.isChecked()
    app.ui.close()
    app2.ui.close()


def test_document_default_font_size_is_the_saved_option(no_shape_collection):  # noqa: F811
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    app, doc = _document_with_ui(document_font_size=10)
    assert float(doc.ui.font_size_cb.get_value()) == 10
    app.ui.close()


def test_document_survives_a_project_save_and_load(tmp_path, no_shape_collection):  # noqa: F811
    # DocumentObject.set_ui() replaced ser_attrs with an 'options' attribute that FlatCAM objects do not have: with a
    # Document in the project, to_dict() raised and every project save (and autosave) failed with "Failed."
    from appCommon.ProjectFile import load_project_file
    from appGUI.ObjectUI import DocumentObjectUI
    from appHandlers.appIO import appIO
    from camlib import dict2obj
    from tests.helpers_objects import make_app as editor_app
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    app = editor_app()
    app.inform = MagicMock()
    app.resource_location = 'assets/resources'
    app.regFK = SimpleNamespace(myKeywords=[])
    app.ui = QtWidgets.QWidget()
    app.ui.plot_tab_area = QtWidgets.QTabWidget(app.ui)
    app.ui.properties_scroll_area = QtWidgets.QScrollArea(app.ui)
    doc = DocumentObject('notes', app)
    doc.source_file = 'Drill with 0.8 mm'
    doc.set_ui(DocumentObjectUI(app=app))
    app.collection.get_list.return_value = [doc]
    app.collection.get_active.return_value = None
    path = str(tmp_path / 'project.FlatPrj')
    assert appIO(app).save_project(path, silent=True) is True
    entry = load_project_file(path, object_hook=dict2obj)['objs'][0]
    restored = DocumentObject('restored', app)
    restored.from_dict(entry)
    assert restored.kind == 'document'
    assert restored.obj_options['name'] == 'notes'
    restored.set_ui(DocumentObjectUI(app=app))
    assert restored.document_editor_tab.code_editor.toPlainText() == 'Drill with 0.8 mm'
    app.ui.close()


def test_script_utf8_atomic_save_origin_and_plain_export(tmp_path, monkeypatch, no_shape_collection):
    from appHandlers.appIO import appIO
    from tests.helpers_objects import make_app as editor_app
    app = editor_app()
    app.file_opened = Mock()
    app.file_saved = Mock()
    obj = ScriptObject('script', app)
    path = tmp_path / 'input.tcl'
    path.write_text('set text {café}', encoding='utf-8-sig')
    obj.parse_file(str(path))
    assert obj.source_file == 'set text {café}'
    output = tmp_path / 'output.tcl'
    app.collection = SimpleNamespace(get_by_name=lambda name: obj, get_active=lambda: obj)
    app.get_last_save_folder = lambda: str(tmp_path)
    io = SimpleNamespace(app=app, log=app.log, inform=Mock())
    io.save_source_file = lambda name, filename: appIO.save_source_file(io, name, filename)
    monkeypatch.setattr('appHandlers.appIO.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output), ''))
    assert appIO.on_file_save_script(io) != 'fail'
    app.file_saved.emit.assert_called_once_with('script', str(output))
    assert output.read_text(encoding='utf-8') == obj.source_file
    assert obj.script_filename == str(output)
    restored = ScriptObject('restored', app)
    restored.from_dict(obj.to_dict())
    assert restored.script_filename == str(output)
    obj.source_file = 'set updated 1'
    assert obj.save_file() != 'fail'
    assert output.read_text(encoding='utf-8') == 'set updated 1'
    assert obj.save_file(str(tmp_path / 'missing' / 'bad.tcl')) == 'fail'
    assert obj.script_filename == str(output)
    app.file_saved.reset_mock()
    monkeypatch.setattr('appHandlers.appIO.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(tmp_path / 'missing' / 'bad.tcl'), ''))
    assert appIO.on_file_save_script(io) == 'fail'
    app.file_saved.emit.assert_not_called()


def _code_editor_saving_to(output, monkeypatch, text):
    from appEditors.appTextEditor import AppTextEditor
    from tests.helpers_objects import make_app as editor_app
    app = editor_app()
    # kept for the whole test: the widgets need a QApplication
    app.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.resource_location = 'assets/resources'
    app.regFK = SimpleNamespace(myKeywords=[])
    app.plotcanvas = SimpleNamespace(pagesize_dict={'A4': (210, 297)})
    app.options['global_workspaceT'] = 'A4'
    editor = AppTextEditor(app=app, plain_text=True)
    editor.code_editor.setPlainText(text)
    monkeypatch.setattr('appEditors.appTextEditor.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output), ''))
    return app, editor


def test_code_editor_pdf_keeps_the_markup_characters_of_the_text(tmp_path, monkeypatch):
    # reportlab's Paragraph reads its text as markup: 'a<b' raised "paraparser: syntax error" (a Critical Error
    # dialog and no PDF), a '<T1>' comment was left out of the PDF
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph
    text = 'G1 X1 (a<b)\n(tool <T1> 0.8 mm & more)\nif {$x < 5} {puts ok}'
    output = tmp_path / 'code.pdf'
    app, editor = _code_editor_saving_to(output, monkeypatch, text)
    markups = []
    monkeypatch.setattr('appEditors.appTextEditor.Paragraph',
                        lambda markup, style: markups.append(markup) or Paragraph(markup, style))
    editor.handleSaveGCode(name='code')
    assert output.read_bytes().startswith(b'%PDF')
    frags = Paragraph(markups[0], getSampleStyleSheet()['Normal']).frags
    assert ''.join('\n' if getattr(frag, 'lineBreak', False) else frag.text for frag in frags) == text
    app.file_saved.emit.assert_called_once_with('cncjob', str(output))
