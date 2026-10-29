"""ToolTransform actions: the matrix given to plot_transformed, bounds computed once, failures isolated."""
import os
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from shapely.geometry import Polygon  # noqa: E402
from shapely.affinity import affine_transform  # noqa: E402
from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtGui import QStandardItemModel  # noqa: E402

import camlib  # noqa: E402
from camlib import Geometry  # noqa: E402
from appPlugins.ToolTransform import ToolTransform  # noqa: E402
from appObjects.AppObject import AppObject  # noqa: E402
from appObjects.GeometryObject import GeometryObject  # noqa: E402
from appHandlers.appHistory import HistoryManager  # noqa: E402
from tests.helpers_objects import make_app_0b  # noqa: E402


def make_app(selected):
    app = MagicMock()
    app.inform.emit = MagicMock()
    app.app_units = "MM"
    app.collection = SimpleNamespace(get_selected=MagicMock(return_value=selected), get_by_name=lambda n: None)

    @contextmanager
    def new(_msg):
        yield

    app.proc_container = SimpleNamespace(new=new, new_text='', update_view_text=lambda *a: None)
    app.app_obj = SimpleNamespace(object_changed=MagicMock(), object_transform_done=MagicMock())
    return app


class FakeObj:
    """A selected object: real camlib.Geometry transforms, counted bounds / plot calls."""

    def __init__(self, app, name='g', kind='geometry', fail=False, fail_value=None):
        self.kind = kind
        self.geo = Geometry(MagicMock())
        self.geo.app = app
        self.geo.multigeo = False
        self.geo.solid_geometry = [Polygon([(0, 0), (4, 0), (4, 2), (1, 3)])]
        self.obj_options = {'name': name}
        self.tools = {}
        self.history_uid = name
        self.ser_attrs = ['solid_geometry', 'obj_options']
        self.bounds_calls = 0
        self.offset_bounds = []
        self.plot_calls = 0
        self.matrices = []
        self.fail = fail
        self.fail_value = fail_value

    @property
    def solid_geometry(self):
        return self.geo.solid_geometry

    @solid_geometry.setter
    def solid_geometry(self, value):
        self.geo.solid_geometry = value

    def from_dict(self, state):
        self.solid_geometry = state['solid_geometry']
        self.obj_options.update(state['obj_options'])

    def _go(self, fcn, *a, **kw):
        if self.fail:
            raise RuntimeError("boom")
        if self.fail_value is not None:
            return self.fail_value
        return fcn(*a, **kw)

    def rotate(self, angle, point):
        return self._go(self.geo.rotate, angle, point)

    def mirror(self, axis, point):
        return self._go(self.geo.mirror, axis, point)

    def skew(self, ax, ay, point):
        return self._go(self.geo.skew, ax, ay, point)

    def scale(self, xf, yf=None, point=None):
        return self._go(self._scale, xf, yf, point)

    def _scale(self, xf, yf, point):
        px, py = (0, 0) if point is None else point
        m = camlib.affine_scale_matrix(xf, yf, (px, py))
        self.geo.solid_geometry = [affine_transform(g, m) for g in self.geo.solid_geometry]

    def offset(self, vect):
        return self._go(self._offset, vect)

    def _offset(self, vect):
        m = camlib.affine_translate_matrix(*vect)
        self.geo.solid_geometry = [affine_transform(g, m) for g in self.geo.solid_geometry]

    def buffer(self, value, join, factor=None):
        self.geo.solid_geometry = [g.buffer(value) for g in self.geo.solid_geometry]

    def bounds(self):
        self.bounds_calls += 1
        return self.geo.solid_geometry[0].bounds

    def set_offset_values(self, bounds=None):
        if bounds is None:
            bounds = self.bounds()
        self.offset_bounds.append(bounds)

    def plot_transformed(self, matrix, kind=None):
        self.matrices.append(tuple(matrix))
        return True

    def plot(self, kind=None):
        self.plot_calls += 1


def reference(poly, matrix):
    return affine_transform(poly, matrix)


class SelectedCollection(QStandardItemModel):
    """The Qt collection model plus the selection API the tool reads on the GUI thread."""

    def __init__(self, objects=()):
        super().__init__()
        self.objects = list(objects)

    def get_selected(self):
        return list(self.objects)

    def get_list(self):
        return list(self.objects)

    def get_by_name(self, name):
        for obj in self.objects:
            if obj.obj_options.get('name') == name:
                return obj
        return None


