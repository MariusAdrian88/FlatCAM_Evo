"""Gerber size selection and atomic, aperture-preserving batch simplification."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtWidgets
from shapely.geometry import Point, Polygon, box

from appEditors.appGerberEditor import SimplifyEditorGrb
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from test_gerber_editor_pick import aperture, make_editor


def size_editor():
    small = DrawToolShape({'solid': box(0, 0, 2, 3)})
    wide = DrawToolShape({'solid': box(0, 0, 4, 3)})
    empty = DrawToolShape({'solid': Polygon()})
    follow = DrawToolShape({'follow': Point(0, 0)})
    editor = make_editor({10: aperture([small, empty]), '11': aperture([wide, follow])})
    editor.ui.size_width_chk.setChecked(True)
    editor.ui.size_width_entry.setText('2')
    editor.ui.size_height_entry.setText('3')
    editor.ui.size_tolerance_entry.setText('0')
    editor.plot_selection = MagicMock()
    return editor, small, wide


def test_size_filters_combine_and_select_searches_whole_board():
    editor, small, wide = size_editor()
    editor.selected = [wide]
    # Select ignores the current selection, so repeating it never only narrows
    assert editor.on_select_by_size() == [small]
    editor.ui.size_width_entry.setText('4,05')
    editor.ui.size_tolerance_entry.setText('0,1')
    editor.ui.size_height_chk.setChecked(True)
    assert editor.on_select_by_size() == [wide]
    editor.selected = [wide, small]
    editor.ui.size_height_entry.setText('8')
    assert editor.on_select_by_size() == []


@pytest.mark.parametrize('field,value', [
    ('size_width_entry', 'nan'),
])
def test_invalid_size_input_preserves_selection(field, value):
    editor, small, wide = size_editor()
    editor.selected = [wide]
    getattr(editor.ui, field).setText(value)
    assert editor.on_select_by_size() == 'fail'
    assert editor.selected == [wide]
    editor.plot_selection.assert_not_called()


def simplify_tool(editor, tolerance=0.1):
    tool = SimplifyEditorGrb.__new__(SimplifyEditorGrb)
    tool.app = editor.app
    tool.draw_app = editor
    tool.ui = SimpleNamespace(geo_tol_entry=MagicMock(), geo_coords_entry=MagicMock(),
                              geo_vertex_entry=MagicMock())
    tool.ui.geo_tol_entry.get_value.return_value = tolerance
    return tool


def run_job(editor):
    job = editor.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    QtWidgets.QApplication.processEvents()


def test_simplification_failure_does_not_delete_any_selected_shape():
    first = DrawToolShape({'solid': box(0, 0, 2, 2)})
    bad = MagicMock()
    bad.simplify.side_effect = ValueError('bad geometry')
    second = DrawToolShape({'solid': bad})
    editor = make_editor({10: aperture([first, second])})
    editor.selected = [first, second]
    revision = editor.geometry_revision
    simplify_tool(editor).on_simplification_click()
    run_job(editor)
    assert editor.storage_dict[10]['geometry'] == [first, second]
    assert editor.selected == [first, second]
    assert editor.geometry_revision == revision
    assert not editor.is_modified
    editor.plot_all.assert_not_called()
    editor.app.inform.emit.assert_called()


@pytest.mark.parametrize('tolerance', [-1])
def test_simplification_rejects_invalid_tolerance(tolerance):
    editor = make_editor({10: aperture([DrawToolShape({'solid': box(0, 0, 1, 1)})])})
    editor.selected = list(editor.storage_dict[10]['geometry'])
    simplify_tool(editor, tolerance).on_simplification_click()
    editor.app.worker_task.emit.assert_not_called()


