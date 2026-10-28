"""GEO09: committed geometry owns a serializable, non-authoritative DXF snapshot."""
from io import StringIO
from time import perf_counter
from types import SimpleNamespace
from threading import Thread
from unittest.mock import Mock, patch

import ezdxf
import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, Polygon

from appEditors.appGeoEditor import AppGeoEditor
from appHandlers.appIO import appIO
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appProjects import ProjectManager
from appMain import App
from appObjects.GeometryObject import GeometryObject
from appWorker import Worker
from appProcess import FCVisibleProcessContainer
from appGUI.GUIElements import FlatCAMActivityView
from tests.test_geo_editor_history import editor_with_history
from tests.test_geo_editor_save import save_editor
from tests.helpers_objects import make_app, save_and_load  # noqa: F401,E402


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield app


def make_geometry(units='MM', multigeo=False):
    app = make_app()
    app.app_units = units
    app.options['geometry_dxf_format'] = 'R2010'
    app.f_handlers = appIO(app)
    obj = GeometryObject('geo', app)
    obj.obj_options['tools_mill_tooldia'] = 0.5
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]])
    line = LineString([(5, 0), (6, 1)])
    obj.solid_geometry = [polygon, line]
    obj.multigeo = multigeo
    if multigeo:
        obj.tools = {1: {'tooldia': 0.5, 'data': {'tools_mill_tooldia': 0.5}, 'solid_geometry': [polygon]},
                     2: {'tooldia': 0.8, 'data': {'custom': 'keep'}, 'solid_geometry': [line]}}
    return app, obj


def app_save_editor(app, obj, shapes, monkeypatch):
    """Real QObject/queued completion, with only the editor's rendering/UI stubbed."""
    editor = AppGeoEditor.__new__(AppGeoEditor)
    QtCore.QObject.__init__(editor)
    editor.__dict__.update(save_editor(shapes).__dict__)
    editor.app = app
    if hasattr(editor, 'source_save_done'):
        editor.source_save_done.connect(editor.on_source_save_done, QtCore.Qt.ConnectionType.QueuedConnection)
    app.geo_editor, app.call_source = editor, 'geo_editor'
    app.collection.get_active.return_value = obj
    app.ui.notebook.count.return_value = 0
    app.old_state_of_tools_toolbar = False
    app.resource_location = ''
    app.post_edit_sig = Mock()
    app._history_edit_saved = Mock()
    obj.build_ui, obj.plot = Mock(), Mock()
    dialog = Mock()
    dialog.clickedButton.return_value = dialog.addButton.return_value
    monkeypatch.setattr('appMain.FCMessageBox', Mock(return_value=dialog))
    return editor


def test_save_exports_in_the_worker_and_completes_on_the_gui_thread(monkeypatch):
    app, obj = make_geometry()
    obj.source_file = 'previous source'
    line = LineString([(0, 0), (1, 0)])
    editor = app_save_editor(app, obj, [], monkeypatch)
    editor.storage = SimpleNamespace(get_objects=lambda: [SimpleNamespace(geo=line) for _ in range(50)])
    view = Mock(spec=FlatCAMActivityView)
    app.proc_container = FCVisibleProcessContainer(view, app)
    worker_thread = QtCore.QThread()
    worker = Worker(app, 'source-test')
    worker.moveToThread(worker_thread)
    worker.worker_task_signal.connect(worker.do_worker_task, QtCore.Qt.ConnectionType.QueuedConnection)
    worker_thread.finished.connect(worker.deleteLater)
    app.worker_task.emit.side_effect = worker.worker_task_signal.emit
    worker_thread.start()
    gui_thread = QtCore.QThread.currentThread()
    export_threads, finalized_threads = [], []
    export_dxf = app.f_handlers.export_dxf

    def recorded_export(*args, **kwargs):
        export_threads.append(QtCore.QThread.currentThread())
        return export_dxf(*args, **kwargs)
    monkeypatch.setattr(app.f_handlers, 'export_dxf', recorded_export)
    editor.deactivate.side_effect = lambda: finalized_threads.append(QtCore.QThread.currentThread())
    try:
        assert App.on_editing_finished(app) == 'pending'
        # nothing is exported or finished before the event loop runs
        assert obj.source_file == 'previous source'
        assert not export_threads
        editor.deactivate.assert_not_called()
        app.post_edit_sig.emit.assert_not_called()
        loop = QtCore.QEventLoop()
        poll = QtCore.QTimer()
        poll.timeout.connect(lambda: loop.quit() if app.post_edit_sig.emit.called else None)
        poll.start(10)
        QtCore.QTimer.singleShot(10000, loop.quit)
        loop.exec()
        poll.stop()
        assert app.post_edit_sig.emit.call_count == 1
        assert len(export_threads) == 1 and export_threads[0] is not gui_thread
        assert finalized_threads == [gui_thread]
        assert app.proc_container.get_procs_count() == 0
        assert len(ezdxf.read(StringIO(obj.source_file)).modelspace()) == 50
    finally:
        worker_thread.quit()
        assert worker_thread.wait(30000)