class Worker:
    """Synchronous stand-in for app.worker_task; ``run=False`` keeps the payload pending like a real worker."""

    def __init__(self, run=True):
        self.run = run
        self.payloads = []

    def emit(self, payload):
        self.payloads.append(payload)
        if self.run:
            payload['fcn'](*payload['params'])


class TransformTest(unittest.TestCase):

    def setUp(self):
        self.objs = []

    def tool(self, objs):
        app = make_app(objs)
        for o in objs:
            o.geo.app = app
        return SimpleNamespace(app=app), app

    def run_action(self, name, objs, *args):
        t, app = self.tool(objs)
        getattr(ToolTransform, name)(t, *args)
        return app

    def check_matrix(self, obj, original):
        self.assertEqual(len(obj.matrices), 1)
        got = obj.geo.solid_geometry[0]
        exp = reference(original, obj.matrices[0])
        self.assertTrue(got.equals_exact(exp, 1e-9), (got.wkt, exp.wkt))
        self.assertEqual(obj.plot_calls, 0)

    def test_matrix_matches_each_action(self):
        original = Polygon([(0, 0), (4, 0), (4, 2), (1, 3)])
        cases = [
            ('on_rotate_action', (30.0, (1.0, 2.0))),
            ('on_flip', ('X', (1.0, 2.0))),
            ('on_flip', ('Y', (1.0, 2.0))),
            ('on_skew', ('X', 10.0, 5.0, (1.0, 2.0))),
            ('on_scale', ('X', 2.0, 3.0, (1.0, 2.0))),
            ('on_scale', ('X', 2.0, 3.0, None)),
            ('on_skew', ('XY', 10.0, 5.0, (1.0, 2.0))),
            ('on_offset', ('X', 5.0)),
            ('on_offset', ('Y', -3.0)),
            ('on_offsetxy_action', (5.0, -3.0)),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                o = FakeObj(None)
                self.run_action(name, [o], *args)
                self.check_matrix(o, original)

    def test_local_bounds_and_notification_compute_fresh_bounds(self):
        objs = [FakeObj(None, 'a'), FakeObj(None, 'b')]
        tool, app = self.tool(objs)
        app.app_obj.object_changed.emit.side_effect = lambda obj: AppObject.on_object_changed(tool, obj)
        app.app_obj.object_transform_done.emit.side_effect = \
            lambda obj, set_offset: AppObject.on_object_transform_done(tool, obj, set_offset)
        ToolTransform.on_rotate_action(tool, 30.0, (0.0, 0.0))
        expected = {o: o.geo.solid_geometry[0].bounds for o in objs}
        for o in objs:
            # the bounds are computed once by on_object_changed and reused by the transform completion
            self.assertEqual(o.bounds_calls, 1)
            self.assertEqual(o.offset_bounds, [expected[o]])
        self.assertEqual(app.app_obj.object_changed.emit.call_count, 2)
        # one final message
        # the plugin emits one final message (the camlib methods emit their own per object)
        msgs = [c.args[0] for c in app.inform.emit.call_args_list]
        self.assertEqual(sum("Rotate done" in m for m in msgs), 1)
        self.assertIn("Rotate done", msgs[-1])

    def test_mutation_then_failure_rolls_back_and_continues(self):
        for failure in ('fail', 'exception', 'metadata', 'plot'):
            with self.subTest(failure=failure):
                bad, good = FakeObj(None, 'bad'), FakeObj(None, 'good')
                original = bad.solid_geometry[0]
                tool, app = self.tool([bad, good])

                def apply(obj):
                    obj.offset((2, 0))
                    if obj is bad:
                        if failure == 'fail':
                            return 'fail'
                        if failure == 'exception':
                            raise RuntimeError('after mutation')

                def meta(obj):
                    obj.obj_options['offset_x'] = 2
                    if obj is bad and failure == 'metadata':
                        raise RuntimeError('metadata')

                if failure == 'plot':
                    bad.plot_transformed = MagicMock(side_effect=RuntimeError('plot'))
                ToolTransform._transform_selected(tool, 'Move', '', apply, (1, 0, 0, 1, 2, 0), meta, 'done')
                self.assertTrue(bad.solid_geometry[0].equals_exact(original, 0))
                self.assertNotIn('offset_x', bad.obj_options)
                self.assertFalse(good.solid_geometry[0].equals_exact(original, 0))

    def test_no_selection(self):
        app = self.run_action('on_offset', [], 'X', 2.0)
        self.assertIn('WARNING_NOTCL', app.inform.emit.call_args.args[0])


    def test_singular_skew_and_uninvertible_matrices_change_nothing(self):
        cases = [
            ('on_skew', ('X', -90.0, 0.0, (0.0, 0.0))),
            ('on_skew', ('X', 270.0, 0.0, (0.0, 0.0))),
            ('on_skew', ('X', 45.0, 45.0, (0.0, 0.0))),
            ('on_scale', ('X', 1e-13, 1.0, (0.0, 0.0))),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                obj = FakeObj(None)
                original = obj.solid_geometry[0]
                tool, app = self.tool([obj])
                getattr(ToolTransform, name)(tool, *args)
                self.assertTrue(obj.solid_geometry[0].equals_exact(original, 0))
                self.assertEqual(obj.matrices, [])
                self.assertTrue(any('not executed' in call.args[0] or 'NOTCL' in call.args[0]
                                    for call in app.inform.emit.call_args_list))

    def test_offset_of_an_opened_gcode_job_moves_its_exported_text(self):
        """Offset through ToolTransform: the exported text (source_file) of a G-code job holds the moved coordinates."""
        import pathlib
        import tempfile
        from tests.test_cncjob_export_gcode import MILL_FILE, _open_gcode

        with tempfile.TemporaryDirectory() as tmp:
            _job_app, job = _open_gcode(pathlib.Path(tmp), MILL_FILE)
        self.assertIn('G0 X1 Y1', job.source_file)
        app = make_app([job])
        ToolTransform.on_offset(SimpleNamespace(app=app), 'X', 10.0)

        self.assertIn('X11', job.source_file)
        self.assertNotIn('X1 Y1', job.source_file)


class TransformUiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_real_app(self):
        app = make_app_0b()
        app.resource_location = ''
        app.collection = SelectedCollection()
        app.delete_selection_shape = MagicMock()
        app.should_we_save = False
        app.headless = False
        app.call_source = 'app'
        app.projects = SimpleNamespace(is_busy=lambda: False)
        app.history_changed = MagicMock()
        app.on_plots_updated = MagicMock()
        app.dec_format = lambda value, dec=None: float('%.*f' % (dec if dec is not None else app.decimals,
                                                                  float(value)))
        app.worker_task = Worker(run=True)
        app.app_obj = AppObject(app)
        history = HistoryManager(app, app.collection)
        app.history = history
        return app, history

    def make_tool_for(self, app):
        tool = ToolTransform(app)
        tool.show()
        return tool

    def make_real_tool(self, objects, worker=None):
        app, history = self.make_real_app()
        app.collection.objects = list(objects)
        app.worker_task = worker or Worker(run=True)
        return self.make_tool_for(app), app, history


    def test_combined_scale_uses_both_entries_and_is_one_undo_step(self):
        branches = (
            (False, (0.0, 0.0, 8.0, 9.0)),   # X factor 2, Y factor 3
            (True, (0.0, 0.0, 8.0, 6.0)),    # linked: the X factor 2 is used for both
        )
        for linked, expected in branches:
            with self.subTest(linked=linked), patch('appObjects.AppObjectTemplate.ShapeCollection', MagicMock()):
                app, history = self.make_real_app()
                obj = GeometryObject('real', app)
                obj.solid_geometry = [Polygon([(0, 0), (4, 0), (4, 2), (1, 3)])]
                obj.plot_transformed = MagicMock(return_value=True)
                original = obj.bounds()
                callback = obj.obj_options.callback
                app.collection.objects = [obj]
                tool = self.make_tool_for(app)

                tool.ui.ref_combo.set_value('Origin')
                tool.ui.scalex_entry.set_value(2)
                tool.ui.scaley_entry.set_value(3)
                tool.ui.scale_link_cb.set_value(linked)

                ToolTransform.on_scalexy(tool)

                self.assertEqual(obj.bounds(), expected)
                self.assertTrue(history.can_undo())
                self.assertTrue(history.undo())
                self.assertEqual(obj.bounds(), original)
                # exactly one step: a second undo has nothing to do
                self.assertFalse(history.undo())
                self.assertTrue(history.redo())
                self.assertEqual(obj.bounds(), expected)
                self.assertEqual(obj.obj_options.callback, callback)


if __name__ == '__main__':
    unittest.main()
