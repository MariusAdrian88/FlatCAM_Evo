"""App Undo / Redo history on real objects in a headless App: matrix, snapshot, delete + rebuild, create, rename."""

import os
import re
import sys
import time

import numpy as np
import pytest
from PyQt6 import QtWidgets

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appGUI.GUIElements as gui
import appHandlers.appHistory as appHistory
from appHandlers.appHistory import apply_matrix_to_object
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def fc():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app = App(qapp=qapp, headless=True)
    # the history records nothing in headless mode; these tests need it
    appHistory.RECORD_IN_HEADLESS = True
    yield app
    appHistory.RECORD_IN_HEADLESS = False


@pytest.fixture
def no_plot(monkeypatch):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()


def _load(fc, kind, filename, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, filename))
        if kind == 'excellon':
            obj.create_geometry()
    return fc.app_obj.new_object(kind, name, init, plot=False)


def _settle(fc, timeout=5.0):
    """Wait for the tasks that an object rebuild starts (the history refuses Undo / Redo while the app is busy)."""
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def _bounds(obj):
    return np.array(obj.bounds())


def test_matrix_undo_redo(fc, no_plot):
    grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'g_matrix')
    b0 = _bounds(grb)
    m = (0.0, -1.0, 1.0, 0.0, 10.0, 0.0)
    apply_matrix_to_object(grb, m)
    fc.history.record_matrix('rot', [(grb, m)])
    b1 = _bounds(grb)
    assert fc.history.undo() and np.allclose(_bounds(grb), b0)
    assert fc.history.redo() and np.allclose(_bounds(grb), b1)


def test_delete_undo_rebuilds_same_uid_and_later_steps_work(fc, no_plot):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_del')
    uid, b0 = exc.history_uid, _bounds(exc)
    m = (1.0, 0.0, 0.0, 1.0, 5.0, 5.0)
    apply_matrix_to_object(exc, m)
    fc.history.record_matrix('move', [(exc, m)])
    fc.history.record_delete('del', [exc])
    fc.object_ops.delete_objects_now([exc])
    assert fc.history.find(uid) is None
    assert fc.history.undo()                    # rebuild
    _settle(fc)
    rebuilt = fc.history.find(uid)
    assert rebuilt is not None and rebuilt is not exc
    assert fc.history.undo()                    # the move, on the rebuilt object
    assert np.allclose(_bounds(rebuilt), b0)


def test_create_undo_redo(fc, no_plot):
    count = len(fc.collection.get_list())
    grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'g_create')
    assert fc.history.undo_label().endswith('g_create')
    uid = grb.history_uid
    assert fc.history.undo() and fc.history.find(uid) is None
    assert len(fc.collection.get_list()) == count
    assert fc.history.redo() and fc.history.find(uid) is not None


def _select(fc, monkeypatch, objs):
    monkeypatch.setattr(fc.collection, 'get_selected', lambda: list(objs))


def _fake_spinner(value):
    class _Spinner:
        def __init__(self, *a, **k):
            pass

        def setWindowIcon(self, *a):
            pass

        def get_value(self):
            return value, True
    return _Spinner


@pytest.mark.parametrize('kind,filename', [('gerber', 'unify_mixed.gbr'), ('excellon', 'unify_mixed.drl')])
def test_rotate_30_undo_restores_bounds(fc, no_plot, monkeypatch, kind, filename):
    obj = _load(fc, kind, filename, 'rot30_' + kind)
    b0 = _bounds(obj)
    _select(fc, monkeypatch, [obj])
    fc.object_ops.on_rotate(silent=True, preset=30)
    b1 = _bounds(obj)
    assert not np.allclose(b1, b0)
    # a hole polygon is rotated twice (not rebuilt), so its circle approximation differs by a tiny amount
    assert fc.history.undo() and np.allclose(_bounds(obj), b0, atol=1e-3)
    assert fc.history.redo() and np.allclose(_bounds(obj), b1, atol=1e-3)


def test_transform_tool_group_undo(fc, no_plot, monkeypatch):
    g1 = _load(fc, 'gerber', 'unify_mixed.gbr', 'tt1')
    g2 = _load(fc, 'gerber', 'unify_mixed.gbr', 'tt2')
    _select(fc, monkeypatch, [g1, g2])
    b1, b2 = _bounds(g1), _bounds(g2)
    n = len(fc.history.undo_stack)
    fc.transform_tool.on_rotate_action(30.0, (1.0, 2.0))
    assert len(fc.history.undo_stack) == n + 1
    assert not np.allclose(_bounds(g1), b1)
    assert fc.history.undo()
    assert np.allclose(_bounds(g1), b1, atol=1e-6) and np.allclose(_bounds(g2), b2, atol=1e-6)


def _source_without_time(text):
    return re.sub(r'^(?:G04 |;)Created on :.*$', '', text or '', flags=re.M)


def test_move_tool_undo_redo_regenerates_source(fc, no_plot):
    kind, filename = 'gerber', 'unify_mixed.gbr'
    obj = _load(fc, kind, filename, 'move_source_' + kind)
    assert obj.regenerate_source()
    source0, b0 = _source_without_time(obj.source_file), _bounds(obj)
    fc.move_tool.move_handler((4.0, -2.5), objects=[obj])
    _settle(fc)
    source1 = _source_without_time(obj.source_file)
    assert source1 != source0
    assert fc.history.undo()
    assert np.allclose(_bounds(obj), b0)
    assert _source_without_time(obj.source_file) == source0
    assert fc.history.redo()
    assert _source_without_time(obj.source_file) == source1


def test_failed_source_regeneration_keeps_the_undo_step(fc, no_plot, monkeypatch):
    failure = 'raise'
    obj = _load(fc, 'gerber', 'unify_mixed.gbr', 'move_recovery')
    assert obj.regenerate_source()
    source0, b0 = _source_without_time(obj.source_file), _bounds(obj)
    fc.move_tool.move_handler((4.0, -2.5), objects=[obj])
    _settle(fc)
    source1, b1 = _source_without_time(obj.source_file), _bounds(obj)
    undo_len, redo_len, label = len(fc.history.undo_stack), len(fc.history.redo_stack), fc.history.undo_label()

    original = type(obj).regenerate_source

    def broken(self, *args, **kwargs):
        if failure == 'raise':
            raise RuntimeError('source export failed')
        return False

    monkeypatch.setattr(type(obj), 'regenerate_source', broken)
    try:
        assert fc.history.undo() is False
    finally:
        monkeypatch.setattr(type(obj), 'regenerate_source', original)

    assert np.allclose(_bounds(obj), b1)
    assert _source_without_time(obj.source_file) == source1
    assert len(fc.history.undo_stack) == undo_len
    assert len(fc.history.redo_stack) == redo_len
    assert fc.history.undo_label() == label

    assert fc.history.undo()
    assert np.allclose(_bounds(obj), b0)
    assert _source_without_time(obj.source_file) == source0