def test_generate_without_storing_preserves_source_owner():
    _, obj = make_geometry()
    obj.source_file = 'previous source'
    text = obj.regenerate_source(store=False)
    assert obj.source_file == 'previous source'
    assert len(ezdxf.read(StringIO(text)).modelspace()) == 3








def test_worker_submission_failure_keeps_committed_geometry_and_finishes_once(monkeypatch):
    app, obj = make_geometry()
    assert obj.regenerate_source()
    previous = obj.source_file
    line = LineString([(8, 0), (9, 0)])
    editor = app_save_editor(app, obj, [line], monkeypatch)
    app.worker_task.emit.side_effect = RuntimeError('queue unavailable')
    assert App.on_editing_finished(app) is None
    assert obj.solid_geometry == [line]
    assert obj.source_file == previous
    assert editor._source_save_pending is False
    editor.deactivate.assert_called_once()
    app.post_edit_sig.emit.assert_called_once()
    assert any('stale' in str(call).lower() for call in app.inform.emit.call_args_list)






@pytest.mark.parametrize('units,unit_code', [('MM', 4)])
@pytest.mark.parametrize('multigeo', [False, True])
def test_editor_snapshot_project_restore_and_disk_export(tmp_path, units, unit_code, multigeo):
    app, obj = make_geometry(units, multigeo)
    polygon, line = obj.solid_geometry
    editor = save_editor([polygon] if multigeo else [polygon, line], multigeo_tool=1 if multigeo else None)
    editor.app = app
    obj.source_file = 'stale'
    seen = []
    editor.deactivate.side_effect = lambda: seen.append(obj.source_file)
    assert AppGeoEditor.update_editor_geometry(editor, obj) != 'fail'
    assert seen == [obj.source_file] and obj.source_file != 'stale'
    app.file_saved.emit.assert_not_called()
    project, restored = save_and_load(app, [obj], tmp_path)
    assert project['objs'][0]['source_file'] == obj.source_file
    assert len(restored) == 1
    again = restored[0]
    assert again.source_file == obj.source_file
    assert again.multigeo == multigeo
    if multigeo:
        assert again.tools[2]['data'] == {'custom': 'keep'}
        assert again.tools[2]['solid_geometry'][0].equals(line)
    snapshot = ezdxf.read(StringIO(again.source_file))
    assert snapshot.units == unit_code
    assert snapshot.header['$MEASUREMENT'] == (1 if units == 'MM' else 0)
    paths = list(snapshot.modelspace())
    assert [entity.closed for entity in paths] == [True, True, False]
    expected = [list(polygon.exterior.coords), list(polygon.interiors[0].coords), list(line.coords)]
    assert [list(entity.get_points('xy')) for entity in paths] == expected
    filename = tmp_path / 'export.dxf'
    app.collection.get_by_name.return_value = again
    app.f_handlers.export_dxf('geo', str(filename), use_thread=False)
    exported = ezdxf.readfile(filename)
    assert exported.units == unit_code
    assert [list(entity.get_points('xy')) for entity in exported.modelspace()] == expected
    # Reimport is an explicit new object, not replacement of the original tool assignments.
    imported = GeometryObject('external', app)

    def new_object(kind, name, initialize, **kwargs):
        assert kind == 'geometry' and name == 'external'
        assert initialize(imported, app) != 'fail'
        return imported

    app.app_obj.new_object.side_effect = new_object
    assert app.f_handlers.import_dxf(str(filename), outname='external', plot=False) != 'fail'
    assert imported.source_file == filename.read_text(encoding='utf-8')
    assert again.source_file == obj.source_file
    assert len(imported.solid_geometry) == 3
    assert sorted(geo.length for geo in imported.solid_geometry) == pytest.approx(
        sorted([polygon.exterior.length, polygon.interiors[0].length, line.length]))


def test_old_project_without_source_still_loads():
    app, obj = make_geometry()
    data = obj.to_dict()
    data.pop('source_file', None)
    again = GeometryObject('loaded', app)
    again.from_dict(data)
    assert again.source_file == ''
    assert again.solid_geometry == obj.solid_geometry


