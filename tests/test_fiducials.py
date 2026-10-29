"""Tests of the Fiducials plugin: the copper geometry, the exported Gerber and the Undo / Redo of every action."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely import Point, unary_union

from appHandlers.appHistory import HistoryManager, history_of
from appHandlers.appIO import appIO
from appObjects.GerberObject import GerberObject
from appPlugins.ToolFiducials.Fiducials import ToolFiducials
from appPlugins.ToolFiducials.FiducialsGen import auto_points, fiducial_geometry
from tests.helpers_objects import (  # noqa: F401
    _headless_qt, assert_poly_equiv, export_gerber_text, make_app, make_gerber, reparse_gerber)

POINTS = [(10.0, 10.0), (30.0, 20.0)]
FIXED_64 = {'gerber_circle_steps_mode': 'fixed', 'gerber_circle_steps': 64}


@pytest.mark.parametrize("fid_type", [1], ids=['cross'])
def test_exported_gerber_matches_solid_geometry(fid_type, tmp_path):
    app = make_app()
    app.f_handlers = appIO(app)
    obj = make_gerber(app)
    tools, solid, geo_list = fiducial_geometry(POINTS, {}, [], 2.0, fid_type, 0.2, FIXED_64, 4)
    obj.tools, obj.solid_geometry, obj.follow_geometry = tools, solid, geo_list

    again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
    assert_poly_equiv(again.solid_geometry, solid, rel=0.02, msg='fid_type %d' % fid_type)


def test_auto_points_grow_by_margin():
    assert auto_points((0, 0, 10, 10), 2.0, 'up', 4) == [(-2, -2), (12, 12), (-2, 12)]
    assert auto_points((0, 0, 10, 10), 2.0, 'down', 4) == [(-2, -2), (12, 12), (12, -2)]
    assert auto_points((0, 0, 10, 10), 2.0, 'no', 4) == [(-2, -2), (12, 12)]


# ---------------------------------------------------------------------------------------------------------------
# the tool with a fake app: a list collection, a real HistoryManager and a new_object() that records the create step
# ---------------------------------------------------------------------------------------------------------------

class FakeCollection:
    def __init__(self):
        self.items = []

    def append(self, obj):
        self.items.append(obj)

    def get_list(self):
        return list(self.items)

    def index(self, row, column, parent=None):
        return SimpleNamespace(internalPointer=lambda: SimpleNamespace(obj=self.items[row]))


class Entry:
    def __init__(self, value=None):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


@pytest.fixture
def setup():
    app = make_app()
    app.f_handlers = appIO(app)
    app.collection = FakeCollection()
    app.headless = False
    app.projects = SimpleNamespace(is_busy=lambda: False)
    app.history_changed = Mock()
    app.history = HistoryManager(app, app.collection)
    app.object_ops = SimpleNamespace(
        delete_objects_now=lambda objs: [app.collection.items.remove(o) for o in objs])
    app.event_is_dragging = False
    app.grid_status = lambda: False
    app.plotcanvas = Mock()
    app.plotcanvas.translate_coords = lambda pos: pos
    app.sel_shapes = Mock()
    app.on_cursor_type = Mock()
    app.on_mouse_click_over_plot = app.on_mouse_click_release_over_plot = Mock()
    app.delete_selection_shape = Mock()
    app.app_cursor = Mock()
    app.created = []

    def new_object(kind, name, initialize, plot=True, autoselected=True, callback=None, callback_params=None,
                   record_history=True):
        obj = GerberObject(name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        history = history_of(app)
        app.collection.append(obj)
        if record_history and history.is_recording():
            history.record_create('New: %s' % name, [obj])
        app.created.append(obj)
        return obj

    app.app_obj.new_object = new_object

    source = make_gerber(app, name='src')
    app.collection.append(source)

    tool = ToolFiducials.__new__(ToolFiducials)
    tool.app = app
    tool.decimals = 4
    tool.grb_steps_per_circle = 64
    tool.cursor_color_memory = None
    tool.old_cursor_type = None
    tool.click_points = []
    tool.fiducial_points = []
    tool.handlers_connected = False
    tool.temp_shapes = app.sel_shapes
    tool.grb_object = tool.sm_object = None
    tool.ui = SimpleNamespace(
        fid_size_entry=Entry(2.0), line_thickness_entry=Entry(0.2), margin_entry=Entry(1.0),
        mode_radio=Entry('auto'), pos_radio=Entry('up'), fid_type_combo=Entry(1), big_cursor_cb=Entry(False),
        bottom_left_coords_entry=Entry(), top_right_coords_entry=Entry(), sec_points_coords_entry=Entry(),
        grb_object_combo=SimpleNamespace(currentIndex=lambda: 0, rootModelIndex=lambda: None))
    return tool, app, source


def names(app):
    return [o.obj_options['name'] for o in app.collection.items]


def click(tool, x, y, button=1):
    tool.on_mouse_release(SimpleNamespace(pos=(x, y), button=button))


def assert_undo_redo_of_fid(app, expected_solid):
    """The action is 1 Undo step and 1 Redo step; Redo brings back the geometry, the apertures and the source."""
    assert app.call_source == 'app'
    assert 'src_fid' in names(app)
    assert app.history.undo()
    assert names(app) == ['src']
    assert app.history.redo()
    fid = app.collection.items[-1]
    assert fid.obj_options['name'] == 'src_fid'
    assert_poly_equiv(fid.solid_geometry, expected_solid, msg='after Redo')
    assert fid.tools and fid.source_file


def test_auto_add_is_one_undo_step(setup):
    tool, app, source = setup
    tool.add_fiducials()
    expected = app.created[0].solid_geometry
    assert not expected.is_empty
    assert_undo_redo_of_fid(app, expected)
    assert not app.history.can_redo()
    app.on_cursor_type.assert_not_called()


def test_manual_add_is_one_undo_step_and_ends_clean(setup):
    tool, app, source = setup
    tool.ui.mode_radio.value = 'manual'
    app.options['global_cursor_type'] = 'small'
    tool.add_fiducials()
    assert app.call_source == 'fiducials_tool'

    click(tool, 0.0, 0.0)
    click(tool, 50.0, 40.0)
    shapes_before_last = app.sel_shapes.add.call_count
    click(tool, 0.0, 40.0)

    # no preview of the last click stays on the canvas, the cursor is restored
    assert app.sel_shapes.add.call_count == shapes_before_last
    app.sel_shapes.clear.assert_called()
    assert app.on_cursor_type.call_args.kwargs['val'] == 'small'
    assert not tool.handlers_connected
    assert_undo_redo_of_fid(app, app.created[0].solid_geometry)


def test_soldermask_opening_sits_on_the_fiducials_and_is_one_undo_step(setup):
    tool, app, source = setup
    tool.ui.fid_type_combo.value = 0
    tool.add_fiducials()
    app.history.undo()
    app.created.clear()

    # the soldermask object has other bounds; the openings still go over the copper fiducials
    mask = make_gerber(app, name='mask')
    mask.solid_geometry = unary_union(mask.solid_geometry).buffer(3.0)
    tool.selected_gerber = lambda: mask
    tool.add_soldermask_opening()
    opening = app.created[-1].solid_geometry
    for pt in auto_points(source.bounds(), 1.0, 'up', 4):
        assert opening.contains(Point(pt).buffer(1.9))
    app.history.undo()
    app.history.redo()
    assert app.collection.items[-1].obj_options['name'] == 'mask_fid'
    assert_poly_equiv(app.collection.items[-1].solid_geometry, opening, msg='after Redo')

