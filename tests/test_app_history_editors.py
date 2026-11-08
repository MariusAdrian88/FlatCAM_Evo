"""Tests of the history step that App.on_editing_finished records for an editor save (Geometry, Gerber, Excellon,
CNCJob)."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shapely.geometry import box

from appHandlers.appHistory import HistoryManager, HistoryStep
from appMain import App
from appObjects.GeometryObject import GeometryObject

from tests.helpers_objects import app, _headless_qt  # noqa: F401,E402


class FakeObj:
    """The attributes that object_state() and HistoryManager.restore_state() need."""
    ser_attrs = ['solid_geometry', 'obj_options']

    def __init__(self, uid, kind, geometry):
        self.history_uid = uid
        self.kind = kind
        self.solid_geometry = geometry
        self.obj_options = {'name': uid}
        self.plot = Mock()
        self.set_offset_values = Mock()

    def from_dict(self, data):
        self.solid_geometry = data['solid_geometry']
        self.obj_options = data['obj_options']

    def bounds(self):
        return self.solid_geometry.bounds


class OtherStep(HistoryStep):
    label = 'other'
    size = 0

    def __init__(self, uid):
        self._uid = uid

    def uids(self):
        return {self._uid}

    def undo(self, history):
        pass

    def redo(self, history):
        pass


def make_app(objs):
    app = SimpleNamespace(headless=False, call_source='app', inform=Mock(), history_changed=Mock(), log=Mock(),
                          options={}, projects=SimpleNamespace(is_busy=lambda: False), _history_edit=None,
                          app_obj=SimpleNamespace(object_changed=Mock()))
    app.history = HistoryManager(app, collection=SimpleNamespace(get_list=lambda: objs))
    return app


def begin(app, obj):
    App._history_edit_begin(app, obj)


def saved(app, obj):
    App._history_edit_saved(app, obj)


class TestEditorHistory(unittest.TestCase):

    def test_gerber_and_excellon_edit_is_one_step(self):
        # Save updates a Gerber / Excellon / Geometry object in place: 1 Edit step
        for kind in ('geometry', 'gerber', 'excellon'):
            old, new = box(0, 0, 1, 1), box(0, 0, 3, 3)
            obj = FakeObj(kind[:1] + '1', kind, old)
            app = make_app([obj])
            begin(app, obj)
            self.assertIsNotNone(app._history_edit)
            obj.solid_geometry = new
            saved(app, obj)
            self.assertEqual([s.label for s in app.history.undo_stack], ['Edit'], kind)
            self.assertTrue(app.history.undo())
            self.assertEqual(obj.solid_geometry, old)
            self.assertTrue(app.history.redo())
            self.assertEqual(obj.solid_geometry, new)


def test_real_multigeo_geometry_edit_is_one_step_and_undo_gives_the_old_tool_geometry(app):
    # what App.on_editing_finished and update_editor_geometry() do for a Save in place, with a real GeometryObject

    old, new = box(0, 0, 1, 1), box(0, 0, 5, 5)
    obj = GeometryObject('geo', app)
    obj.history_uid = 'g1'
    obj.multigeo = True
    obj.tools = {1: {'tooldia': 1.0, 'data': {'tools_mill_tooldia': 1.0}, 'solid_geometry': [old]}}
    obj.solid_geometry = [old]
    obj.plot = Mock()
    obj.set_offset_values = Mock()
    history_app = make_app([obj])
    begin(history_app, obj)
    obj.tools[1]['solid_geometry'] = [new]
    obj.solid_geometry = [new]
    saved(history_app, obj)
    assert [s.label for s in history_app.history.undo_stack] == ['Edit']
    assert history_app.history.undo()
    assert obj.tools[1]['solid_geometry'] == [old] and obj.solid_geometry == [old]
    assert history_app.history.redo()
    assert obj.tools[1]['solid_geometry'] == [new] and obj.solid_geometry == [new]


if __name__ == '__main__':
    unittest.main()
