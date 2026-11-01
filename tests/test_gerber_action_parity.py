"""Gerber counterparts retain native FOLLOW and material ownership semantics."""

import math
from unittest.mock import MagicMock, call

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from shapely import LineString, Point
from shapely.geometry import box, MultiPolygon
from shapely.ops import unary_union
from camlib import gerber_quad_segs

from appEditors import appGerberEditor as module
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appGUI.MainGUI import MainGUI
from appEditors.grb_plugins.GrbTrackEditPlugin import ClipEditorGrb
from test_gerber_editor_clear import editor_save
from test_gerber_editor_pick import make_editor, aperture, pad, event
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, _headless_qt, make_gerber, NATIVE_GBR, reparse_gerber,
)


class JumpHolder(QtCore.QObject):
    """Stand-in of the app jump signal (no arguments)."""
    signal = QtCore.pyqtSignal()


@pytest.mark.parametrize('same_owner', [True, False])
def test_difference_keeps_cutters_and_order_with_history(same_owner):
    target = DrawToolShape({'solid': MultiPolygon([box(0, 0, 10, 10), box(12, 0, 13, 1)])})
    cutter = DrawToolShape({'solid': box(2, 2, 8, 8)})
    unrelated = pad(20, 20, 1)
    storage = {10: aperture([unrelated, target, cutter] if same_owner else [unrelated, target])}
    if not same_owner:
        storage[11] = aperture([cutter])
    editor = make_editor(storage)
    editor.selected = [target, cutter]
    original = cutter.geo
    result = editor.apply_region_operation('difference_keep')
    assert result != 'fail'
    assert unary_union([s.geo['solid'] for s in result]).equals(target.geo['solid'].difference(cutter.geo['solid']))
    assert sum(len(s.geo['solid'].interiors) for s in result) == 1
    assert editor.storage_dict[10]['geometry'] == ([unrelated, cutter] if same_owner else [unrelated])
    assert editor.storage_dict[10 if same_owner else 11]['geometry'][-1] is cutter
    assert cutter.geo is original
    assert editor.selected == result + [cutter]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.selected == [target, cutter]
    assert editor.storage_dict[10]['geometry'][1] is target
    assert editor.redo()
    assert editor.storage_dict[10 if same_owner else 11]['geometry'][-1] is cutter


def arc_tool(editor):
    editor.last_aperture_selected = 10
    editor.loaded_as_regions = False
    editor.jump_holder = JumpHolder()
    editor.app.jump_signal = editor.jump_holder.signal
    tool = module.ArcEditorGrb(editor)
    editor.active_tool = tool
    return tool


@pytest.mark.parametrize('mode,points', [
    ('c12', [(0, 0), (1, 0), (0, 1)]),
    ('12c', [(1, 0), (0, 1), (0, 0)]),
    ('132', [(1, 0), (0, 1), (math.sqrt(0.5), math.sqrt(0.5))])])
def test_arc_modes_preview_commit_and_history(mode, points):
    editor = make_editor({10: aperture([])})
    tool = arc_tool(editor)
    tool.mode, tool.direction = mode, 'ccw'
    tool.click(points[0])
    tool.click(points[1])
    preview = tool.utility_geometry(points[2]).geo
    tool.click(points[2])
    assert tool.complete
    assert tool.geometry.geo['solid'].equals(preview['solid'])
    follow = tool.geometry.geo['follow']
    assert follow.geom_type == 'LineString' and follow.is_simple
    assert follow.coords[0] == pytest.approx((1, 0))
    assert follow.coords[-1] == pytest.approx((0, 1))
    assert all(math.hypot(x, y) == pytest.approx(1) for x, y in follow.coords)
    assert tool.geometry.geo['solid'].equals(follow.buffer(0.05, quad_segs=gerber_quad_segs(editor.app.options, 0.05, editor.app.app_units)))
    editor.on_grb_shape_complete()
    assert len(editor.storage_dict[10]['geometry']) == 1
    assert not editor.loaded_as_regions
    assert editor.undo() and editor.storage_dict[10]['geometry'] == []
    assert editor.redo() and len(editor.storage_dict[10]['geometry']) == 1
    tool.clean_up()


@pytest.mark.parametrize('mode,kept,bad', [
    ('132', [(0, 0), (2, 0)], (1, 0)),  # Point2 on the line of Point1 and Point3
    ('132', [(0, 0)], (0, 0)),  # Point3 on Point1
    ('12c', [(1, 1)], (1, 1))])  # the two ends of the chord on one point
def test_semidisc_refuses_a_point_without_an_arc_and_takes_the_next_one(mode, kept, bad):
    editor = make_editor({10: aperture([])})
    editor.last_aperture_selected = 10
    tool = module.DiscSemiEditorGrb(editor)
    editor.active_tool = tool
    tool.mode = mode
    for point in kept + [bad]:
        tool.click(point)
    assert tool.points == kept and not tool.complete
    assert 'non-collinear' in editor.app.inform.emit.call_args.args[0]
    for point in [(2, 2), (3, 1)][len(kept) - 1:]:
        tool.click(point)
    assert tool.complete
    assert tool.geometry.geo['solid'].is_valid and not tool.geometry.geo['solid'].is_empty


