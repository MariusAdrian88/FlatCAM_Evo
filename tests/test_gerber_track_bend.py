"""Move Gerber FOLLOW bends without moving their fixed neighbors or changing apertures."""

from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, Point, box

from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from test_gerber_editor_pick import make_editor, event
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402


def track(coords, width=0.4, divisor=2, steps=16):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / divisor, steps)})


def editor_for(shapes, width=0.4):
    editor = make_editor({10: {'type': 'C', 'size': width, 'geometry': shapes, 'tag': 'unchanged'}})
    editor.app.abort_flag = False
    editor.select_tool = AppGerberEditor.select_tool.__get__(editor)
    editor.draw_utility_geometry = lambda geo_shape: None
    return editor


def start(editor, point):
    editor.ui.bend_move_btn.click()
    editor.on_canvas_click(event(*point))
    return editor.active_tool


def release(editor, point):
    editor.on_canvas_click_release(event(*point))


@pytest.mark.parametrize('divisor,steps', [(2, 16)])
def test_internal_bend_stretches_runs_preserves_width_and_fixed_bends(divisor, steps):
    coords = [(0, 0), (2, 0), (4, 0), (4, 2), (4, 4), (8, 4)]
    original = track(coords, divisor=divisor, steps=steps)
    editor = editor_for([original])
    editor.gerber_obj = SimpleNamespace(steps_per_circle=steps * 4)
    editor.selected = [original]
    revision = editor.geometry_revision
    tool = start(editor, (4, 0))
    preview = tool.utility_geometry((5, 1))
    assert preview is not None
    assert original.geo['follow'].coords[:] == coords
    release(editor, (5, 1))
    result = editor.storage_dict[10]['geometry'][0]
    assert result.geo['follow'].coords[:] == [(0, 0), (2.5, 0.5), (5, 1), (4.5, 2.5), (4, 4), (8, 4)]
    assert result.geo['solid'].equals_exact(result.geo['follow'].buffer(0.4 / divisor, steps), 1e-10)
    assert editor.storage_dict[10]['tag'] == 'unchanged'
    assert editor.selected == [result]
    assert editor.geometry_revision == revision + 1
    assert editor.is_modified
    assert float(editor.ui.area_entry.get_value()) == pytest.approx(result.geo['solid'].area, abs=1e-4)
    editor.plot_all.assert_called_once_with()
    assert editor.calls == {'row': 0, 'sel': 0}


@pytest.mark.parametrize('problem', ['branch'])
def test_unsupported_or_ambiguous_targets_never_mutate(problem):
    shape = track([(0, 0), (4, 0), (4, 4)])
    editor = editor_for([shape])
    if problem == 'branch':
        editor.storage_dict[10]['geometry'].append(track([(4, 0), (8, 0)]))
    elif problem == 'different_aperture':
        shape.geo = track([(0, 0), (4, 0)]).geo
        editor.storage_dict[11] = {'type': 'C', 'size': 0.4, 'geometry': [track([(4, 0), (4, 4)])]}
    elif problem == 'noncircular':
        editor.storage_dict[10]['type'] = 'R'
    elif problem == 'clear':
        shape.geo['clear'] = box(1, 1, 2, 2)
    elif problem == 'modified':
        shape.geo['solid'] = shape.geo['solid'].buffer(0.2)
    elif problem == 'region':
        editor.storage_dict[10]['type'] = 'REG'
    elif problem == 'closed':
        shape.geo = track([(0, 0), (4, 0), (4, 4), (0, 0)]).geo
    elif problem == 'endpoint':
        shape.geo = track([(0, 0), (4, 0)]).geo
    else:
        shape.geo = {'follow': Point(4, 0), 'solid': Point(4, 0).buffer(0.2)}
    revision = editor.geometry_revision
    before = shape.geo
    start(editor, (4, 0))
    release(editor, (5, 1))
    assert shape.geo is before
    assert editor.storage_dict[10]['geometry'][0] is shape
    assert editor.geometry_revision == revision
    editor.plot_all.assert_not_called()


@pytest.mark.parametrize('position', [(float('nan'), 1)])
def test_invalid_destination_is_atomic(position):
    shape = track([(0, 0), (4, 0), (4, 4)])
    editor = editor_for([shape])
    revision = editor.geometry_revision
    start(editor, (4, 0))
    release(editor, position)
    assert editor.storage_dict[10]['geometry'] == [shape]
    assert editor.geometry_revision == revision


def test_bend_is_undone_and_redone_by_the_editor_history():
    shape = track([(0, 0), (4, 0), (4, 4)])
    editor = editor_for([shape])
    editor.selected = [shape]
    start(editor, (4, 0))
    release(editor, (5, 1))
    changed = editor.storage_dict[10]['geometry'][0]
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [shape]
    assert not editor.is_modified
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'][0].geo['follow'].equals(changed.geo['follow'])
    assert editor.is_modified


def test_real_editor_save_build_reparses_without_region_conversion(app):

    source = make_gerber(app, NATIVE_GBR, name='board')
    original_tools = source.tools
    shape = track([(0, 0), (4, 0), (4, 4)])
    editor = editor_for([shape])
    start(editor, (4, 0))
    release(editor, (5, 1))
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    saved = result['obj']
    assert saved.tools[10]['size'] == 0.4
    assert not saved.export_as_regions
    assert 'G36*' not in saved.source_file
    assert source.tools is original_tools
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(saved.source_file.splitlines()) is None
    record = parsed.tools[10]['geometry'][0]
    assert record['follow'].coords[:] == [(0, 0), (5, 1), (4, 4)]
    assert record['solid'].hausdorff_distance(editor.storage_dict[10]['geometry'][0].geo['solid']) < 1e-6
