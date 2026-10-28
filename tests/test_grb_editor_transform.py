"""Gerber Editor Transformation plugin actions (rotate, flip, skew, scale, offset, buffer): batch results against the
old per-shape shapely.affinity algorithm, the plot count, the selection and the pick index. The editor is the real
class; the application and the shape collection are stand-ins. Everything is headless."""

import os
import sys
import time
import statistics
import unittest
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6 import QtCore, QtWidgets

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from shapely.affinity import rotate, scale, skew, translate
from shapely.geometry import Point, box

from appEditors import appGerberEditor
from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appEditors.grb_plugins import GrbTransformationPlugin
from appEditors.grb_plugins.GrbTransformationPlugin import TransformEditorTool, queue_transform
from test_gerber_editor_pick import aperture, make_editor, pad, trace

KEYS = ('solid', 'follow', 'clear')


class FakeShapes:
    """Stand-in of the shape collection: records the calls."""

    def __init__(self, transform_ok=True):
        self.last_key = 0
        self.added = []
        self.removed = []
        self.transformed = []
        self.redraws = 0
        self.transform_ok = transform_ok

    def add(self, **kwargs):
        self.last_key += 1
        self.added.append((self.last_key, kwargs))
        return self.last_key

    def remove(self, key, update=False):
        self.removed.append(key)

    def clear(self, update=False):
        pass

    def redraw(self, *args, **kwargs):
        self.redraws += 1

    def transform_keys(self, keys, matrix, update=True):
        self.transformed.append((list(keys), tuple(matrix)))
        return self.transform_ok


class Host:
    """Stand-in of the tool: the path the UI uses (queue_transform -> history job -> result applied on the GUI
    thread), run synchronously on the real editor and a fake app."""

    def __init__(self, draw_app):
        self.app = draw_app.app
        self.draw_app = draw_app
        self.messages = []
        self.app.inform.emit.side_effect = self.messages.append

    def run(self, kind, params):
        if queue_transform(self, kind, params) == 'fail':
            return 'fail'
        job = self.app.worker_task.emit.call_args.args[0]
        job['fcn'](*job['params'])
        QtWidgets.QApplication.processEvents()

    def on_rotate_action(self, val, point):
        return self.run('rotate', [val, point])

    def on_flip(self, axis, point):
        return self.run('flip', [axis, point])

    def on_skew(self, axis, xval, yval, point):
        return self.run('skew', [axis, xval, yval, point])

    def on_scale(self, axis, xfactor, yfactor, point):
        return self.run('scale', [axis, xfactor, yfactor, point])

    def on_offset(self, axis, num):
        return self.run('offset', [axis, num])

    def on_buffer_action(self, value, join, factor=None):
        return self.run('buffer', [value, join] if factor is None else [value, join, factor])


def full_shape(cx, cy):
    geo = {'solid': Point(cx, cy).buffer(0.5, 8), 'follow': Point(cx, cy), 'clear': Point(cx, cy).buffer(0.7, 8)}
    return DrawToolShape(geo)


def build(count=5, real_plot=False, transform_ok=True):
    shapes = [full_shape(i * 2.0, (i % 3) * 1.5) for i in range(count)]
    shapes.append(trace(-3, -1, 4, 2))
    editor = make_editor({10: aperture(shapes[:3]), 11: aperture(shapes[3:])})
    editor.app.options.update({'global_draw_color': '#0000FFFF', 'global_sel_draw_color': '#FF0000FF',
                               'global_theme': 'default'})
    editor.shapes = FakeShapes(transform_ok)
    editor.tolerance = None
    editor.plot_count = 0
    if real_plot:
        real = AppGerberEditor.plot_all.__get__(editor)

        def counted():
            editor.plot_count += 1
            real()
        editor.plot_all = counted
    else:
        def counted():
            editor.plot_count += 1
        editor.plot_all = counted
    editor.selected = list(shapes[:4]) + [shapes[-1]]
    host = Host(editor)
    return host, editor, shapes


def snapshot(shapes):
    return [{k: v for k, v in s.geo.items()} for s in shapes]


def reference(shapes_geo, fn):
    return [{k: fn(v) for k, v in geo.items() if k in KEYS} for geo in shapes_geo]


def same_geo(test, a, b):
    test.assertEqual(a.geom_type, b.geom_type)
    test.assertTrue(np.allclose(np.asarray(a.bounds), np.asarray(b.bounds), atol=1e-9))
    test.assertTrue(np.allclose(np.array(a.exterior.coords if hasattr(a, 'exterior') else a.coords),
                                np.array(b.exterior.coords if hasattr(b, 'exterior') else b.coords), atol=1e-9))


