"""Script and Document objects: construction, editing, save and load.

FlatCAMObj.__init__ forwarded the 'app' keyword to the next class in the MRO. For ScriptObject and DocumentObject
no camlib base follows QObject, so PyQt6 raised "TypeError: 'app' is an unknown keyword argument" and the app could
not create a Script or a Document object.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import pytest
from PyQt6 import QtWidgets

from appObjects.DocumentObject import DocumentObject
from appObjects.ScriptObject import ScriptObject
from tests.helpers_objects import no_shape_collection  # noqa: F401,E402


def test_geometry_starts_with_a_default_tool(no_shape_collection):
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b
    app = make_app_0b()
    app.options['tools_mill_tooldia'] = '0.3, 1.0'
    geo = GeometryObject('geometry', app)
    assert list(geo.tools) == [1]
    assert geo.tools[1]['tooldia'] == 0.3
    assert geo.tools[1]['solid_geometry'] == []


def test_blank_geometry_factory_accepts_a_diameter_list(no_shape_collection):
    from appObjects.AppObject import AppObject
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b
    app = make_app_0b()
    app.options['tools_mill_tooldia'] = '0.3, 1.0'
    created = []

    def new_object(kind, name, initialize, plot):
        obj = GeometryObject(name, app)
        initialize(obj, app)
        created.append(obj)
        return obj

    factory = SimpleNamespace(new_object=new_object)
    factory._app_option = lambda key: app.options[key]
    AppObject.new_geometry_object(factory, new_name='blank')
    assert created[0].tools[1]['tooldia'] == 0.3
    assert created[0].tools[1]['data']['name'] == 'blank'


@pytest.mark.parametrize('has_tools', [True, False])
def test_legacy_geometry_load_preserves_shared_shapes_and_saved_settings(no_shape_collection, has_tools):
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b
    from shapely.geometry import LineString
    geo = GeometryObject('geometry', make_app_0b())
    shapes = [LineString([(0, 0), (1, 0)]), LineString([(0, 1), (1, 1)])]
    # an old copy in a tool: the old app scaled, moved and edited only the shared shapes
    stale = [LineString([(5, 5), (6, 6)])]
    tools = {'3': {'tooldia': 0.5, 'data': {'cutz': -0.25, 'endz': 15}, 'solid_geometry': stale},
             '7': {'tooldia': 1.0, 'data': {'cutz': -0.5}, 'solid_geometry': []}} if has_tools else {}
    saved = dict(geo.to_dict(), multigeo=False, tools=tools, solid_geometry=shapes)
    saved.pop('source_file', None)
    geo.from_dict(saved)
    assert list(geo.tools) == (['3', '7'] if has_tools else [1])
    for tool in geo.tools.values():
        assert tool['solid_geometry'] == shapes
    # every tool mills the shapes, the object holds each shape 1 time
    assert geo.solid_geometry == shapes
    assert geo.bounds() == (0.0, 0.0, 1.0, 1.0)
    if has_tools:
        assert geo.tools['3']['data'] == {'cutz': -0.25, 'endz': 15}
    assert geo.source_file == ''


def test_geometry_reload_preserves_tool_shapes_with_an_empty_legacy_aggregate(no_shape_collection):
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_app_0b
    from shapely.geometry import LineString
    geo = GeometryObject('geometry', make_app_0b())
    shapes = [LineString([(0, 0), (1, 2)])]
    geo.tools[1]['solid_geometry'] = shapes
    restored = GeometryObject('restored', geo.app)
    restored.from_dict(geo.to_dict())
    assert restored.tools[1]['solid_geometry'] == shapes


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


def _document_with_ui(source_file='', document_type='text', **options):
    from appGUI.GUIElements import FCDetachableTab2
    from appGUI.ObjectUI import DocumentObjectUI
    from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
    from tests.helpers_objects import make_app as editor_app
    app = editor_app()
    app.options.update(options)
    app.inform = MagicMock()
    app.resource_location = 'assets/resources'
    app.regFK = SimpleNamespace(myKeywords=[])
    app.ui = QtWidgets.QWidget()
    app.ui.plot_tab_area = FCDetachableTab2(app.ui)
    app.ui.properties_scroll_area = QtWidgets.QScrollArea(app.ui)
    app.get_last_save_folder = lambda: ''
    app.pdf_viewer = PDFViewer(app)
    doc = DocumentObject('notes', app)
    if document_type == 'pdf':
        doc.set_pdf(source_file)
    else:
        doc.source_file = source_file
        doc.document_type = document_type
    doc.set_ui(DocumentObjectUI(app=app))
    return app, doc


def test_document_saves_html_with_formatting_and_the_toolbar_follows_the_cursor(tmp_path, monkeypatch,
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

    output = tmp_path / 'note.html'
    monkeypatch.setattr('appObjects.DocumentObject.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output.with_suffix('')), ''))
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


@pytest.mark.parametrize('document_type', ['text', 'html', 'pdf'])
def test_document_survives_a_project_save_and_load(tmp_path, no_shape_collection, document_type):  # noqa: F811
    # DocumentObject.set_ui() replaced ser_attrs with an 'options' attribute that FlatCAM objects do not have: with a
    # Document in the project, to_dict() raised and every project save (and autosave) failed with "Failed."
    from appCommon.ProjectFile import load_project_file
    from appGUI.ObjectUI import DocumentObjectUI
    from appHandlers.appIO import appIO
    from camlib import dict2obj
    from tests.test_pdf_viewer import pdf_bytes
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    content = {'text': 'Drill with 0.8 mm', 'html': '<p>Drill with <b>0.8 mm</b></p>', 'pdf': pdf_bytes()}
    app, doc = _document_with_ui(content[document_type], document_type)
    app.collection.get_list.return_value = [doc]
    app.collection.get_active.return_value = None
    path = str(tmp_path / 'project.FlatPrj')
    assert appIO(app).save_project(path, silent=True) is True
    entry = load_project_file(path, object_hook=dict2obj)['objs'][0]
    if document_type == 'html':
        entry.pop('document_type')  # older projects stored HTML without a document type
    restored = DocumentObject('restored', app)
    restored.from_dict(entry)
    assert restored.kind == 'document'
    assert restored.document_type == document_type
    assert restored.obj_options['name'] == 'notes'
    app.ui.plot_tab_area.closeTab(0)
    restored.set_ui(DocumentObjectUI(app=app))
    copied = DocumentObject('copy', app)
    copied.copy_from(restored)
    assert copied.document_type == document_type
    assert copied.source_file == restored.source_file
    if document_type == 'pdf':
        tab = app.ui.plot_tab_area.currentWidget()
        assert tab.data == content['pdf'] == restored.pdf_data
        assert tab.document.pageCount() == 1
        restored.build_ui()
        assert app.ui.plot_tab_area.count() == 1
        restored.delete()
        assert app.ui.plot_tab_area.count() == 0
    else:
        edit = restored.document_editor_tab.code_editor
        assert edit.toPlainText() == 'Drill with 0.8 mm'
        if document_type == 'html':
            cursor = edit.textCursor()
            cursor.setPosition(13)
            assert cursor.charFormat().fontWeight() >= 700
    app.ui.close()


@pytest.mark.parametrize('suffix', ['.PDF', '.txt', '.html', '.FlatDoc', '.log'])
def test_open_document_routes_content_and_rejects_invalid_input(tmp_path, no_shape_collection, suffix):
    from appGUI.ObjectUI import DocumentObjectUI
    from appHandlers.appIO import appIO
    from tests.test_pdf_viewer import pdf_bytes
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app, existing = _document_with_ui()
    created = []

    def create(kind, name, initialize, **kwargs):
        assert kind == 'document'
        doc = DocumentObject(name, app)
        if initialize(doc, app) == 'fail':
            return 'fail'
        doc.set_ui(DocumentObjectUI(app=app))
        created.append(doc)
        return doc

    app.app_obj.new_object = create
    rich = suffix in ('.html', '.FlatDoc')
    content = pdf_bytes() if suffix == '.PDF' else (
        '<p>café <b>board</b></p>' if rich else 'café <b>literal</b>').encode('utf-8-sig')
    path = tmp_path / ('board' + suffix)
    path.write_bytes(content)
    io = appIO(app)
    assert io.open_document(str(path)) != 'fail'
    doc = created[0]
    if suffix == '.PDF':
        assert app.ui.plot_tab_area.currentWidget().data == content
        assert doc.document_type == 'pdf'
    else:
        assert doc.document_editor_tab.code_editor.toPlainText() == ('café board' if rich else 'café <b>literal</b>')
    path.write_bytes(b'not a PDF' if suffix == '.PDF' else b'\xff\xfe\x00\x80')
    assert io.open_document(str(path)) == 'fail'
    assert len(created) == 1
    app.ui.close()


@pytest.mark.parametrize('document_type,suffix', [('pdf', '.pdf'), ('text', '.txt'), ('html', '.html')])
def test_document_save_uses_content_type_and_keeps_existing_file_on_failure(
        tmp_path, monkeypatch, no_shape_collection, document_type, suffix):
    from tests.test_pdf_viewer import pdf_bytes
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    content = pdf_bytes() if document_type == 'pdf' else 'café <b>board</b>'
    app, doc = _document_with_ui(content, document_type)
    output = tmp_path / ('saved' + suffix)
    monkeypatch.setattr('appObjects.DocumentObject.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output.with_suffix('')), ''))
    assert doc.save_document()
    if document_type == 'pdf':
        assert output.read_bytes() == content
    else:
        assert output.read_text(encoding='utf-8') == content
    assert not (tmp_path / 'saved').exists()
    app.file_saved.emit.reset_mock()
    monkeypatch.setattr('appObjects.DocumentObject.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(tmp_path / 'missing' / 'bad'), ''))
    assert not doc.save_document()
    app.file_saved.emit.assert_not_called()
    if document_type == 'pdf':
        assert output.read_bytes() == content
    app.ui.close()


@pytest.mark.parametrize('document_type,source', [('text', 'café board'), ('html', '<b>café</b> board')])
def test_document_exports_other_formats_by_extension_and_opens_by_extension(
        tmp_path, monkeypatch, no_shape_collection, document_type, source):
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app, doc = _document_with_ui(source, document_type)
    app.plotcanvas = SimpleNamespace(pagesize_dict={})
    app.app_units = 'mm'
    app.options.update({'global_tpdf_lmargin': 10, 'global_tpdf_tmargin': 10, 'global_tpdf_rmargin': 10,
                        'global_tpdf_bmargin': 10})
    picked = {}
    monkeypatch.setattr('appObjects.DocumentObject.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (picked['name'], picked.get('filter', '')))
    for name in ('a.TXT', 'a.FlatDoc', 'a.pdf', 'a.htm'):
        picked['name'] = str(tmp_path / name)
        assert doc.save_document()
        data = (tmp_path / name).read_bytes()
        if name.endswith('pdf'):
            assert data.startswith(b'%PDF')
        elif name.endswith('TXT'):
            assert data.decode('utf-8') == 'café board'
        else:
            assert b'caf' in data and (b'<b>' in data if document_type == 'html' else b'<html' in data.lower())
    picked.update(name=str(tmp_path / 'plain'), filter='FlatCAM Docs (*.FlatDoc)')
    assert doc.save_document() and (tmp_path / 'plain.FlatDoc').exists()
    assert doc.document_type == document_type and doc.source_file == source
    (tmp_path / 'in.html').write_text('<b>x</b> y', encoding='utf-8')
    (tmp_path / 'in.txt').write_text('<b>x</b> y', encoding='utf-8')
    (tmp_path / 'in.pdf').write_bytes(b'%PDF-1.4')
    assert doc.open_file_in_editor(str(tmp_path / 'in.html')) is None
    assert doc.document_type == 'html' and doc.document_editor_tab.code_editor.toPlainText() == 'x y'
    assert doc.open_file_in_editor(str(tmp_path / 'in.txt')) is None
    assert doc.document_type == 'text' and doc.source_file == '<b>x</b> y'
    assert doc.open_file_in_editor(str(tmp_path / 'in.pdf')) == 'fail'
    assert doc.source_file == '<b>x</b> y'
    app.ui.close()


def test_document_edits_mark_the_owning_project_and_plain_text_stays_plain(tmp_path, monkeypatch, no_shape_collection):
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app, doc = _document_with_ui()
    owner = app.collection
    context = SimpleNamespace(collection=owner, should_we_save=False)
    app.projects = SimpleNamespace(contexts=[context])
    doc._document_collection = owner
    app.collection = object()
    app.should_we_save = False
    text = 'changed <b>literal</b>'
    doc.document_editor_tab.code_editor.setPlainText(text)
    output = tmp_path / 'notes.txt'
    monkeypatch.setattr('appObjects.DocumentObject.FCFileSaveDialog.get_saved_filename',
                        lambda **kwargs: (str(output), ''))
    assert doc.save_document()
    assert output.read_text(encoding='utf-8') == text
    assert context.should_we_save
    assert not app.should_we_save
    app.ui.close()


def test_document_typing_is_classified_after_a_pause_and_every_reader_sees_it(no_shape_collection):
    from PyQt6 import QtGui, QtTest
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app, doc = _document_with_ui()
    edit = doc.document_editor_tab.code_editor
    edit.setPlainText('typed')
    assert doc.to_dict()['document_type'] == 'text' and doc.to_dict()['source_file'] == 'typed'
    cursor = edit.textCursor()
    cursor.select(QtGui.QTextCursor.SelectionType.Document)
    fmt = QtGui.QTextCharFormat()
    fmt.setFontWeight(QtGui.QFont.Weight.Bold)
    cursor.mergeCharFormat(fmt)
    assert not doc.ui.type_label.text().endswith('HTML')  # a keystroke does not classify
    QtTest.QTest.qWait(450)  # the pause: the timer classifies without any reader
    assert doc.ui.type_label.text().endswith('HTML')
    edit.setPlainText('last words')
    doc.close_editor()  # a pending edit is kept, and no timer fires on the closed editor
    QtTest.QTest.qWait(450)
    assert doc.source_file == 'last words'
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


@pytest.mark.parametrize('target_kind', ['gerber', 'excellon'])
def test_geometry_one_shot_conversion_keeps_shared_shapes_once(target_kind, no_shape_collection):
    from appHandlers.appEdit import appEditor
    from appObjects.GeometryObject import GeometryObject
    from shapely import Point
    from tests.helpers_objects import make_app_0b

    app = make_app_0b()
    app.dec_format = lambda value, decimals: round(value, decimals)
    source = GeometryObject('shared', app)
    shape = Point(2, 3).buffer(0.5)
    other = Point(7, 3).buffer(0.5)
    source.tools = {1: dict(source.default_tool(), solid_geometry=[shape]),
                    2: dict(source.default_tool(), solid_geometry=[shape.reverse(), other])}
    app.collection.get_selected = lambda: [source]
    made = []

    def new_object(kind, name, initializer, **kwargs):
        target = SimpleNamespace(tools={}, solid_geometry=[])
        assert initializer(target, app) != 'fail'
        made.append(target)
        return target

    app.app_obj.new_object = new_object
    assert getattr(appEditor, 'convert_any2' + target_kind)(app) is None
    assert len(made) == 1
    if target_kind == 'gerber':
        assert len(made[0].tools[0]['geometry']) == 2
        assert made[0].tools[0]['geometry'][0]['solid'].equals(shape)
        assert made[0].tools[0]['geometry'][1]['solid'].equals(other)
    else:
        drills = [p for tool in made[0].tools.values() for p in tool['drills']]
        assert len(drills) == 2
        assert drills[0].distance(Point(2, 3)) < 1e-9
        assert drills[1].distance(Point(7, 3)) < 1e-9


@pytest.mark.parametrize('mode', ['missing', None, False, True])
def test_geometry_load_without_mode_preserves_top_shapes_only_when_tools_are_empty(mode, no_shape_collection):
    from appObjects.GeometryObject import GeometryObject
    from shapely import LineString
    from tests.helpers_objects import make_app_0b

    app = make_app_0b()
    source = GeometryObject('source', app)
    top = LineString([(1, 2), (3, 4)])
    own = LineString([(10, 20), (30, 40)])
    saved = source.to_dict()
    saved.update(solid_geometry=[top], tools={1: dict(source.default_tool(), solid_geometry=[])})
    if mode == 'missing':
        # a file of an old release without the key (a new save always holds it)
        saved.pop('multigeo', None)
    else:
        saved['multigeo'] = mode
    restored = GeometryObject('loaded', app)
    restored.from_dict(saved)
    if mode is not True:
        assert len(restored.solid_geometry) == 1
        assert restored.solid_geometry[0].equals_exact(top, 0)
    saved['tools'][1]['solid_geometry'] = [own]
    restored.from_dict(saved)
    assert len(restored.solid_geometry) == 1
    # a single-geo object milled its shared shapes; the copy in its tool could be older
    assert restored.solid_geometry[0].equals_exact(top if mode is False else own, 0)


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
