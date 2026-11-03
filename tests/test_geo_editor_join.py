"""Join uses explicit straight bridges, never endpoint welding or global snapping."""
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString

from appEditors import appGeoEditor as geo
from appGUI.GUIElements import FCTree
from tests.test_geo_editor_split import qt_application, interactive_editor
from tests.test_geo_editor_history import run_worker


def join_editor(paths):
    editor, first = interactive_editor()
    editor.storage.remove(first)
    shapes = [geo.DrawToolShape(LineString(coords)) for coords in paths]
    for shape in shapes:
        shape.data['custom'] = {'label': 'track'}
        shape.data['tool'] = 7
        editor.storage.insert(shape)
    editor.selected = list(shapes)
    editor.multigeo_tool = 7
    editor.plot_changes()
    editor.apply_path_join = lambda tolerance: geo.AppGeoEditor.apply_path_join(editor, tolerance)
    return editor, shapes


@pytest.mark.parametrize('reverse', [True])
@pytest.mark.parametrize('gap', [0.05])
def test_exact_and_near_preserve_vertices_length_metadata_and_replay(reverse, gap):
    a, b = [(0, 0), (1, 1), (2, 0)], [(2 + gap, 0), (3, 1), (4, 0)]
    editor, originals = join_editor([a, b[::-1] if reverse else b])
    editor._snap_cache = object()
    assert editor.apply_path_join(0.125) != 'fail'
    result, = editor.selected
    assert result.geo.is_valid and result.geo.is_simple
    assert set(a + b) <= set(result.geo.coords)
    assert result.geo.length == pytest.approx(sum(s.geo.length for s in originals) + gap)
    assert result.data['custom'] == originals[0].data['custom']
    assert result.data['tool'] == editor.multigeo_tool == 7
    assert result.data['bounds'] == result.geo.bounds
    assert editor._snap_cache is None
    assert set(editor.storage.indexes) == {id(result)}
    assert editor.storage.nearest((0, 0))[1] is result
    assert editor.sel_shapes.items == [result.geo]
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.selected == originals
    assert editor.sel_shapes.items == [s.geo for s in originals]
    assert editor.redo()
    assert editor.selected == [result]
    assert editor.sel_shapes.items == [result.geo]


@pytest.mark.parametrize('paths,tolerance', [
    ([[(0, 0), (1, 0)], [(1.101, 0), (2, 0)]], 0.1),
    ([[(0, 0), (1, 0)], [(1, 0), (2, 0)], [(1, 0), (1, 1)]], 0),
])
def test_reject_gap_branching_unsupported_and_crossing_without_mutation(paths, tolerance):
    editor, originals = join_editor(paths)
    cache = editor._snap_cache = object()
    assert editor.apply_path_join(tolerance) == 'fail'
    assert editor.selected == originals
    assert set(editor.storage.get_objects()) == set(originals)
    assert editor._snap_cache is cache
    assert not editor.undo()
    assert editor.app.inform.emit.called


def test_closed_output_and_disjoint_component_stay_distinguishable():
    editor, originals = join_editor([
        [(0, 0), (2, 0), (2, 2)], [(2, 2), (0, 2), (0, 0)],
        [(10, 0), (11, 0)], [(11, 0), (12, 0)], [(20, 0), (21, 0)]])
    assert editor.apply_path_join(0) != 'fail'
    assert len(editor.selected) == 3
    assert sum(s.geo.is_closed for s in editor.selected) == 1
    assert originals[-1] in editor.selected
    assert all(s.geo.is_valid for s in editor.selected)
    assert editor.undo()
    assert editor.selected == originals


@pytest.mark.parametrize('key,value', [('tool', 8)])
def test_incompatible_metadata_or_owner_is_not_silently_lost(key, value):
    editor, originals = join_editor([[(0, 0), (1, 0)], [(1, 0), (2, 0)]])
    originals[1].data[key] = value
    assert editor.apply_path_join(0) == 'fail'
    assert editor.selected == originals
    assert not editor.undo()


