"""Split one open path at a projected click, in one committed operation."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtWidgets
from shapely.geometry import LineString, LinearRing, MultiLineString, Polygon

from appEditors import appGeoEditor as geo
from appGUI.GUIElements import FCTree
from tests.test_geo_editor_history import run_worker
from tests.test_geo_editor_vertices import vertex_editor


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def split_editor(geometry=None):
    editor, original = vertex_editor(geometry if geometry is not None else
                                     LineString([(0, 0), (2, 0), (4, 2)]))
    editor.multigeo_tool = 7
    original.data['tool'] = 7
    editor.apply_path_split = lambda *a, **k: geo.AppGeoEditor.apply_path_split(editor, *a, **k)
    return editor, original


@pytest.mark.parametrize('point', [(1, 0.05)])
@pytest.mark.parametrize('reverse', [False])
def test_split_conserves_length_direction_metadata_and_replays(point, reverse):
    coords = [(0, 0), (2, 0), (4, 2)]
    editor, original = split_editor(LineString(coords[::-1] if reverse else coords))
    editor.storage.replace = Mock(wraps=editor.storage.replace)
    editor._snap_cache = object()
    assert editor.apply_path_split(point, 0.1) != 'fail'
    a, b = editor.selected
    assert a.geo.length > 0 and b.geo.length > 0
    assert a.geo.length + b.geo.length == pytest.approx(original.geo.length)
    assert a.geo.coords[0] == original.geo.coords[0]
    assert b.geo.coords[-1] == original.geo.coords[-1]
    assert a.geo.coords[-1] == b.geo.coords[0]
    assert original.geo.distance(geo.Point(a.geo.coords[-1])) < 1e-12
    for shape in (a, b):
        assert shape.data['custom'] == original.data['custom']
        assert shape.data['tool'] == 7
        assert shape.data['bounds'] == shape.geo.bounds
        assert shape.data is not original.data
    assert editor.multigeo_tool == 7
    assert editor._snap_cache is None
    assert set(editor.storage.indexes) == {id(a), id(b)}
    assert editor.storage.nearest(a.geo.coords[0])[1] is a
    assert editor.sel_shapes.items == [a.geo, b.geo]
    editor.storage.replace.assert_called_once()
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.selected == [original]
    assert list(editor.storage.get_objects()) == [original]
    assert editor.sel_shapes.items == [original.geo]
    assert not editor.undo()
    assert editor.redo()
    assert editor.selected == [a, b]
    assert editor.sel_shapes.items == [a.geo, b.geo]


@pytest.mark.parametrize('point,tolerance', [
    ((2, 0.101), 0.1),
])
def test_rejection_preserves_original_selection_render_cache_and_redo(point, tolerance):
    editor, original = split_editor(LineString([(0, 0), (4, 0)]))
    assert editor.apply_path_split((2, 0), 0.1) != 'fail'
    assert editor.undo()
    redo = list(editor._history_redo)
    cache = editor._snap_cache = object()
    editor.build_ui_sig.emit.reset_mock()
    assert editor.apply_path_split(point, tolerance) == 'fail'
    assert list(editor.storage.get_objects()) == [original]
    assert editor.selected == [original]
    assert editor.sel_shapes.items == [original.geo]
    assert editor._snap_cache is cache
    assert editor._history_redo == redo
    editor.build_ui_sig.emit.assert_not_called()
    assert editor.app.inform.emit.called
    assert editor.redo()


@pytest.mark.parametrize('geometry', [
    Polygon([(0, 0), (4, 0), (0, 4)]),
])
def test_unsupported_shapes_explain_open_linestring_scope(geometry):
    editor, original = split_editor(geometry)
    assert editor.apply_path_split((2, 0), 0.1) == 'fail'
    assert 'open' in str(editor.app.inform.emit.call_args).lower()
    assert 'LineString' in str(editor.app.inform.emit.call_args)
    assert editor.selected == [original]
    assert not editor.undo()


def test_zero_tolerance_accepts_exact_interior_and_rejects_offpath():
    editor, original = split_editor()
    assert editor.apply_path_split((1, 0.001), 0) == 'fail'
    assert editor.apply_path_split((1, 0), 0) != 'fail'


def interactive_editor():
    editor, original = split_editor(LineString([(0, 0), (4, 0)]))
    editor.decimals = 4
    editor.app.decimals = 4
    editor.app.app_units = 'mm'
    editor.app.resource_location = ''
    editor.app.on_editing_finished = Mock()
    editor.ui = geo.AppGeoEditorUI(editor.app)
    editor.select_tool = Mock()
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: pos)
    editor.app.grid_status = lambda: False
    editor.app.ui = SimpleNamespace(rel_position_label=Mock(), update_location_labels=Mock())
    editor.app.plotcanvas = SimpleNamespace(on_update_text_hud=Mock())
    editor.snap = Mock(return_value=(2, 0.05))
    editor.active_tool = geo.FCSplit(editor)
    return editor, original