@pytest.mark.parametrize('operation,steps,direction', [('arc', 16, 'cw'), ('arc', 64, 'ccw'), ('difference_keep', 16, 'cw')])
def test_new_actions_native_export_reparse(app, tmp_path, operation, steps, direction):
    editor = make_editor({10: aperture([])})
    for options in (editor.app.options, app.options):
        options.update(gerber_circle_steps_mode='fixed', gerber_circle_steps=steps)
    if operation == 'arc':
        tool = arc_tool(editor)
        tool.direction = direction
        for point in [(0, 0), (2, 0), (0, 2)]:
            tool.click(point)
        editor.on_grb_shape_complete()
        tool.clean_up()
    else:
        first = DrawToolShape({'solid': box(0, 0, 10, 10)})
        cutter = DrawToolShape({'solid': box(2, 2, 8, 8)})
        editor.storage_dict[10]['geometry'] = [first]
        editor.storage_dict[11] = {'type': 'REG', 'size': 0, 'geometry': [cutter]}
        editor.selected = [first, cutter]
        assert editor.apply_region_operation(operation) != 'fail'
    tools = {key: {**value, 'geometry': [s.geo for s in value['geometry']]}
             for key, value in editor.storage_dict.items() if value['geometry']}
    expected = unary_union([s.geo['solid'] for value in editor.storage_dict.values() for s in value['geometry']])
    edited = editor_save(app, make_gerber(app, NATIVE_GBR), tools, loaded_as_regions=editor.loaded_as_regions)
    again = reparse_gerber(app, edited.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, expected, abs_tol=1e-4)




NEW_MENU_ACTIONS = ['grb_invert_selection', 'grb_select_pads', 'grb_select_tracks', 'grb_select_regions',
                    'grb_select_chain', 'grb_step_repeat', 'grb_transcode', 'grb_snap_grid', 'grb_clip',
                    'grb_clip_pad', 'grb_stretch', 'grb_elbow', 'grb_elbow90', 'grb_delete_unused', 'grb_renumber',
                    'grb_merge_duplicates', 'grb_round_sizes', 'grb_select_same_type', 'grb_pads_to_flashes',
                    'grb_teardrops', 'grb_snowman']


def test_camguide_menu_actions_reach_the_editor():
    editor = make_editor({10: aperture([])})
    for name in NEW_MENU_ACTIONS:
        assert getattr(editor.app.ui, name + '_menuitem').triggered.connect.called, name
    # a menu action runs its operation: Invert Selection selects what was not selected
    first, second = pad(0, 0, 1), pad(5, 0, 1)
    editor.storage_dict[10]['geometry'] = [first, second]
    editor.touch_geometry()
    editor.selected = [first]
    handler = editor.app.ui.grb_invert_selection_menuitem.triggered.connect.call_args.args[0]
    handler()
    assert editor.selected == [second]


def test_clip_tool_drag_on_the_canvas_clips_a_track():
    follow = LineString([(0, 0), (10, 0)])
    shape = DrawToolShape({'follow': follow, 'solid': follow.buffer(0.5, 16)})
    editor = make_editor({10: aperture([shape])})
    editor.loaded_as_regions = False
    editor.active_tool = ClipEditorGrb(editor)
    editor.on_canvas_click(event(4, -2))
    editor.on_canvas_click_release(event(6, 2))
    # Clip cuts the track at the edges of the rectangle: 3 tracks that meet at x = 4 and x = 6
    ends = sorted(tuple(sorted(round(x, 6) for x, _y in s.geo['follow'].coords))
                  for s in editor.storage_dict[10]['geometry'])
    assert ends == [(0.0, 4.0), (4.0, 6.0), (6.0, 10.0)]
    assert len(editor.history.undo_stack) == 1


def test_right_click_exits_stretch_clears_preview_and_keeps_geometry():
    from appEditors.grb_plugins.GrbTrackEditPlugin import StretchEditorGrb
    from test_gerber_editor_pick import RecordingShapes
    follow = LineString([(0, 0), (10, 0)])
    shape = DrawToolShape({'follow': follow, 'solid': follow.buffer(0.5, 16)})
    editor = make_editor({10: aperture([shape])})
    editor.select_tool = module.AppGerberEditor.select_tool.__get__(editor)
    editor.tool_shape = RecordingShapes()
    tool = StretchEditorGrb(editor)
    editor.active_tool = tool
    editor.on_canvas_click(event(10, 0))
    editor.on_canvas_click_release(event(10, 0))
    editor.draw_utility_geometry(tool.utility_geometry((12, 2)))
    assert editor.tool_shape.added
    # A right-button drag is panning, not cancellation.
    editor.on_canvas_click_release(event(12, 2, button=2, dragging=1))
    assert editor.active_tool is tool
    editor.on_canvas_click_release(event(12, 2, button=2))
    assert editor.active_tool.name == 'select'
    assert not editor.tool_shape.added
    assert editor.storage_dict[10]['geometry'] == [shape]
    assert not editor.history.undo_stack
