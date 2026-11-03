"""Characterization of the 'app' snapping branch of ``ToolDistance.Distance.snap_handler``: the center of the Gerber
pad (flash) or of the Excellon drill / slot under the cursor. It must pass before and after the move to
``feature_access.iter_features``.
"""

from unittest.mock import MagicMock, Mock
from types import MethodType, SimpleNamespace

import pytest

from appPlugins.ToolDistance import Distance
from shapely import Point, MultiLineString, box

from tests.helpers_objects import app, excellon, gerber, _headless_qt, make_excellon, make_gerber  # noqa: F401,E402


def snap(app_objects, pos):
    """Run the unbound snap_handler with a fake tool; return (returned position, jump location or None)."""
    fake_app = MagicMock()
    fake_app.collection.get_list.return_value = app_objects
    fake_app.options = {}
    fake_app.defaults = {}
    fake_tool = Mock()
    fake_tool.original_call_source = 'app'
    fake_tool.app = fake_app
    fake_tool._snap_cache = {}
    fake_tool.resolve_snap = MethodType(Distance.resolve_snap, fake_tool)
    fake_tool.update_cursor = MethodType(Distance.update_cursor, fake_tool)
    fake_tool.ui.big_cursor_cb.get_value.return_value = False
    result = Distance.snap_handler(fake_tool, pos)
    jumps = fake_app.on_jump_to.call_args_list
    assert len(jumps) <= 1
    return result, (jumps[0].kwargs['custom_location'] if jumps else None)


@pytest.fixture
def objs(gerber, excellon):
    return [gerber, excellon]


class TestGerberPads:
    @pytest.mark.parametrize('click, center', [
        ((5.2, 5.1), (5.0, 5.0)),         # C flash
    ])
    def test_click_inside_a_pad_snaps_to_its_center(self, objs, click, center):
        result, jump = snap([objs[0]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

class TestExcellon:
    @pytest.mark.parametrize('click, center', [
        ((5.1, 12.1), (5.0, 12.0)),       # T2 drill
    ])
    def test_click_inside_a_drill(self, objs, click, center):
        result, jump = snap([objs[1]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

    def test_click_inside_a_slot_snaps_to_the_slot_centroid(self, objs):
        result, jump = snap([objs[1]], (24.0, 12.2))      # T2 slot (20,12)-(28,12)
        assert result == pytest.approx((24.0, 12.0))
        assert jump == pytest.approx((24.0, 12.0))

@pytest.fixture
def measuring(_headless_qt):
    from PyQt6 import QtCore
    from vispy.gloo.context import FakeCanvas
    from appGUI.VisPyVisuals import ShapeCollectionVisual

    class ObjectSignals(QtCore.QObject):
        object_changed = QtCore.pyqtSignal(object)

    canvas = FakeCanvas()
    fake_app = MagicMock()
    fake_app.decimals = 4
    fake_app.app_units = 'MM'
    fake_app.options = {}
    fake_app.defaults = {}
    fake_app.call_source = 'app'
    fake_app.dec_format.side_effect = lambda value, decimals: round(value, decimals)
    fake_app.plotcanvas.new_shape_collection.return_value = ShapeCollectionVisual(pool=None)
    fake_app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    fake_app.plotcanvas.cursor_color = '#FF0000FF'
    fake_app.collection.get_list.return_value = []
    fake_app.grid_status.return_value = False
    fake_app.ui.grid_snap_btn.isChecked.return_value = False
    fake_app.app_obj = ObjectSignals()
    tool = Distance(fake_app)
    tool.init_plugin()
    tool.active = True
    tool.ui_connect()
    fake_app.call_source = 'measurement'
    yield tool
    tool.on_exit()
    tool.deleteLater()
    del canvas


def release(tool, pos=(0, 0), button=1, dragging=False):
    tool.on_mouse_click_release(SimpleNamespace(pos=pos, button=button, is_dragging=dragging))


def move(tool, pos):
    tool.on_mouse_move(SimpleNamespace(pos=pos, is_dragging=False))
    tool.app.log.error.assert_not_called()


def flash(center=(0, 0), solid=None):
    center = Point(center)
    solid = center.buffer(1) if solid is None else solid
    return SimpleNamespace(kind='gerber', obj_options={'plot': True}, solid_geometry=[solid],
                           tools={1: {'geometry': [{'solid': solid, 'follow': center}]}})


def test_app_snap_respects_boundary_visibility_and_composed_copper(measuring):
    obj = flash(solid=box(-1, -1, 1, 1))
    measuring.app.collection.get_list.return_value = [obj]
    assert measuring.snap_handler((1, 0)) == (0, 0)
    obj.obj_options['plot'] = False
    assert measuring.snap_handler((.2, .2)) == (.2, .2)
    obj.obj_options['plot'] = True
    obj.solid_geometry = [box(-1, -1, 0, 1)]
    assert measuring.snap_handler((.2, .2)) == (.2, .2)
    assert measuring.snap_handler((-.2, .2)) == (0, 0)
    obj.solid_geometry = []
    assert measuring.snap_handler((-.2, .2)) == (-.2, .2)


def test_pending_gerber_transform_updates_measurement_snap_and_history(measuring, _headless_qt):
    from concurrent.futures import ThreadPoolExecutor
    from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
    from test_gerber_editor_pick import make_editor, aperture, pad

    editor = make_editor({10: aperture([pad(0, 0, 1)])})
    editor.selected = list(editor.storage_dict[10]['geometry'])
    # Queue an ordinary editor transform before starting measurement. Its GUI-thread
    # result can arrive after Distance has disconnected the editor's mouse handlers.
    measuring.on_exit()
    assert queue_transform(SimpleNamespace(app=editor.app, draw_app=editor), 'offset', ('X', 5)) != 'fail'
    job = editor.app.worker_task.emit.call_args.args[0]
    measuring.app.grb_editor = editor
    measuring.app.call_source = 'grb_editor'
    measuring.app.ui.notebook.count.return_value = 0
    measuring.on_start_measuring()
    assert measuring.resolve_snap((.2, .2)) == (0, 0)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(job['fcn'], *job['params']).result()
    _headless_qt.processEvents()
    assert measuring.resolve_snap((.2, .2)) == (.2, .2)
    assert measuring.resolve_snap((5.2, .2)) == pytest.approx((5, 0))
    assert editor.undo()
    assert measuring.resolve_snap((.2, .2)) == (0, 0)
    assert measuring.resolve_snap((5.2, .2)) == (5.2, .2)
    assert editor.redo()
    assert measuring.resolve_snap((5.2, .2)) == pytest.approx((5, 0))
    measuring.on_exit()
    assert editor.undo()
    measuring.on_start_measuring()
    assert measuring.resolve_snap((.2, .2)) == (0, 0)
    assert measuring.resolve_snap((5.2, .2)) == (5.2, .2)


