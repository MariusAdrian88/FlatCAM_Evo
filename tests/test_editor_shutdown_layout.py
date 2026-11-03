"""Persist the application toolbar arrangement when quitting from an editor."""
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets

from appMain import App
from appHandlers.appLifecycle import AppLifecycle
from tests.test_shutdown_routing import shutdown, qt_app  # noqa: F401


SOURCES = ['geo_editor']


def toolbar_states(window):
    tools = QtWidgets.QToolBar('Tools', window)
    tools.setObjectName('test_tools')
    window.addToolBar(tools)
    editor = QtWidgets.QToolBar('Editor', window)
    editor.setObjectName('test_editor')
    window.addToolBar(editor)
    tools.show()
    editor.hide()
    normal = window.saveState(0)
    tools.hide()
    editor.show()
    return tools, editor, normal


@pytest.mark.parametrize('source', SOURCES)
def test_window_close_saves_normal_layout_without_disturbing_an_editor_if_exit_stops(shutdown, source):
    s = shutdown
    tools, editor, normal = toolbar_states(s.window)
    s.app.call_source = source
    s.app._editor_gui_state = normal
    s.app.should_we_save = False
    current = s.window.saveState(0)
    s.action.trigger()
    saved = next(call.args[1] for call in s.settings.setValue.call_args_list
                 if call.args[0] == 'saved_gui_state')
    assert saved == (current if source == 'app' else normal)
    assert s.window.saveState(0) == current
    assert tools.isHidden() and not editor.isHidden()


@pytest.mark.parametrize('source', SOURCES)
def test_confirmed_quit_restores_and_saves_layout_before_editor_cleanup_can_replace_it(shutdown, monkeypatch, source):
    s = shutdown
    tools, editor, normal = toolbar_states(s.window)
    s.app.call_source = source
    s.app._editor_gui_state = normal
    current = s.window.saveState(0)
    expected = current if source == 'app' else normal
    s.app.headless = False
    s.app.tools_db_tab = None
    s.app.geo_editor = None
    s.window.general_pref_form = Mock()
    monkeypatch.setattr(QtCore, 'QSettings', Mock(return_value=s.settings))
    monkeypatch.setattr(QtWidgets.QApplication, 'quit', Mock())
    monkeypatch.setattr(QtCore.QTimer, 'singleShot', Mock())

    def deactivate(kind):
        # Editor shutdown can change both toolbar visibility and call_source.
        tools.hide()
        editor.hide()
        s.app.call_source = 'app'

    s.app._deactivate_editor.side_effect = deactivate
    assert AppLifecycle.quit_application(s.lifecycle) is True
    saved = next(call.args[1] for call in s.settings.setValue.call_args_list
                 if call.args[0] == 'saved_gui_state')
    assert saved == expected
    assert s.window.saveState(0) == expected


@pytest.mark.parametrize('kind,attribute,method,source', [
    ('gerber', 'grb_editor', 'edit_fcgerber', 'grb_editor'),
])
def test_entering_an_editor_captures_the_layout_before_toolbars_change(qt_app, kind, attribute, method, source):
    window = QtWidgets.QMainWindow()
    window.show()
    tools, toolbar, normal = toolbar_states(window)
    window.restoreState(normal)
    ui = Mock(toolbarplugins=tools, saveState=window.saveState,
              geo_edit_toolbar=toolbar, grb_edit_toolbar=toolbar, exc_edit_toolbar=toolbar)
    ui.notebook.count.return_value = 0
    ui.splitter.sizes.return_value = [120, 400]
    obj = Mock(kind=kind, multigeo=False)
    app = Mock(call_source='app', ui=ui)
    app.collection.get_active.return_value = obj
    getattr(getattr(app, attribute), method).side_effect = lambda *args: toolbar.show()
    try:
        App.on_editing_start(app)
        assert app.call_source == source
        assert isinstance(app._editor_gui_state, QtCore.QByteArray)
        assert app._editor_gui_state == normal
        assert tools.isHidden() and not toolbar.isHidden()
    finally:
        window.close()
