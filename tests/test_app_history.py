"""Tests of the app Undo / Redo history (appHandlers/appHistory.py)."""

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shapely import LineString
from shapely.geometry import Point
from appHandlers.appHistory import (COORD_BYTES, HistoryError, HistoryManager, HistoryStep, SnapshotStep,
                                    invert_matrix, object_state)
from appHandlers.appProjects import ProjectManager
from appObjects.ProjectContext import ProjectContext
from tests.helpers_objects import make_app, make_gerber  # noqa: F401,E402


class FakeStep(HistoryStep):
    def __init__(self, label, log, size=0, uids=(), fail=False):
        self.label = label
        self.size = size
        self._uids = set(uids)
        self.log = log
        self.fail = fail

    def uids(self):
        return self._uids

    def undo(self, history):
        if self.fail:
            raise HistoryError('gone')
        self.log.append(('undo', self.label))

    def redo(self, history):
        self.log.append(('redo', self.label))


def fake_app(**options):
    app = SimpleNamespace(headless=False, call_source='app', inform=Mock(), history_changed=Mock(), log=Mock(),
                          options=dict(options), projects=SimpleNamespace(is_busy=lambda: False))
    return app


def manager(**options):
    app = fake_app(**options)
    history = HistoryManager(app, collection=SimpleNamespace(get_list=lambda: []))
    app.history = history
    return history, app


class TestManager(unittest.TestCase):

    def test_undo_redo_order_and_redo_cleared(self):
        h, _app = manager()
        log = []
        for name in 'abc':
            h.push(FakeStep(name, log))
        self.assertTrue(h.undo())
        self.assertTrue(h.undo())
        self.assertTrue(h.redo())
        self.assertEqual(log, [('undo', 'c'), ('undo', 'b'), ('redo', 'b')])
        self.assertEqual((h.undo_label(), h.redo_label()), ('b', 'c'))
        h.push(FakeStep('d', log))
        self.assertFalse(h.can_redo())
        self.assertFalse(h.redo())

    def test_memory_limit(self):
        h, _app = manager(global_history_memory_mb=1)
        log = []
        for i in range(5):
            h.push(FakeStep(str(i), log, size=400 * 1024))
        # 1 MB holds 2 steps of 400 KB
        self.assertEqual([s.label for s in h.undo_stack], ['3', '4'])
        # a single step bigger than the limit is kept, so the last action can always be undone
        h.push(FakeStep('big', log, size=5 * 1024 * 1024))
        self.assertEqual([s.label for s in h.undo_stack], ['big'])

    def test_group(self):
        h, _app = manager()
        log = []
        with h.group('all'):
            h.push(FakeStep('a', log))
            with h.group('inner'):
                h.push(FakeStep('b', log))
        self.assertEqual(len(h.undo_stack), 1)
        self.assertEqual(h.undo_label(), 'all')
        h.undo()
        self.assertEqual(log, [('undo', 'b'), ('undo', 'a')])
        h.redo()
        self.assertEqual(log[2:], [('redo', 'a'), ('redo', 'b')])

    def test_suspended_and_headless(self):
        h, app = manager()
        log = []
        with h.suspended():
            h.push(FakeStep('a', log))
        self.assertFalse(h.can_undo())
        app.headless = True
        h.push(FakeStep('b', log))
        self.assertFalse(h.can_undo())

    def test_blocked_in_editor_and_busy(self):
        h, app = manager()
        log = []
        h.push(FakeStep('a', log))
        app.call_source = 'geo_editor'
        self.assertFalse(h.undo())
        app.call_source = 'app'
        app.projects.is_busy = lambda: True
        self.assertFalse(h.undo())
        self.assertEqual(log, [])
        self.assertTrue(h.can_undo())

    def test_failing_step_is_retained_for_retry(self):
        h, app = manager()
        log = []
        h.push(FakeStep('ok', log))
        h.push(FakeStep('bad', log, fail=True))
        self.assertFalse(h.undo())
        self.assertEqual(h.undo_label(), 'bad')
        self.assertFalse(h.can_redo())
        self.assertIn('[ERROR_NOTCL]', app.inform.emit.call_args[0][0])
        h.undo_stack[-1].fail = False
        self.assertTrue(h.undo())


    def test_invert_matrix(self):
        m = (0.0, -2.0, 2.0, 0.0, 5.0, -3.0)
        a, b, d, e, xoff, yoff = invert_matrix(m)
        # m then its inverse on the point (1, 1)
        x, y = 0.0 * 1 - 2.0 * 1 + 5.0, 2.0 * 1 + 0.0 * 1 - 3.0
        self.assertAlmostEqual(a * x + b * y + xoff, 1.0)
        self.assertAlmostEqual(d * x + e * y + yoff, 1.0)
        with self.assertRaises(HistoryError):
            invert_matrix((1.0, 2.0, 2.0, 4.0, 0.0, 0.0))


class TestIdentityAndProjects(unittest.TestCase):

    def test_load_preserves_louddict_callback_and_defaults(self):
        for key in ('obj_options', 'options'):
            obj = make_gerber(make_app())
            options = obj.obj_options
            callback = Mock()
            options.set_change_callback(callback)
            options['project_default'] = 7
            obj.from_dict({key: {'name': 'loaded', 'plot': False}})
            self.assertIs(obj.obj_options, options)
            self.assertEqual(options['name'], 'loaded')
            self.assertEqual(options['project_default'], 7)
            options['plot'] = True
            callback.assert_called_with('plot')


class TestMerge(unittest.TestCase):

    def test_snapshots_with_the_same_key_merge(self):
        h, _app = manager()
        h.push(SnapshotStep('Cut Z', [('u1', {'v': 1}, {'v': 2})], merge_key=('cutz', 'u1', 3)))
        h.push(SnapshotStep('Cut Z', [('u1', {'v': 2}, {'v': 3})], merge_key=('cutz', 'u1', 3)))
        self.assertEqual(len(h.undo_stack), 1)
        self.assertEqual(h.undo_stack[0].items, [('u1', {'v': 1}, {'v': 3})])

    def test_other_key_other_objects_or_after_undo_do_not_merge(self):
        h, _app = manager()
        h.push(SnapshotStep('a', [('u1', {}, {})], merge_key='k'))
        h.push(SnapshotStep('b', [('u1', {}, {})], merge_key='other'))
        h.push(SnapshotStep('c', [('u2', {}, {})], merge_key='other'))
        h.push(SnapshotStep('d', [('u2', {}, {})]))
        h.push(SnapshotStep('e', [('u2', {}, {})]))
        self.assertEqual(len(h.undo_stack), 5)

class TestRebuildSaveRename(unittest.TestCase):

    def test_rename_to_a_taken_name_raises(self):
        a = SimpleNamespace(obj_options={'name': 'a'})
        b = SimpleNamespace(obj_options={'name': 'b'})
        collection = SimpleNamespace(get_list=lambda: [a, b], rename_object=Mock())
        h = HistoryManager(fake_app(), collection=collection)
        with self.assertRaises(HistoryError):
            h.rename(a, 'b')
        collection.rename_object.assert_not_called()
        h.rename(a, 'free')
        collection.rename_object.assert_called_once_with(a, 'free')


if __name__ == '__main__':
    unittest.main()