def test_svg_and_dxf_import_with_letters_that_are_not_ascii(tmp_path):
    # the import reads the parsed file again for its source text: a UTF-8 SVG on a cp1254 system and an ANSI
    # (cp1254) DXF made the import fail there, after a good parse
    app, _ = make_geometry()
    svg = tmp_path / 'title.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" viewBox="0 0 20 10">'
                   '<title>KARŞI ŞASE</title><rect x="2" y="2" width="10" height="5"/></svg>', encoding='utf-8')
    dxf = tmp_path / 'ansi.dxf'
    doc = ezdxf.new('R2000')
    doc.encoding = 'cp1254'
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True)
    doc.modelspace().add_text('KARŞI', dxfattribs={'height': 2})
    doc.saveas(dxf)
    assert b'KAR\xdeI' in dxf.read_bytes()
    created = {}

    def new_object(kind, name, initialize, **kwargs):
        created[name] = GeometryObject(name, app)
        return initialize(created[name], app)

    app.app_obj.new_object.side_effect = new_object
    assert app.f_handlers.import_svg(str(svg), outname='svg', plot=False) != 'fail'
    assert app.f_handlers.import_dxf(str(dxf), outname='dxf', plot=False, text_mode='none') != 'fail'
    assert created['svg'].solid_geometry and '<title>KARŞI ŞASE</title>' in created['svg'].source_file
    assert len(created['dxf'].solid_geometry) == 1 and 'ANSI_1254' in created['dxf'].source_file


@pytest.mark.parametrize('failure', ['new', 'write'])
def test_dxf_failure_preserves_good_source_and_does_not_export(tmp_path, monkeypatch, failure):
    app, obj = make_geometry()
    assert obj.regenerate_source()
    old_source = obj.source_file
    real_doc = ezdxf.new('R2010')
    if failure == 'new':
        monkeypatch.setattr('appObjects.GeometryObject.ezdxf.new', Mock(side_effect=ValueError('new failed')))
    elif failure == 'partial':
        real_add = real_doc.modelspace().add_lwpolyline
        calls = []

        def add(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:
                raise ValueError('entity failed after first path')
            return real_add(*args, **kwargs)

        monkeypatch.setattr(real_doc.modelspace(), 'add_lwpolyline', add)
        monkeypatch.setattr('appObjects.GeometryObject.ezdxf.new', lambda *a: real_doc)
        assert obj.export_dxf() is None
    else:
        monkeypatch.setattr(real_doc, 'write', Mock(side_effect=ValueError('text serialization failed')))
        monkeypatch.setattr('appObjects.GeometryObject.ezdxf.new', lambda *a: real_doc)
    assert obj.regenerate_source() is False
    assert obj.source_file == old_source
    if failure != 'write':
        filename = tmp_path / 'failed.dxf'
        app.collection.get_by_name.return_value = obj
        app.f_handlers.export_dxf('geo', str(filename), use_thread=False)
        assert not filename.exists()
        app.file_saved.emit.assert_not_called()


@pytest.mark.parametrize('failure', [False, 'raise'])
def test_actual_app_edit_finish_commits_then_refreshes_before_close(tmp_path, monkeypatch, failure):
    app, obj = make_geometry()
    assert obj.regenerate_source()
    old_source = obj.source_file
    line = LineString([(10, 0), (12, 0)])
    editor = app_save_editor(app, obj, [line], monkeypatch)
    if failure == 'return':
        monkeypatch.setattr(obj, 'export_dxf', lambda: None)
    elif failure == 'raise':
        monkeypatch.setattr(obj, 'regenerate_source', Mock(side_effect=ValueError('unexpected exporter failure')))
    seen = []
    editor.deactivate.side_effect = lambda: seen.append((list(obj.solid_geometry), obj.source_file))
    assert App.on_editing_finished(app) == 'pending'
    assert obj.solid_geometry == [line]
    assert not seen
    app.post_edit_sig.emit.assert_not_called()
    job = app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    assert obj.source_file == old_source
    QtWidgets.QApplication.processEvents()
    assert seen == [([line], obj.source_file)]
    editor.deactivate.assert_called_once()
    app.post_edit_sig.emit.assert_called_once()
    if failure:
        assert obj.source_file == old_source
        assert any('stale' in str(call).lower() for call in app.inform.emit.call_args_list)
    else:
        doc = ezdxf.read(StringIO(obj.source_file))
        assert [list(e.get_points('xy')) for e in doc.modelspace()] == [list(line.coords)]
    _, restored = save_and_load(app, [obj], tmp_path)
    assert len(restored) == 1
    assert restored[0].solid_geometry == [line]
    assert restored[0].source_file == obj.source_file


def test_busy_app_exit_does_not_regenerate_or_close():
    editor = editor_with_history()
    editor.submit_worker(Mock(), [])
    app, obj = make_geometry()
    obj.regenerate_source = Mock()
    editor.deactivate = Mock()
    app.call_source, app.geo_editor = 'geo_editor', editor
    app.collection.get_active.return_value = obj
    assert App.on_editing_finished(app) == 'fail'
    obj.regenerate_source.assert_not_called()
    editor.deactivate.assert_not_called()
