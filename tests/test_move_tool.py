"""Move correctness, worker/GUI boundaries and Gerber batch translation."""

import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PyQt6 import QtCore, QtWidgets, QtTest
from shapely import Point, LineString, Polygon, affinity

from appParsers.ParseGerber import Gerber
from appParsers.ParseExcellon import Excellon
from appPlugins.ToolMove import ToolMove
from appPlugins.ToolMove.MoveGen import selection_shape
from camlib import translate_geometry


def make_gerber(count=500):
    elements = [
        {'solid': Point(index, 2).buffer(0.2), 'follow': Point(index, 2),

         'clear': LineString([(index, 1), (index, 3)])}
        for index in range(count)
    ]
    return SimpleNamespace(
        app=Mock(), solid_geometry=[element['solid'] for element in elements],
        follow_geometry=[element['follow'] for element in elements],
        tools={10: {'geometry': elements}, 11: {'type': 'C'},
               12: {'geometry': [{'solid': None}, {'follow': []}, {}]}})


def test_gerber_offset_batches_apertures_and_preserves_all_geometry():
    obj = make_gerber()
    original = [dict(element) for element in obj.tools[10]['geometry']]
    with patch('appParsers.ParseGerber.translate_geometry', wraps=translate_geometry) as translate:
        assert Gerber.offset(obj, (3, -5)) is None
    # Batch calls scale with apertures, not with the number of pads/tracks.
    assert translate.call_count <= 5
    for old, new in zip(original, obj.tools[10]['geometry']):
        for key in old:
            assert new[key].equals_exact(affinity.translate(old[key], 3, -5), 1e-10)
    assert obj.solid_geometry[0].equals_exact(obj.tools[10]['geometry'][0]['solid'], 1e-10)
    assert obj.follow_geometry[0].equals_exact(obj.tools[10]['geometry'][0]['follow'], 1e-10)
    assert obj.tools[12]['geometry'] == [{'solid': None}, {'follow': []}, {}]


def make_excellon(count=500):
    obj = SimpleNamespace(app=Mock(), circle_steps=lambda radius: 32, tools={
        1: {'tooldia': 0.8, 'drills': [Point(index, 2) for index in range(count)],
            'slots': [(Point(1, 3), Point(4, 3))]},
        2: {'tooldia': 1.2, 'drills': [Point(5, 6)], 'slots': []}})
    obj.create_geometry = lambda: Excellon.create_geometry(obj)
    assert obj.create_geometry() is None
    return obj


def test_excellon_move_translates_existing_solids_without_rebuffering():
    obj = make_excellon()
    before = list(obj.solid_geometry)
    with patch.object(obj, 'create_geometry', wraps=obj.create_geometry) as rebuild:
        Excellon.offset(obj, (3, -5))
    rebuild.assert_not_called()
    for old, new in zip(before, obj.solid_geometry):
        assert new.equals_exact(affinity.translate(old, 3, -5), 1e-10)
    assert obj.tools[1]['drills'][0].equals(Point(3, -3))
    assert obj.tools[1]['slots'][0][0].equals(Point(4, -2))
    assert obj.tools[1]['slots'][0][1].equals(Point(7, -2))
    assert obj.tools[2]['drills'][0].equals(Point(8, 1))
    assert obj.solid_geometry == [geo for tool in obj.tools.values() for geo in tool['solid_geometry']]


@pytest.fixture
def tool():
    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = Mock()
    app.decimals = 4
    app.proc_container.new.side_effect = lambda text: nullcontext()
    app.collection.get_selected.return_value = []
    move = ToolMove(app)
    yield move
    move.close()
    qt_app.processEvents()


