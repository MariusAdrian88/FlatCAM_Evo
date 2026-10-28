"""Exit Editor: Save (in place) and Save as New for the Geometry, Gerber and Excellon editors, and the exit of the
Code Editor (CNCJob) with its tab. Headless."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import Point, box

from appEditors.appExcEditor import AppExcEditor, match_tool_data, tools_with_data
from appEditors.appGerberEditor import AppGerberEditor, edit_copy_name, get_finite_bounds
from appGUI.GUIElements import FCDetachableTab2
from appHandlers.appIO import appIO
from appMain import App
from appObjects.GeometryObject import GeometryObject

from tests.helpers_objects import (  # noqa: F401,E402
    app, _headless_qt, make_gerber, make_same_diameter_excellon, NATIVE_GBR,
)

BOUNDS_KEYS = ('xmin', 'ymin', 'xmax', 'ymax')


# ---------------------------------------------------------------------------------------------------------------
# the Exit Editor dialog and the flow of each button
# ---------------------------------------------------------------------------------------------------------------

class FakeBox:
    """Stands in for FCMessageBox: records the buttons, 'clicks' the button with the text in FakeBox.click."""
    click = None
    last = None

    def __init__(self, *args, **kwargs):
        self.buttons = {}
        FakeBox.last = self

    def addButton(self, text, role):
        button = SimpleNamespace(text=text, role=role)
        self.buttons[text] = button
        return button

    def clickedButton(self):
        return self.buttons.get(FakeBox.click)

    def __getattr__(self, name):
        return Mock()


def editor_app(kind, empty=False):
    app_stub = MagicMock()
    app_stub.call_source = 'grb_editor'
    app_stub.resource_location = ''
    app_stub.old_state_of_tools_toolbar = False
    app_stub.ui.notebook.count.return_value = 0
    app_stub.history = None
    obj = SimpleNamespace(kind=kind, obj_options={'name': 'obj'}, set_offset_values=Mock(), build_ui=Mock(),
                          plot=Mock(), bounds=Mock(return_value=(0, 0, 1, 1)))
    app_stub.collection.get_active.return_value = obj
    app_stub._edited_obj_is_empty = Mock(return_value=empty)
    app_stub._history_edit_saved = Mock()
    app_stub._history_edit = object()
    return app_stub, obj


def finish(app_stub, click, force_cancel=None):
    FakeBox.click = click
    with patch('appMain.FCMessageBox', FakeBox):
        return App.on_editing_finished(app_stub, force_cancel=force_cancel)






def test_gerber_save_updates_in_place_and_records_after_the_update():
    app_stub, obj = editor_app('gerber', empty=True)
    assert finish(app_stub, 'Save') == 'pending'
    app_stub.grb_editor.update_fcgerber.assert_not_called()
    on_complete = app_stub.grb_editor.update_fcgerber_in_place.call_args.kwargs['on_complete']
    # the source is not updated yet: no history step, no teardown
    app_stub._history_edit_saved.assert_not_called()
    app_stub.post_edit_sig.emit.assert_not_called()
    on_complete()
    app_stub._history_edit_saved.assert_called_once_with(obj)
    app_stub.grb_editor.refresh_edited_gerber.assert_called_once_with(obj)
    app_stub.post_edit_sig.emit.assert_called_once()
    # an in-place save never deletes the source, even when it is empty
    app_stub.collection.delete_by_name.assert_not_called()


def test_gerber_save_as_new_keeps_the_source_and_the_empty_source_rule():
    app_stub, obj = editor_app('gerber', empty=True)
    finish(app_stub, 'Save as New')
    app_stub.grb_editor.update_fcgerber.assert_called_once_with()
    app_stub.grb_editor.update_fcgerber_in_place.assert_not_called()
    app_stub.grb_editor.refresh_edited_gerber.assert_not_called()
    app_stub.collection.delete_by_name.assert_called_once_with('obj')
    app_stub._history_edit_saved.assert_not_called()
    assert app_stub._history_edit is None

    app_stub, obj = editor_app('gerber', empty=False)
    finish(app_stub, 'Save as New')
    app_stub.collection.delete_by_name.assert_not_called()


def test_excellon_save_in_place_and_save_as_new():
    app_stub, obj = editor_app('excellon', empty=True)
    assert finish(app_stub, 'Save') == 'pending'
    call = app_stub.exc_editor.update_fcexcellon.call_args
    assert call.args == (obj,) and call.kwargs['in_place'] is True
    app_stub._history_edit_saved.assert_not_called()
    call.kwargs['on_complete']()
    app_stub._history_edit_saved.assert_called_once_with(obj)
    app_stub.exc_editor.refresh_edited_excellon.assert_called_once_with(obj)
    app_stub.collection.delete_by_name.assert_not_called()

    app_stub, obj = editor_app('excellon', empty=True)
    assert finish(app_stub, 'Save as New') == 'pending'
    call = app_stub.exc_editor.update_fcexcellon.call_args
    assert call.args == (obj,) and call.kwargs['in_place'] is False
    app_stub.post_edit_sig.emit.assert_not_called()
    call.kwargs['on_complete']()
    app_stub.collection.delete_by_name.assert_not_called()
    app_stub._history_edit_saved.assert_not_called()
    assert app_stub._history_edit is None




def test_geometry_save_writes_into_the_source():
    app_stub, obj = editor_app('geometry')
    app_stub.geo_editor.update_editor_geometry.return_value = 'pending'
    assert finish(app_stub, 'Save') == 'pending'
    call = app_stub.geo_editor.update_editor_geometry.call_args
    assert call.args == (obj,)
    call.kwargs['on_complete']()
    app_stub._history_edit_saved.assert_called_once_with(obj)
    obj.build_ui.assert_called_once()
    app_stub._copy_geometry_for_save_as_new.assert_not_called()


def test_geometry_save_as_new_writes_into_a_copy_and_leaves_the_source():
    app_stub, obj = editor_app('geometry')
    copy_obj = SimpleNamespace(kind='geometry', obj_options={'name': 'obj_edit'}, set_offset_values=Mock(),
                               build_ui=Mock(), plot=Mock(), bounds=Mock(return_value=(0, 0, 2, 2)))
    app_stub._copy_geometry_for_save_as_new = Mock(return_value=copy_obj)
    app_stub.geo_editor.update_editor_geometry.return_value = 'pending'
    assert finish(app_stub, 'Save as New') == 'pending'
    call = app_stub.geo_editor.update_editor_geometry.call_args
    assert call.args == (copy_obj,)
    call.kwargs['on_complete']()
    copy_obj.build_ui.assert_called_once()
    copy_obj.plot.assert_called_once()
    obj.build_ui.assert_not_called()
    obj.plot.assert_not_called()
    app_stub._history_edit_saved.assert_not_called()
    assert app_stub._history_edit is None




def test_geometry_save_as_new_removes_the_copy_when_the_save_is_rejected():
    app_stub, _obj = editor_app('geometry')
    copy_obj = SimpleNamespace(obj_options={'name': 'obj_edit'})
    app_stub._copy_geometry_for_save_as_new = Mock(return_value=copy_obj)
    app_stub.geo_editor.update_editor_geometry.return_value = 'fail'
    assert finish(app_stub, 'Save as New') == 'fail'
    app_stub.collection.delete_by_name.assert_called_once_with('obj_edit')


def gcode_editor_tabs(app_stub):
    """A real Plot Area with the Code Editor tab between 2 other tabs."""
    tabs = FCDetachableTab2()
    for name in ('plot_area_tab', 'gcode_editor_tab', 'preferences_tab'):
        page = QtWidgets.QWidget()
        page.setObjectName(name)
        tabs.addTab(page, name)
    app_stub.ui.plot_tab_area = tabs
    app_stub.call_source = 'gcode_editor'

    # leaving the editor (update_fcgcode() for Yes, _deactivate_editor() for No) sets call_source back to 'app'
    def leave(*args):
        app_stub.call_source = 'app'
    app_stub.gcode_editor.update_fcgcode.side_effect = leave
    app_stub._deactivate_editor.side_effect = leave
    return tabs


def tab_names(tabs):
    return [tabs.widget(idx).objectName() for idx in range(tabs.count())]


def test_cncjob_exit_editor_yes_with_a_tab_after_the_code_editor():
    # the loop that closed the Code Editor tab went on over the old tab count: widget(idx) was None, an
    # AttributeError (Critical Error dialog) and the app stayed in the editor mode (no post_edit_sig)
    app_stub, _obj = editor_app('cncjob')
    tabs = gcode_editor_tabs(app_stub)
    finish(app_stub, 'Yes')
    assert tab_names(tabs) == ['plot_area_tab', 'preferences_tab']
    app_stub.post_edit_sig.emit.assert_called_once()


@pytest.mark.parametrize('click', ['Yes', 'No'])
def test_cncjob_closing_the_code_editor_tab_closes_only_that_tab(click):
    # the X of the Code Editor tab: the handler of tab_closed_signal exits the editor, which closes the tab itself;
    # closeTab() then removed the tab at the old index, which was the next tab (here Preferences)
    app_stub, _obj = editor_app('cncjob')
    tabs = gcode_editor_tabs(app_stub)
    errors = []

    def on_closed(tab_name, index):
        # as App.on_plot_area_tab_closed(); FlatCAM shows an exception of a slot as a Critical Error
        if tab_name == 'gcode_editor_tab':
            try:
                finish(app_stub, click)
            except Exception as err:
                errors.append(err)
    tabs.tab_closed_signal.connect(on_closed)
    assert tabs.closeTab(1) is True
    assert errors == []
    assert tab_names(tabs) == ['plot_area_tab', 'preferences_tab']
    app_stub.post_edit_sig.emit.assert_called_once()


# ---------------------------------------------------------------------------------------------------------------
# names, bounds
# ---------------------------------------------------------------------------------------------------------------





# ---------------------------------------------------------------------------------------------------------------
# Geometry: Save as New copy
# ---------------------------------------------------------------------------------------------------------------





# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

def gerber_storage(source, drop_first_aperture=False):
    storage = {}
    for ap, tool in source.tools.items():
        storage[ap] = {k: v for k, v in tool.items() if k != 'geometry'}
        storage[ap]['geometry'] = [SimpleNamespace(geo=dict(rec)) for rec in tool['geometry']]
    if drop_first_aperture:
        storage.pop(next(ap for ap in storage if storage[ap]['geometry']))
    return storage


def gerber_stand_in(app, source, storage):
    app.f_handlers = appIO(app)
    app.app_units = 'MM'
    app.custom_signal = MagicMock()
    app.main_thread = QtCore.QThread.currentThread()
    return SimpleNamespace(app=app, gerber_obj=source, gerber_obj_options=source.obj_options, results=[1],
                           loaded_as_regions=False, storage_dict=storage, edited_obj_name=source.obj_options['name'])


def non_bounds_options(obj):
    return {k: v for k, v in obj.obj_options.items() if k not in BOUNDS_KEYS}




def test_gerber_in_place_build_failure_leaves_the_source_unchanged(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    tools_before = repr(sorted(source.tools))
    solid_before = source.solid_geometry
    storage = gerber_storage(source)
    storage[next(iter(storage))]['geometry'] = [SimpleNamespace(geo=None)]
    stand_in = gerber_stand_in(app, source, storage)

    AppGerberEditor.build_edited_gerber(stand_in, 'board', storage)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'obj' not in result and result['error']

    handlers = []
    app.connect_custom_signal = lambda target, params: handlers.append(target)
    stand_in.deactivate_grb_editor = Mock()
    stand_in.build_edited_gerber = Mock()
    on_complete = Mock()
    AppGerberEditor.update_fcgerber_in_place(stand_in, on_complete=on_complete)
    handlers[0](result)
    stand_in.deactivate_grb_editor.assert_not_called()
    on_complete.assert_not_called()
    assert app.inform.emit.call_args.args[0].startswith('[ERROR_NOTCL]')
    assert repr(sorted(source.tools)) == tools_before and source.solid_geometry is solid_before






# ---------------------------------------------------------------------------------------------------------------
# Excellon
# ---------------------------------------------------------------------------------------------------------------

def tools(*dias):
    return {i + 1: {'tooldia': dia} for i, dia in enumerate(dias)}


def test_match_tool_data_follows_a_renumbering():
    assert match_tool_data(tools(0.8, 1.0), tools(1.0, 0.8), {0.8: 0.8, 1.0: 1.0}, 4) == {1: 2, 2: 1}


def test_match_tool_data_follows_a_diameter_edit():
    assert match_tool_data(tools(0.8), tools(1.2), {0.8: 1.2}, 4) == {1: 1}








def test_excellon_in_place_apply_keeps_identity_options_and_tool_data(app):
    source = make_same_diameter_excellon(app, name='holes')
    uid = source.history_uid
    options = non_bounds_options(source)
    app.f_handlers = appIO(app)
    app.custom_signal = MagicMock()
    app.main_thread = QtCore.QThread.currentThread()
    new_tools = {
        1: {'tooldia': 1.0, 'drills': [Point(5, 5)], 'solid_geometry': []},
        2: {'tooldia': 2.5, 'drills': [Point(1, 1)], 'solid_geometry': []},
    }
    built = []

    def submit(build, commit):
        # the build runs detached from the source, the commit changes the source
        edited = build()
        built.append(edited)
        assert len(source.tools) == 2 and source.tools[1]['data']['tag'] == 'first'
        assert edited is not source
        commit(edited)

    stand_in = SimpleNamespace(app=app, edited_obj=source, edited_obj_name='holes', new_tools=new_tools,
                               olddia_newdia={1.0: 1.0}, data_defaults={'default': True}, decimals=4,
                               storage_dict={}, units=source.units.upper(), deactivate=Mock(),
                               history=SimpleNamespace(submit=submit))

    AppExcEditor._queue_excellon_save(stand_in, source, True, None)
    edited = built[0]
    assert source.history_uid == uid and source.obj_options['name'] == 'holes'
    assert non_bounds_options(source) == options
    assert set(source.tools) == {1, 2}
    assert source.tools[1]['data']['tag'] == 'first' and source.tools[1]['data']['cutz'] == -1.7
    assert source.tools[2]['data'] == {'default': True}
    assert [(p.x, p.y) for p in source.tools[1]['drills']] == [(5, 5)]
    assert len(source.solid_geometry) == 2
    assert source.source_file
    assert tuple(source.obj_options[k] for k in BOUNDS_KEYS) == pytest.approx(edited.bounds())


