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


def test_move_finishes_plot_and_export_before_gui_completion(tool):
    events, threads = [], []
    layer = make_layer(events, threads)
    gui_thread = QtCore.QThread.currentThread()
    tool.app.inform.emit.side_effect = lambda text: events.append(text)
    tool.move_handler((3, -5), objects=[layer])
    run_worker(tool)
    QtTest.QTest.qWait(20)
    assert layer.geometry.equals(Point(13, 15))
    assert all(thread is gui_thread for name, thread in threads if name in ('ui', 'checkbox'))
    assert all(thread is not gui_thread for name, thread in threads if name in ('offset', 'plot', 'export'))
    layer.plot.assert_called_once()
    layer.regenerate_source.assert_called_once()
    layer.bounds.assert_called_once()
    layer.set_offset_values.assert_called_once_with(bounds=(13.0, 15.0, 13.0, 15.0))
    assert events.index('plot') < next(index for index, event in enumerate(events) if '[success]' in event)
    assert events.index('export') < events.index('ui')
    assert tool.app.should_we_save is True


@pytest.mark.parametrize('failure', ['offset', 'export'])
def test_move_failure_is_reported_and_moved_geometry_is_replotted(tool, failure):
    layer = make_layer([], [])
    if failure == 'offset':
        layer.offset.side_effect = None
        layer.offset.return_value = 'fail'
    else:
        layer.regenerate_source.side_effect = None
        layer.regenerate_source.return_value = False
    tool.move_handler((1, 2), objects=[layer])
    run_worker(tool)
    QtTest.QTest.qWait(20)
    assert any('[ERROR_NOTCL]' in call.args[0] for call in tool.app.inform.emit.call_args_list)
    assert not any('[success]' in call.args[0] for call in tool.app.inform.emit.call_args_list)
    layer.plot.assert_called_once()
    # a failed move releases the in-flight state too
    calls = tool.app.worker_task.emit.call_count
    tool.move_handler((1, 2), objects=[layer])
    assert tool.app.worker_task.emit.call_count == calls + 1
    run_worker(tool)
    QtTest.QTest.qWait(20)


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


@pytest.mark.parametrize('offset', [(float('nan'), 2), (1, 2, 3)])
def test_invalid_move_offset_is_rejected_before_any_change(tool, offset):
    layer = make_layer([], [])
    assert tool.move_handler(offset, objects=[layer]) == 'fail'
    layer.offset.assert_not_called()
    tool.app.worker_task.emit.assert_not_called()
    assert any('[ERROR_NOTCL]' in call.args[0] for call in tool.app.inform.emit.call_args_list)




def test_overlapping_move_is_rejected_until_the_gui_completion(tool):
    layer = make_layer([], [])
    offset_fn = layer.offset.side_effect
    started, release = threading.Event(), threading.Event()

    def blocked_offset(vector):
        started.set()
        assert release.wait(5)
        offset_fn(vector)

    layer.offset.side_effect = blocked_offset
    tool.move_handler((3, 0), objects=[layer])
    task = tool.app.worker_task.emit.call_args.args[0]
    worker = threading.Thread(target=lambda: task['fcn'](*task['params']))
    worker.start()
    try:
        assert started.wait(5)

        calls = tool.app.worker_task.emit.call_count
        assert tool.move_handler((0, 5), objects=[layer]) == 'fail'
        assert tool.app.worker_task.emit.call_count == calls
        assert layer.geometry.equals(Point(10, 20))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    QtTest.QTest.qWait(20)
    assert layer.geometry.equals(Point(13, 20))

    # the first move is complete: a later move is accepted again
    calls = tool.app.worker_task.emit.call_count
    tool.move_handler((0, 5), objects=[layer])
    assert tool.app.worker_task.emit.call_count == calls + 1
    run_worker(tool)
    QtTest.QTest.qWait(20)
    assert layer.geometry.equals(Point(13, 25))


@pytest.mark.parametrize('units, box', [
    ('MM', Polygon([(0, 0), (10, 0), (10, 0.1), (0, 0.1)])),
    ('MM', Polygon([(0, 0), (10, 0), (10, 0), (0, 0)])),
])
def test_selection_shape_keeps_thin_boxes_visible(units, box):
    preview = selection_shape(box, units, False)
    assert not preview.is_empty and preview.area > 0
    line = selection_shape(box, units, True)
    assert line.geom_type == 'LinearRing' and not line.is_empty

