"""Feature-level filtering and deletion use editor-local selection/history."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtWidgets

from appEditors.appExcEditor import AppExcEditor, AppExcEditorUI
from test_exc_editor_history import scene


def editor_scene():
    editor, drill, slot = scene()
    for name in ('filter_features', 'select_filtered_features', 'delete_filtered_features', 'on_history_row'):
        setattr(editor, name, MethodType(getattr(AppExcEditor, name), editor))
    return editor, drill, slot


def test_filter_selection_scope_and_one_step_deletion():
    editor, drill, slot = editor_scene()
    assert [r['shape'] for r in editor.filter_features(kind='slot')] == [slot]
    assert not editor.filter_features(kind='slot', selected_only=True)
    editor.selected = [drill, slot]
    assert [r['shape'] for r in editor.filter_features(length=(2, 3))] == [slot]
    assert [r['shape'] for r in editor.filter_features(bounds=(-1, -1, 1, 1))] == [drill]
    assert not editor.filter_features(diameter=(2, 3))
    assert editor.delete_filtered_features(kind='slot', selected_only=True) == 1
    assert editor.selected == [drill]
    assert list(editor.storage_dict[1.0].get_objects()) == [drill]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.selected == [drill, slot]
    assert list(editor.storage_dict[1.0].get_objects()) == [drill, slot]


@pytest.mark.parametrize('filters', [dict(diameter=(float('nan'), 2)), dict(kind='polygon')])
def test_invalid_filters_are_atomic(filters):
    editor, drill, slot = editor_scene()
    assert editor.delete_filtered_features(**filters) == 'fail'
    assert editor.selected == [drill]
    assert list(editor.storage_dict[1.0].get_objects()) == [drill, slot]
    assert not editor.history.undo_stack


def test_real_feature_controls_selection_filters_and_undo():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    editor, drill, slot = editor_scene()
    editor.ui = AppExcEditorUI(SimpleNamespace(decimals=4, app_units='MM', resource_location='assets/resources'))
    for name in ('on_feature_filter_action', 'on_feature_selection'):
        setattr(editor, name, MethodType(getattr(AppExcEditor, name), editor))
    editor.ui.tools_table_exc.setRowCount(3)  # One tool and two summary rows.
    editor.ui.tools_table_exc.setItem(0, 0, QtWidgets.QTableWidgetItem('1'))
    editor.ui.tools_table_exc.setItem(0, 1, QtWidgets.QTableWidgetItem('1.0'))
    editor.ui.feature_table.itemSelectionChanged.connect(lambda: editor.on_feature_selection())
    editor.ui.feature_select.clicked.connect(lambda: editor.on_feature_filter_action())
    editor.ui.feature_delete.clicked.connect(lambda: editor.on_feature_filter_action(delete=True))
    editor.refresh_feature_table()
    assert editor.ui.feature_table.rowCount() == 2
    editor.ui.feature_table.selectRow(1)
    assert editor.selected == [slot]
    assert editor.ui.tools_table_exc.item(0, 0).isSelected()
    editor.ui.feature_kind.setCurrentIndex(2)
    editor.ui.feature_ranges['length'].setText('2, 3')
    editor.ui.feature_select.click()
    assert editor.selected == [slot]
    editor.ui.feature_delete.click()
    assert list(editor.storage_dict[1.0].get_objects()) == [drill]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert list(editor.storage_dict[1.0].get_objects()) == [drill, slot]
    assert editor.selected == [slot]
