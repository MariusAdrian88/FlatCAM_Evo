"""Gerber Editor automatic tools: drawn pads to flashes, teardrops and snowman fillets."""
import math

import pytest
from shapely import LineString, Point, Polygon, box
from shapely.ops import unary_union

from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins import GrbAutoPlugin as module
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from test_gerber_editor_pick import make_editor
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402


def track(coords, width=0.4):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / 2, 16)})


def flash(x, y=0, diameter=2):
    follow = Point(x, y)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(diameter / 2, 64)})


def region(polygon):
    return DrawToolShape({'solid': polygon, 'follow': polygon.exterior})


def editor_for(storage):
    editor = make_editor(storage)
    editor.loaded_as_regions = False
    editor.selected = []
    return editor


def footprint(editor):
    return unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                        for shape in aperture['geometry']])


def snapshot(editor):
    return {key: list(value['geometry']) for key, value in editor.storage_dict.items()}


def flash_at(editor, x, y):
    """The aperture and record of the flash whose follow point is (x, y)."""
    for aperture in editor.storage_dict.values():
        for shape in aperture['geometry']:
            follow = shape.geo.get('follow')
            if isinstance(follow, Point) and follow.equals_exact(Point(x, y), 1e-9):
                return aperture, shape
    return None, None


def is_stored(editor, record):
    return any(shape is record for aperture in editor.storage_dict.values() for shape in aperture['geometry'])


def pad_and_track_editor(track_width=0.4):
    pad, path = flash(0), track([(0, 0), (10, 0)], width=track_width)
    return editor_for({10: {'type': 'C', 'size': 2, 'geometry': [pad, path]}}), pad, path


def test_drawn_circle_and_rectangle_become_flashes_and_l_shape_is_skipped():
    circle = region(Point(0, 0).buffer(1, 64))
    rect = region(box(10, 0, 12, 1))
    ell = region(Polygon([(20, 0), (22, 0), (22, 1), (21, 1), (21, 2), (20, 2)]))
    editor = editor_for({11: {'type': 'REG', 'size': 0, 'geometry': [circle, rect, ell]}})
    before, keys, before_footprint = snapshot(editor), set(editor.storage_dict), footprint(editor)
    assert module.apply_pads_to_flashes(editor, 0.01) == (2, 1)
    c_aperture, c_shape = flash_at(editor, 0, 0)
    assert c_aperture['type'] == 'C' and c_aperture['size'] == 2
    assert c_shape.geo['solid'].symmetric_difference(circle.geo['solid']).area < 0.01
    r_aperture, _ = flash_at(editor, 11, 0.5)
    assert r_aperture['type'] == 'R' and (r_aperture['width'], r_aperture['height']) == (2, 1)
    assert editor.storage_dict[11]['geometry'] == [ell]
    assert footprint(editor).symmetric_difference(before_footprint).area < 0.01
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot(editor) == before
    assert set(editor.storage_dict) == keys


def test_existing_aperture_of_the_same_size_is_reused():
    circle = region(Point(0, 0).buffer(1, 64))
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [flash(30)]},
                         11: {'type': 'REG', 'size': 0, 'geometry': [circle]}})
    assert module.apply_pads_to_flashes(editor, 0.01) == (1, 0)
    _, shape = flash_at(editor, 0, 0)
    assert shape in editor.storage_dict[10]['geometry']
    assert [key for key, value in editor.storage_dict.items() if value['type'] == 'C'] == [10]


def test_teardrop_fills_the_neck_and_is_skipped_for_a_track_wider_than_the_pad():
    editor, pad, path = pad_and_track_editor()
    before, keys, before_footprint = snapshot(editor), set(editor.storage_dict), footprint(editor)
    added = module.apply_teardrops(editor, 50, 80)
    assert len(added) == 1
    assert is_stored(editor, added[0])
    drop = added[0].geo['solid']
    assert drop.intersection(pad.geo['solid']).area > 0
    assert drop.intersection(path.geo['solid']).area > 0
    assert footprint(editor).area > before_footprint.area + 0.05
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot(editor) == before
    assert set(editor.storage_dict) == keys
    wide, _, _ = pad_and_track_editor(track_width=2.5)
    wide_before = snapshot(wide)
    assert module.apply_teardrops(wide, 50, 80) == []
    assert snapshot(wide) == wide_before


def test_snowman_adds_a_circle_of_the_given_size_on_the_track_outside_the_pad():
    editor, _, _ = pad_and_track_editor()
    before, keys = snapshot(editor), set(editor.storage_dict)
    added = module.apply_snowman(editor, 50, 50)
    assert len(added) == 1
    assert is_stored(editor, added[0])
    ball = added[0].geo['solid']
    assert ball.area == pytest.approx(math.pi * 0.5 ** 2, rel=0.01)
    assert ball.centroid.distance(Point(1.5, 0)) < 0.01
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot(editor) == before
    assert set(editor.storage_dict) == keys


def test_native_save_reparse_after_teardrop_undo_redo(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [flash(0)]},
                         11: {'type': 'C', 'size': 0.4, 'geometry': [track([(0, 0), (10, 0)])]}})
    assert module.apply_teardrops(editor, 50, 80) != 'fail'
    for mode in ('edited', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = editor.loaded_as_regions
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
        assert unary_union(parsed.solid_geometry).symmetric_difference(footprint(editor)).area < 0.01


@pytest.mark.parametrize('kind', ['R', 'O'])
def test_drawn_pads_save_as_flashes_of_rectangular_and_oblong_apertures(app, kind):
    pad = box(0, 0, 2, 1) if kind == 'R' else LineString([(0.5, 0.5), (3.5, 0.5)]).buffer(0.5, 64)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [flash(20)]},
                         0: {'type': 'REG', 'size': 0, 'geometry': [region(pad)]}})
    assert module.apply_pads_to_flashes(editor, 0.01)[0] == 1
    assert any(aperture['type'] == kind for aperture in editor.storage_dict.values())
    assert not editor.loaded_as_regions
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for tool in parsed.tools.values()
               if tool.get('type') == kind for record in tool['geometry'])