class TransformCase(unittest.TestCase):
    def check(self, action, ref_fn, selected_only=True):
        host, editor, shapes = build()
        before = snapshot(shapes)
        selected = list(editor.selected)
        action(host)
        sel_ids = {id(s) for s in selected}
        for shape, old in zip(shapes, before):
            if id(shape) in sel_ids:
                for key, expected in reference([old], ref_fn)[0].items():
                    same_geo(self, shape.geo[key], expected)
            else:
                for key, value in old.items():
                    self.assertIs(shape.geo[key], value)
        self.assertEqual(editor.plot_count, 1)
        self.assertEqual(editor.selected, selected)
        self.assertEqual(host.messages, [])
        return host, editor, shapes

    def test_rotate(self):
        self.check(lambda h: h.on_rotate_action(33.0, (1.5, -2.0)), lambda g: rotate(g, -33.0, origin=(1.5, -2.0)))

    def test_flip_x_and_y(self):
        self.check(lambda h: h.on_flip('X', (1.0, 2.0)), lambda g: scale(g, 1, -1, origin=(1.0, 2.0)))
        self.check(lambda h: h.on_flip('Y', (1.0, 2.0)), lambda g: scale(g, -1, 1, origin=(1.0, 2.0)))

    def test_skew(self):
        self.check(lambda h: h.on_skew('X', 12.0, 5.0, (0.5, 0.5)), lambda g: skew(g, 12.0, 5.0, origin=(0.5, 0.5)))

    def test_scale(self):
        self.check(lambda h: h.on_scale('X', 1.7, 0.6, (2.0, 1.0)), lambda g: scale(g, 1.7, 0.6, origin=(2.0, 1.0)))

    def test_offset(self):
        self.check(lambda h: h.on_offset('X', 3.25), lambda g: translate(g, 3.25, 0))
        self.check(lambda h: h.on_offset('Y', -1.5), lambda g: translate(g, 0, -1.5))

    def test_buffer_distance(self):
        host, editor, shapes = build()
        before = snapshot(shapes)
        selected = list(editor.selected)
        host.on_buffer_action(0.2, 'round')
        for shape, old in zip(shapes, before):
            if shape in selected:
                same_geo(self, shape.geo['solid'], old['solid'].buffer(0.2, quad_segs=16, join_style='round'))
                self.assertIs(shape.geo['follow'], old['follow'])
                if 'clear' in old:
                    same_geo(self, shape.geo['clear'], old['clear'].buffer(0.2, quad_segs=16, join_style='round'))
        self.assertEqual(editor.plot_count, 1)
        self.assertEqual(editor.selected, selected)
        self.assertEqual(host.messages, [])


    def test_buffer_factor(self):
        host, editor, shapes = build()
        before = snapshot(shapes)
        host.on_buffer_action(1.3, 'round', True)
        for shape, old in zip(shapes, before):
            if shape in editor.selected:
                for key in old:
                    center = old['solid'].bounds
                    origin = ((center[0] + center[2]) / 2, (center[1] + center[3]) / 2)
                    same_geo(self, shape.geo[key], scale(old[key], 1.3, 1.3, origin=origin))
        self.assertEqual(editor.plot_count, 1)

    def test_no_selection(self):
        host, editor, shapes = build()
        editor.selected = []
        host.on_rotate_action(10.0, (0, 0))
        self.assertEqual(editor.plot_count, 0)
        self.assertEqual(len(host.messages), 1)
        self.assertTrue(host.messages[0].startswith('[WARNING_NOTCL]'))


    def test_duplicate_selection_transformed_once(self):
        host, editor, shapes = build()
        editor.selected = [shapes[0], shapes[0]]
        old = shapes[0].geo['solid']
        host.on_offset('X', 1.0)
        same_geo(self, shapes[0].geo['solid'], translate(old, 1.0, 0))


@pytest.mark.parametrize('kind,params', [
    ('skew', ['X', 90, 0, (0, 0)]),
    ('rotate', [float('inf'), (0, 0)]),
])
def test_invalid_transform_preserves_selection_and_history(kind, params):
    host, editor, shapes = build()
    before, selected = snapshot(shapes), list(editor.selected)
    assert queue_transform(host, kind, params) == 'fail'
    assert snapshot(shapes) == before
    assert editor.selected == selected
    assert not editor.history.undo_stack
    assert editor.history.pending is None


def test_scaling_eroded_pad_keeps_empty_solid_and_finite_follow():
    host, editor, shapes = build()
    editor.selected = [shapes[0]]
    host.on_buffer_action(-2, 1)
    host.on_buffer_action(1.2, 1, True)
    assert shapes[0].geo['solid'].is_empty
    assert shapes[0].geo['follow'].equals(Point(0, 0))


