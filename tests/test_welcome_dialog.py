"""Welcome navigation, file dispatch and dismissal into the workspace."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from defaults import AppDefaults, AppOptions


@pytest.fixture(scope='module')
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def welcome(qapp):
    from appGUI.WelcomeDialog import WelcomeDialog
    opened = []
    handlers = Mock()
    for name in ('open_document', 'open_hpgl2', 'import_dxf', 'import_svg', 'open_gerber',
                 'open_excellon', 'open_script', 'open_project', 'on_file_import_sprint_layout',
                 'on_file_import_diptrace', 'import_pdf'):
        getattr(handlers, name).side_effect = lambda *args, kind=name: opened.append((kind, args))
    app = SimpleNamespace(
        ui=QtWidgets.QWidget(), resource_location='assets/resources', version='1.2.3', version_date='2026/11/07',
        beta=False, defaults=AppDefaults(beta=False),
        recent=[], recent_projects=[], f_handlers=handlers, image_tool=Mock(), pdf_tool=Mock(),
        get_last_folder=lambda: '', inform=Mock(), log=Mock(), preferencesUiManager=Mock(),
        projects=SimpleNamespace(new=lambda: SimpleNamespace(filename=None)), app_obj=Mock())
    app.options = AppOptions(version='test', baseline=app.defaults, defaults=app.defaults)
    app.worker_task = SimpleNamespace(emit=lambda task: task['fcn'](*task['params']))
    dialog = WelcomeDialog(app, app.ui)
    dialog.show()
    qapp.processEvents()
    yield dialog, app, opened
    dialog.close()
    app.ui.close()
    dialog.deleteLater()
    app.ui.deleteLater()


@pytest.mark.parametrize('resources', ['assets/resources', 'assets/resources/dark_resources'])
def test_manufacturing_and_cad_icons_render_in_both_themes(welcome, resources):
    from appGUI.WelcomeDialog import welcome_entries
    dialog, app, opened = welcome
    app.resource_location = resources
    entries = {entry['key']: entry for title, group in welcome_entries(app) for entry in group}
    for key in ('odb', 'dxf'):
        image = QtGui.QImage(entries[key]['icon'])
        assert image.size() == QtCore.QSize(64, 64)
        pixmap = QtGui.QIcon(entries[key]['icon']).pixmap(18, 18)
        assert not pixmap.isNull()


def test_search_and_creation_availability(welcome):
    dialog, app, opened = welcome
    assert dialog.tabs.currentIndex() == 0
    assert {'pcbdoc', 'proteus', 'kicad_pcb', 'sprint_layout', 'eagle_brd', 'allegro_brd', 'diptrace'} <= set(dialog.entries)
    dialog.filter_entry.setText('sprint')
    assert dialog.visible_keys() == ['sprint_layout']
    assert not dialog.new_button.isEnabled()
    dialog.filter_entry.clear()
    dialog.select('geometry')
    assert dialog.new_button.isEnabled()
    assert not dialog.browse_button.isEnabled()
    dialog.select('svg')
    assert dialog.browse_button.isEnabled()
    assert dialog.object_type.count() == 2


def test_category_keyboard_selection_and_enter_never_start_a_file_action(welcome, monkeypatch):
    dialog, app, opened = welcome
    picked = []
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName',
                        lambda **kwargs: picked.append(True) or ('', ''))
    dialog.tree.setFocus()
    dialog.select('geometry')
    QtTest.QTest.keyClick(dialog.tree, QtCore.Qt.Key.Key_Up)
    assert dialog.title_label.text() == 'Geometry'
    assert 'Import drawings' in dialog.details_label.text()
    assert not dialog.browse_button.isEnabled() and not dialog.new_button.isEnabled()
    QtTest.QTest.keyClick(dialog.tree, QtCore.Qt.Key.Key_Return)
    assert not picked and not opened
    assert app.f_handlers.method_calls == [] and app.app_obj.method_calls == []


def test_search_keeps_valid_category_and_reserves_no_match_for_zero_results(welcome):
    dialog, app, opened = welcome
    category = dialog.tree.topLevelItem(1)
    dialog.tree.setCurrentItem(category)
    dialog.filter_entry.setText('geometry')
    assert dialog.tree.currentItem() is category
    assert 'Import drawings' in dialog.details_label.text()
    dialog.filter_entry.setText('no-such-file-type')
    assert dialog.title_label.text() == 'No file type matches the search.'
    assert not dialog.ext_label.isVisible() and not dialog.empty_label.isVisible()
    dialog.filter_entry.clear()
    assert dialog.title_label.text() == 'FlatCAM Evo Project'
    assert dialog.browse_button.isEnabled() and dialog.new_button.isEnabled()


def test_child_selection_restores_details_and_keyboard_activation_opens_its_file(
        welcome, monkeypatch, tmp_path):
    dialog, app, opened = welcome
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))
    filename = tmp_path / 'drawing.svg'
    filename.write_text('<svg/>', encoding='utf-8')
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName', lambda **kwargs: (str(filename), ''))
    dialog.select('svg')
    assert dialog.ext_label.isVisible() and dialog.creates_label.isVisible() and dialog.object_type.isVisible()
    assert dialog.findChild(QtWidgets.QLabel, 'welcome_history_heading').isVisible()
    assert dialog.browse_button.isEnabled() and not dialog.new_button.isEnabled()
    dialog.tree.setFocus()
    QtTest.QTest.keyClick(dialog.tree, QtCore.Qt.Key.Key_Return)
    assert opened == [('import_svg', (str(filename), 'geometry'))]


def test_recent_files_match_type_limit_and_survive_bad_history(welcome, tmp_path):
    dialog, app, opened = welcome
    paths = [str(tmp_path / ('board%d.lay6' % n)) for n in range(8)]
    app.recent = [None, 'broken', {}, {'kind': 'sprint_lay6', 'filename': 42},
                  {'kind': 'gerber', 'filename': 'other.gbr'}] + [
                      {'kind': 'sprint_lay6', 'filename': name} for name in paths]
    dialog.select('sprint_layout')
    assert dialog.recent_list.count() == 5
    assert [dialog.recent_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(5)] == paths[:5]
    assert paths[0] in dialog.recent_list.item(0).toolTip()
    app.recent = {'invalid': 'history'}
    dialog.select('document')
    assert dialog.recent_list.count() == 0
    app.recent = [{'kind': 'geometry', 'filename': 'unknown.format'}]
    dialog.filter_entry.setText('no matching file type')
    assert dialog.recent_list.count() == 0


@pytest.mark.parametrize('key,history_kind,suffix,handler', [
    ('document', 'Document', '.FlatDoc', 'open_document'),
    ('hpgl2', 'geometry', '.plt', 'open_hpgl2'),
    ('dxf', 'geometry', '.dxf', 'import_dxf'),
    ('sprint_layout', 'sprint_lay6', '.lay6', 'on_file_import_sprint_layout'),
])
def test_recent_opens_exact_file_with_correct_handler(welcome, tmp_path, key, history_kind, suffix, handler):
    dialog, app, opened = welcome
    filename = tmp_path / ('selected' + suffix)
    filename.write_text('data', encoding='utf-8')
    app.recent = [{'kind': history_kind, 'filename': str(filename)}]
    dialog.select(key)
    item = dialog.recent_list.item(0)
    assert item is not None
    dialog.open_recent(item)
    assert opened[0][0] == handler
    assert opened[0][1][0] == str(filename)
    assert not dialog.isVisible()


def test_missing_recent_and_cancelled_browse_keep_welcome(welcome, monkeypatch):
    dialog, app, opened = welcome
    app.recent = [{'kind': 'gerber', 'filename': 'missing-welcome-test.gbr'}]
    dialog.select('gerber')
    dialog.open_recent(dialog.recent_list.item(0))
    assert dialog.isVisible() and not opened
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName', lambda **kwargs: ('', ''))
    dialog.browse_button.click()
    assert dialog.isVisible() and not opened


def test_single_file_browse_preserves_vector_object_choice(welcome, tmp_path, monkeypatch):
    dialog, app, opened = welcome
    filename = tmp_path / 'drawing.svg'
    filename.write_text('<svg/>', encoding='utf-8')
    dialog.select('svg')
    dialog.object_type.setCurrentIndex(dialog.object_type.findData('gerber'))
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName', lambda **kwargs: (str(filename), ''))
    dialog.browse_button.click()
    assert opened == [('import_svg', (str(filename), 'gerber'))]
    assert not dialog.isVisible()


def test_checkbox_persists_same_preference_and_informational_tabs_only_continue(welcome):
    dialog, app, opened = welcome
    dialog.next_time.click()
    assert app.defaults['global_welcome'] is False
    assert app.options['global_welcome'] is False
    app.preferencesUiManager.save_defaults.assert_called_once_with(silent=True)
    for index in range(1, 4):
        dialog.tabs.setCurrentIndex(index)
        assert not dialog.browse_button.isVisible()
        assert not dialog.new_button.isVisible()
        assert dialog.continue_button.isVisible() and dialog.continue_button.isDefault()
        assert dialog.next_time.isVisible()


@pytest.mark.parametrize('action', ['escape', 'close', 'continue'])
def test_dismissal_leaves_workspace_alive(welcome, qapp, action):
    dialog, app, opened = welcome
    app.ui.show()
    if action == 'escape':
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Escape)
    elif action == 'close':
        dialog.close_button.click()
    else:
        dialog.continue_button.click()
    assert not dialog.isVisible()
    assert app.ui.isVisible()
    assert not opened


@pytest.mark.parametrize('busy', [False, True])
def test_new_object_uses_an_untitled_project_without_discarding_current(welcome, busy):
    from tests.test_projects_manager import FakeCollection, make_app, make_manager
    dialog, original, opened = welcome
    app, manager = make_manager(make_app(FakeCollection(['existing object'])))
    app.projects = manager
    app.project_filename = 'existing.FlatPrj'
    app.should_we_save = True
    previous = manager.active
    app.workers.pending_count.return_value = int(busy)
    app.app_obj.new_document_object = lambda: app.collection.objects.append('document')
    dialog.app = app
    dialog.select('document')
    dialog.new_button.click()
    assert previous.collection.objects == ['existing object']
    if busy:
        assert manager.active is previous
        assert dialog.isVisible()
    else:
        assert manager.active is not previous
        assert manager.active.filename is None
        assert app.collection.objects == ['document']
        assert previous.should_we_save and previous.filename == 'existing.FlatPrj'
        assert not dialog.isVisible()


@pytest.mark.parametrize('control', ['continue_button', 'close_button', 'new_button'])
def test_return_activates_the_focused_action(welcome, monkeypatch, control):
    dialog, app, opened = welcome
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName', lambda **kwargs: ('', ''))
    dialog.select('document')
    button = getattr(dialog, control)
    button.setFocus()
    QtTest.QTest.keyClick(button, QtCore.Qt.Key.Key_Return)
    assert not dialog.isVisible()
    assert not opened


def test_saved_startup_choice_survives_preferences_cancel(welcome, tmp_path):
    from appGUI.preferences.PreferencesUIManager import PreferencesUIManager
    dialog, app, opened = welcome
    manager = PreferencesUIManager.__new__(PreferencesUIManager)
    QtCore.QObject.__init__(manager)
    manager.defaults = app.defaults
    manager.ui = SimpleNamespace(app=app)
    manager.data_path = str(tmp_path)
    manager.inform = app.inform
    app.preferencesUiManager = manager
    dialog.next_time.click()
    saved = AppDefaults(beta=False)
    saved.load(str(tmp_path / 'current_defaults.FlatConfig'), inform=app.inform)
    assert saved['global_welcome'] is False
    app.defaults.update(app.defaults.current_defaults)  # Preferences > Cancel restores this snapshot.
    assert app.options['global_welcome'] is False


@pytest.mark.parametrize('height', [480, 360])
def test_layout_keeps_actions_accessible_on_a_small_logical_screen(welcome, qapp, monkeypatch, height):
    dialog, app, opened = welcome
    monkeypatch.setattr(app.ui, 'screen', lambda: SimpleNamespace(
        availableGeometry=lambda: QtCore.QRect(0, 0, 640, height)))
    dialog.hide()
    dialog.select('gerber')
    dialog.show()
    qapp.processEvents()
    assert dialog.width() <= 640 and dialog.height() <= height
    for control in (dialog.next_time, dialog.continue_button, dialog.browse_button, dialog.new_button):
        rect = QtCore.QRect(control.mapTo(dialog, QtCore.QPoint()), control.size())
        assert dialog.rect().contains(rect)


def test_keyboard_can_reach_and_open_the_last_recent_file_on_a_compact_screen(
        welcome, qapp, monkeypatch, tmp_path):
    dialog, app, opened = welcome
    filenames = [tmp_path / ('board%d.gbr' % index) for index in range(5)]
    for filename in filenames:
        filename.write_text('board', encoding='utf-8')
    app.recent = [{'kind': 'gerber', 'filename': str(filename)} for filename in filenames]
    monkeypatch.setattr(app.ui, 'screen', lambda: SimpleNamespace(
        availableGeometry=lambda: QtCore.QRect(0, 0, 640, 360)))
    dialog.hide()
    dialog.select('gerber')
    dialog.show()
    qapp.processEvents()
    dialog.recent_list.setFocus()
    dialog.recent_list.setCurrentRow(0)
    QtTest.QTest.keyClick(dialog.recent_list, QtCore.Qt.Key.Key_End)
    qapp.processEvents()
    outer = dialog.findChildren(QtWidgets.QScrollArea)[0]
    item_rect = dialog.recent_list.visualItemRect(dialog.recent_list.currentItem())
    center = dialog.recent_list.viewport().mapTo(outer.viewport(), item_rect.center())
    assert outer.viewport().rect().contains(center)
    QtTest.QTest.keyClick(dialog.recent_list, QtCore.Qt.Key.Key_Return)
    assert opened[0][1][0] == str(filenames[-1])