def make_layer(events, threads):
    layer = SimpleNamespace(kind='gerber', visible=True, obj_options={'plot': True},
                            geometry=Point(10, 20))

    def record(name):
        events.append(name)
        threads.append((name, QtCore.QThread.currentThread()))

    def offset(vector):
        record('offset')
        layer.geometry = affinity.translate(layer.geometry, *vector)

    layer.offset = Mock(side_effect=offset)
    layer.bounds = Mock(side_effect=lambda: layer.geometry.bounds)
    layer.plot = Mock(side_effect=lambda: record('plot'))
    layer.regenerate_source = Mock(side_effect=lambda: record('export'))
    layer.set_offset_values = Mock(side_effect=lambda **kwargs: record('ui'))
    layer.ui = SimpleNamespace(aperture_table_visibility_cb=QtWidgets.QCheckBox())
    layer.ui.aperture_table_visibility_cb.setChecked(True)
    layer.ui.aperture_table_visibility_cb.toggled.connect(lambda checked: record('checkbox'))
    layer.replotApertures = Mock()
    return layer


def run_worker(tool):
    task = tool.app.worker_task.emit.call_args.args[0]
    worker = threading.Thread(target=lambda: task['fcn'](*task['params']))
    worker.start()
    worker.join(5)
    assert not worker.is_alive()


def test_moved_cncjob_exports_the_moved_gcode_also_after_undo_redo(tool):
    # 'Export G-Code' writes source_file: the move makes it again, inside the Undo snapshot of the CNCJob
    from appHandlers.appHistory import HistoryManager
    from appObjects.CNCJobObject import CNCJobObject
    from tests.helpers_objects import make_app_0b

    path = [(10, 0), (10, 5), (14, 5)]
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        job = CNCJobObject('job', make_app_0b())
    job.multitool, job.obj_options['type'] = True, 'Geometry'
    job.gcode_parsed = [{'geom': LineString(path), 'kind': ['C', 'S']}]
    job.tools = {1: {'gcode': 'G00 X10.0000 Y0.0000\nG01 X10.0000 Y5.0000\nG01 X14.0000 Y5.0000\n',
                     'gcode_parsed': job.gcode_parsed, 'solid_geometry': [LineString(path)],
                     'data': {'tools_mill_ppname_g': 'default'}}}
    job.plot, job.set_offset_values = Mock(), Mock()
    assert job.regenerate_source() is True
    old = job.source_file
    tool.app.headless, tool.app.call_source = False, 'app'
    tool.app.projects = SimpleNamespace(is_busy=lambda: False)
    tool.app.history = HistoryManager(tool.app, collection=SimpleNamespace(get_list=lambda: [job]))

    tool.move_handler((3, -5), objects=[job])
    run_worker(tool)
    QtTest.QTest.qWait(20)

    moved = job.source_file
    assert 'G01 X17.0000 Y0.0000' in job.tools[1]['gcode']
    assert 'G01 X17.0000 Y0.0000' in moved and moved == job.regenerate_source(store=False)
    assert any('[success]' in call.args[0] for call in tool.app.inform.emit.call_args_list)
    assert tool.app.history.undo() and job.source_file == old
    assert tool.app.history.redo() and job.source_file == moved


@pytest.mark.parametrize('offset', [(float('nan'), 2)])
def test_invalid_move_offset_is_rejected_before_any_change(tool, offset):
    layer = make_layer([], [])
    assert tool.move_handler(offset, objects=[layer]) == 'fail'
    layer.offset.assert_not_called()
    tool.app.worker_task.emit.assert_not_called()
    assert any('[ERROR_NOTCL]' in call.args[0] for call in tool.app.inform.emit.call_args_list)





@pytest.fixture
def source_tool(tool):
    """A move tool on a real headless app: objects regenerate their source with the real exports."""
    from appHandlers.appHistory import HistoryManager
    from appHandlers.appIO import appIO
    from tests.helpers_objects import make_app

    app = make_app()
    app.f_handlers = appIO(app)
    app.headless, app.projects, app.delete_selection_shape = False, SimpleNamespace(is_busy=lambda: False), Mock()
    app.proc_container.new.side_effect = lambda text: nullcontext()
    app.collection = SimpleNamespace(get_list=lambda: [], get_by_name=lambda name: app.objects[name],
                                     get_selected=lambda: [])
    app.objects = {}
    app.history = HistoryManager(app, collection=app.collection)
    tool.app = app
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        yield tool


