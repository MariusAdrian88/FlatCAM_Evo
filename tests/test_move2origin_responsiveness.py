"""Real Qt action dispatch and worker/GUI completion boundaries for group moves."""

import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets, QtTest

from appHandlers.appObjectOps import AppObjectOps
from appHandlers.appSignalConnector import AppSignalConnector


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_app():
    app = Mock()
    app.options = {}
    app.defaults = {}
    app.ui = Mock()
    app.collection.get_selected.return_value = []
    app.proc_container.new.side_effect = lambda text: nullcontext()
    app.ui.pop_menucolor.actions.return_value = []
    return app


class Layer:
    def __init__(self, x, events, threads, entered=None, release=None):
        self.x = x
        self.obj_options = {}
        self.events = events
        self.threads = threads
        self.entered = entered
        self.release = release
        self.offset_entry = QtWidgets.QLineEdit()

    def bounds(self):
        return self.x, 5.0, self.x + 1.0, 6.0

    def offset(self, vector):
        self.threads.append(('offset', QtCore.QThread.currentThread()))
        self.x += vector[0]

    def plot(self):
        self.threads.append(('plot', QtCore.QThread.currentThread()))
        self.events.append('plot')

    def regenerate_source(self):
        self.threads.append(('export', QtCore.QThread.currentThread()))
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError('test did not release exporter')
        self.events.append('export')

    def set_offset_values(self, bounds=None):
        self.threads.append(('ui', QtCore.QThread.currentThread()))
        self.offset_entry.setText(str(bounds if bounds is not None else self.bounds()))
        self.events.append('ui')


def wait_for(predicate):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < 2000:
        QtTest.QTest.qWait(1)
    assert predicate(), 'Qt completion timed out'


def test_group_move_keeps_gui_alive_and_finalizes_after_export(qt_app):
    app = make_app()
    events, threads, tasks, gui_events = [], [], [], []
    entered, release = threading.Event(), threading.Event()
    first = Layer(10.0, events, threads, entered, release)
    second = Layer(20.0, events, threads)
    outsider = Layer(100.0, events, threads)
    app.collection.get_selected.return_value = [first, second]
    app.worker_task.emit.side_effect = tasks.append
    app.plotcanvas.fit_view.side_effect = lambda **kwargs: threads.append(('fit', QtCore.QThread.currentThread()))
    app.inform.emit.side_effect = lambda text: events.append(text)
    ops = AppObjectOps(app)
    ops.on_move2origin()
    app.collection.get_selected.return_value = [outsider]
    gui_thread = QtCore.QThread.currentThread()
    worker = threading.Thread(target=lambda: tasks[0]['fcn'](*tasks[0]['params']), daemon=True)
    worker.start()
    try:
        wait_for(entered.is_set)
        # A queued GUI event must run while export is blocked; periodic timer delivery depends on suite load.
        QtCore.QTimer.singleShot(0, lambda: gui_events.append(QtCore.QThread.currentThread()))
        wait_for(lambda: bool(gui_events))
        assert gui_events == [gui_thread] and worker.is_alive()
        assert not any(name == 'ui' or name == 'fit' for name, thread in threads)
        assert not any('Origin set' in event for event in events)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    wait_for(lambda: any('Origin set' in event for event in events))
    assert first.x == 0.0 and second.x == 10.0 and outsider.x == 100.0
    assert events.index('ui') > max(index for index, event in enumerate(events) if event == 'export')
    assert all(thread is not gui_thread for name, thread in threads if name in ('offset', 'plot', 'export'))
    assert all(thread is gui_thread for name, thread in threads if name in ('ui', 'fit'))
    assert first.obj_options['xmin'] == 0.0
    assert first.offset_entry.text() == str((0.0, 5.0, 1.0, 6.0))
    app.delete_selection_shape.assert_called_once_with()


def test_move_error_reports_after_worker_and_does_not_fit(qt_app):
    app = make_app()
    layer = Layer(10.0, [], [])
    layer.regenerate_source = Mock(side_effect=ValueError('export failed'))
    app.collection.get_selected.return_value = [layer]
    ops = AppObjectOps(app)
    ops.on_move2origin(use_thread=False)
    app.plotcanvas.fit_view.assert_not_called()
    assert 'export failed' in app.inform.emit.call_args.args[0]


class ClickCanvas:
    """The mouse_release handlers of the canvas (graph_event_connect / graph_event_disconnect) and a click."""

    def __init__(self):
        self.handlers = []

    def graph_event_connect(self, name, callback):
        self.handlers.append((name, callback))

    def graph_event_disconnect(self, name, callback=None):
        self.handlers = [handler for handler in self.handlers if handler != (name, callback)]

    def translate_coords(self, pos):
        return pos

    def click(self, button, pos):
        for name, callback in list(self.handlers):
            if name == 'mouse_release':
                callback(SimpleNamespace(button=button, pos=pos))


def test_set_origin_moves_the_objects_once_for_a_double_click(qt_app):
    app = make_app()
    app.defaults = Mock()
    app.replot_signal = MagicMock()
    app.grid_status.return_value = False
    app.plotcanvas = ClickCanvas()
    tasks = []
    app.worker_task.emit.side_effect = tasks.append
    AppObjectOps(app).on_set_origin()
    # the second click comes before the worker task of the first click has run
    app.plotcanvas.click(1, (3.0, 4.0))
    app.plotcanvas.click(1, (3.0, 4.0))
    assert len(tasks) == 1
    layer = Layer(10.0, [], [])
    app.collection.get_list.return_value = [layer]
    tasks[0]['fcn'](*tasks[0]['params'])
    assert layer.x == 7.0
