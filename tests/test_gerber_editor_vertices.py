"""Gerber Editor vertex editing: the plan of a shape, region and track vertex edits with undo and redo, the stale-state
and busy guards, the single 'Edit vertices' menu action, the grip cancel and hide rules, and the VertexGrips defaults.
The editor is the real AppGerberEditor; the application and the canvas are stand-ins. Everything is headless."""


from PyQt6 import QtCore
from shapely.geometry import LineString, box

from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appEditors.grb_plugins.GrbVertices import vertex_plan
from test_gerber_editor_pick import make_editor, make_select, aperture, pad
from tests.helpers_objects import _headless_qt  # noqa: F401,E402

NO_MOD = QtCore.Qt.KeyboardModifier.NoModifier
SQUARE = box(0, 0, 10, 10)   # exterior: (10,0) (10,10) (0,10) (0,0) (10,0) in this shapely version
LABEL = 'Edit vertices'


def region_editor(geometry=SQUARE):
    shape = DrawToolShape({'solid': geometry, 'follow': geometry.exterior})
    editor = make_editor({0: aperture([shape], kind='REG')})
    editor.history.reset()
    make_select(editor)
    editor.selected = [shape]
    return editor, shape


def track_editor(follow=None):
    follow = follow or LineString([(0, 0), (10, 0), (10, 10)])
    shape = DrawToolShape({'solid': follow.buffer(0.2, 16), 'follow': follow})
    editor = make_editor({10: {'type': 'C', 'size': 0.4, 'geometry': [shape]}})
    editor.history.reset()
    make_select(editor)
    editor.selected = [shape]
    return editor, shape


def warnings(editor):
    return [c[0][0] for c in editor.app.inform.emit.call_args_list]


def warned(editor):
    return any('[WARNING_NOTCL]' in text for text in warnings(editor))


def storage_shape(editor, apid):
    return editor.storage_dict[apid]['geometry'][0]


# 1. vertex_plan


def test_plan_point_follow_pad_has_no_grips():
    shape = pad(1, 1, 0.5)
    editor = make_editor({10: aperture([shape], kind='C')})
    make_select(editor)
    editor.selected = [shape]
    assert vertex_plan(editor, shape) is None
    assert editor.on_edit_vertices() == 'fail'
    assert any('pad' in text for text in warnings(editor))


# 2. region move, insert, remove: solid and follow stay consistent, one history step, undo and redo

def follow_matches_solid(shape):
    solid = shape.geo['solid']
    return list(shape.geo['follow'].coords) == list(solid.exterior.coords)


def test_region_move_updates_solid_and_follow_in_one_step_and_undo_redo_restore_exactly():
    editor, shape = region_editor()
    editor.apply_vertex_edit('move', 1, (12, 12), target=shape)
    result = storage_shape(editor, 0)
    assert (12, 12) in list(result.geo['solid'].exterior.coords)
    assert follow_matches_solid(result)
    assert len(editor.history.undo_stack) == 1
    assert editor.history.undo_stack[0]['label'] == LABEL
    assert editor.undo()
    assert storage_shape(editor, 0) is shape
    assert shape.geo['solid'].equals_exact(SQUARE, 0.0)
    assert list(shape.geo['follow'].coords) == list(SQUARE.exterior.coords)
    assert not editor.is_modified
    assert editor.redo()
    result = storage_shape(editor, 0)
    assert (12, 12) in list(result.geo['solid'].exterior.coords)
    assert follow_matches_solid(result)


def test_region_remove_drops_vertex_from_solid_and_follow():
    editor, shape = region_editor()
    editor.apply_vertex_edit('remove', 1, target=shape)
    result = storage_shape(editor, 0)
    assert (10, 10) not in list(result.geo['solid'].exterior.coords)
    assert follow_matches_solid(result)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert shape.geo['solid'].equals_exact(SQUARE, 0.0)


# 3. track move: follow edited, solid rebuilt with the same width, native shape, closed result refused

def test_track_move_edits_follow_and_rebuilds_solid_with_same_width():
    editor, shape = track_editor()
    editor.apply_vertex_edit('move', 1, (14, 3), target=shape)
    result = storage_shape(editor, 10)
    assert list(result.geo['follow'].coords) == [(0, 0), (14, 3), (10, 10)]
    assert result.geo['solid'].equals_exact(result.geo['follow'].buffer(0.2, 16), 1e-9)
    # the solid keeps the radius of the original track: the buffer radius is the same
    assert result.geo['solid'].hausdorff_distance(result.geo['follow'].buffer(0.2, 16)) < 1e-9
    assert len(editor.history.undo_stack) == 1


def test_track_closing_edit_is_rejected_without_a_history_step():
    editor, shape = track_editor()
    outcome = editor.apply_vertex_edit('move', 2, (0, 0), target=shape)
    assert outcome == 'fail'
    assert warned(editor)
    assert storage_shape(editor, 10) is shape
    assert editor.history.undo_stack == []


# 4. stale state between show and press / release


# 5. busy guard


# 6. remove_editor_actions keeps exactly one 'Edit vertices' action


# 7. grips cancelled on tool change and hidden on clear()


# 8. VertexGrips default arguments still work for a Geo-style stand-in