def real_object(tool, kind):
    from appObjects.GeometryObject import GeometryObject
    from tests.helpers_objects import make_excellon, make_gerber

    app = tool.app
    if kind == 'geometry':
        obj = GeometryObject('obj', app)
        obj.solid_geometry = [LineString([(0, 0), (4, 0)]), Polygon([(1, 1), (3, 1), (3, 3)])]
        obj.source_file = 'stale text'
    else:
        obj = (make_gerber if kind == 'gerber' else make_excellon)(app, name='obj')
        assert obj.regenerate_source() is True
        obj.ui = SimpleNamespace(aperture_table_visibility_cb=QtWidgets.QCheckBox())
    obj.plot, obj.set_offset_values = Mock(), Mock()
    app.objects['obj'] = obj
    return obj


def move_and_finish(tool, obj, offset):
    """Run the move job and its GUI completion. The background source task is left queued."""
    tool.move_handler(offset, objects=[obj])
    task = tool.app.worker_task.emit.call_args_list[-1].args[0]
    task['fcn'](*task['params'])
    QtTest.QTest.qWait(20)


@pytest.mark.parametrize('kind', ['geometry', 'gerber', 'excellon'])
def test_source_follows_the_move_after_done_and_a_flush(source_tool, kind):
    from appHandlers.appSourceRegen import flush
    tool = source_tool
    obj = real_object(tool, kind)
    old = obj.source_file
    move_and_finish(tool, obj, (3, -5))

    assert any('[success]' in call.args[0] for call in tool.app.inform.emit.call_args_list), (
        tool.app.inform.emit.call_args_list, tool.app.log.exception.call_args_list)
    assert obj.source_file == old  # "Done" does not wait for the export
    flush(tool.app)
    assert obj.source_file != old
    if kind == 'geometry':  # a DXF text has a GUID and a time stamp: look for the moved coordinates
        assert '-5.0' in obj.source_file
    else:
        assert obj.source_file == obj.regenerate_source(store=False)


def test_save_source_right_after_a_move_writes_the_new_text(source_tool, tmp_path):
    tool = source_tool
    obj = real_object(tool, 'gerber')
    move_and_finish(tool, obj, (3, -5))
    target = tmp_path / 'saved.gbr'
    tool.app.f_handlers.save_source_file('obj', str(target))
    assert obj.source_file in target.read_text()
    assert obj.source_file == obj.regenerate_source(store=False)


def test_two_quick_moves_give_the_source_of_the_final_position(source_tool):
    from appHandlers.appSourceRegen import flush
    tool = source_tool
    obj = real_object(tool, 'gerber')
    old = obj.source_file
    move_and_finish(tool, obj, (3, -5))
    move_and_finish(tool, obj, (1, 1))
    flush(tool.app)
    assert obj.source_file != old and obj.source_file == obj.regenerate_source(store=False)
    assert obj.bounds()[0] == pytest.approx(make_start_xmin(tool) + 4, abs=1e-6)


def test_copy_right_after_a_move_does_not_wait_and_gets_the_moved_source(source_tool):
    from appHandlers.appObjectOps import AppObjectOps
    from appHandlers.appSourceRegen import flush
    from appObjects.GerberObject import GerberObject
    tool = source_tool
    app = tool.app
    obj = real_object(tool, 'gerber')
    old = obj.source_file
    move_and_finish(tool, obj, (3, -5))
    made = []

    def new_object(kind, name, initialize, *args, **kwargs):
        made.append(GerberObject(name, app))
        initialize(made[-1], app)
        return made[-1]

    app.app_obj.new_object.side_effect = new_object
    app.collection.get_selected = lambda: [obj]
    AppObjectOps(app).on_copy_command()
    assert obj.source_file == old  # the copy did not run the export of the source in the GUI thread
    flush(app)
    assert obj.source_file != old
    assert made[0].source_file == made[0].regenerate_source(store=False) != old


def make_start_xmin(tool):
    from tests.helpers_objects import make_gerber
    return make_gerber(tool.app, name='ref').bounds()[0]