@pytest.mark.parametrize('kind,params', [
    ('offset', ['X', sys.float_info.max]),
    ('buffer', [sys.float_info.max, 1, True]),
])
def test_unrepresentable_worker_output_preserves_board_and_history(kind, params):
    host, editor, shapes = build()
    editor.selected = [shapes[-1]]
    host.on_offset('X', 1)
    assert editor.undo()
    before = snapshot(shapes)
    state = editor.history.state()
    revision = editor.geometry_revision
    undo, redo = list(editor.history.undo_stack), list(editor.history.redo_stack)
    host.messages.clear()
    results = []
    editor.geometry_result.disconnect()
    editor.geometry_result.connect(results.append)
    assert queue_transform(host, kind, params) != 'fail'
    job = editor.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()
    # Don't pass corrupt geometry into GEOS history/export checks while reproducing the failure.
    assert 'error' in results[0]
    editor.history.apply_result(results[0])
    assert snapshot(shapes) == before
    assert editor.history.state() == state
    assert editor.geometry_revision == revision
    assert editor.history.undo_stack == undo and editor.history.redo_stack == redo
    assert host.messages and not any(message.startswith('[success]') for message in host.messages)


def test_affine_validates_all_keys_before_assigning_any_result():
    shape = DrawToolShape({'solid': box(0, 0, 1, 1), 'follow': Point(sys.float_info.max, 0)})
    before = dict(shape.geo)
    with pytest.raises(ValueError):
        GrbTransformationPlugin.affine_selection([shape], (2, 0, 0, 2, 0, 0))
    assert shape.geo == before




def transformation_tool(editor):
    from defaults import AppDefaults
    editor.app.defaults = AppDefaults()
    return TransformEditorTool(editor.app, editor)


@pytest.mark.parametrize('outcome', ['failure', 'stale'])
def test_rejected_queued_result_never_reports_success(outcome):
    host, editor, shapes = build()
    tool = transformation_tool(editor)
    before = snapshot(shapes)
    tool.on_rotate(val=20, ref=0)
    job = editor.app.worker_task.emit.call_args.args[0]
    if outcome == 'failure':
        with patch.object(GrbTransformationPlugin, 'affine_selection', side_effect=ValueError('failed')):
            job['fcn'](*job['params'])
    else:
        editor.touch_geometry()
        job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()
    assert snapshot(shapes) == before
    assert not editor.history.undo_stack
    assert host.messages and not any(message.startswith('[success]') for message in host.messages)


@pytest.mark.parametrize('kind', ['scale', 'buffer'])
def test_standalone_tool_finishes_after_commit_without_losing_targets(kind):
    from appEditors.appGerberEditor import ScaleEditorGrb, BufferEditorGrb
    from appEditors.grb_plugins.GrbBufferPlugin import BufferEditorTool
    from defaults import AppDefaults
    host, editor, shapes = build()
    editor.selected = [shapes[0]]
    editor.sync_aperture_rows({10})
    editor.ui.scale_factor_entry.set_value(2)
    if kind == 'scale':
        tool = ScaleEditorGrb(editor)
        action = tool.on_scale_click
    else:
        editor.app.defaults = AppDefaults()
        tool = BufferEditorGrb.__new__(BufferEditorGrb)
        tool.draw_app, tool.app, tool.name = editor, editor.app, 'buffer'
        tool.buff_tool = BufferEditorTool(editor.app, editor)
        tool.buff_tool.ui.buffer_distance_entry.set_value(0.2)
        action = tool.on_buffer_clicked
    editor.active_tool = tool
    editor.shapes.visible = True
    editor.select_tool = AppGerberEditor.select_tool.__get__(editor)
    results = []
    editor.geometry_result.disconnect()
    editor.geometry_result.connect(results.append)
    before = shapes[0].geo['solid']
    action()
    assert shapes[0].geo['solid'] is before
    job = editor.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()
    editor.history.apply_result(results[0])
    if kind == 'scale':
        assert shapes[0].geo['solid'].area == pytest.approx(before.area * 4)
    else:
        assert shapes[0].geo['solid'].area == pytest.approx(before.buffer(0.2, quad_segs=16).area)
    assert editor.active_tool.name == 'select'
    assert not any('Wait for' in message for message in host.messages)


def test_escape_in_transform_tool_clears_the_selection_and_returns_to_select():
    from appEditors.appGerberEditor import TransformEditorGrb
    from appGUI.MainGUI import MainGUI
    host, editor, shapes = build()
    with patch.object(editor.transform_tool, 'run'):
        editor.active_tool = TransformEditorGrb(editor)
    with QtCore.QSignalBlocker(editor.ui.apertures_table):
        editor.ui.apertures_table.selectRow(0)
    editor.app.call_source = 'grb_editor'
    editor.app.grb_editor = editor
    # the key event as the canvas sends it (MainGUI.keyPressEvent through the VisPy key callback)
    gui = SimpleNamespace(app=editor.app, grb_select_btn=SimpleNamespace(isChecked=lambda: False))
    MainGUI.keyPressEvent(gui, SimpleNamespace(key='Escape', native=SimpleNamespace()))
    assert editor.selected == []
    assert editor.ui.apertures_table.selectedItems() == []
    editor.select_tool.assert_called_once_with('select')




if __name__ == '__main__':
    unittest.main()
