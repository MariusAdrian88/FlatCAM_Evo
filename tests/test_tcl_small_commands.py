"""Small Tcl commands: save_sys, check_rules, qrcode, subtract_objects, invert_gerber and the project commands."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from defaults import AppDefaults, AppOptions
from tclCommands.TclCommandSaveSys import TclCommandSaveSys
from tclCommands.TclCommandSetSys import TclCommandSetSys


def make_set_sys_command():
    defaults = AppDefaults(beta=False)
    options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    app = SimpleNamespace(defaults=defaults, options=options)
    return SimpleNamespace(app=app, raise_tcl_error=Mock()), defaults, options


class TestTclSetSys(unittest.TestCase):
    def test_save_sys_saves_the_project_key_that_set_sys_changed(self):
        command, defaults, options = make_set_sys_command()
        other = 'IN' if defaults['units'] == 'MM' else 'MM'
        TclCommandSetSys.execute(command, {'name': 'units', 'value': other.lower()}, [])
        TclCommandSetSys.execute(command, {'name': 'global_gridx', 'value': 2.5}, [])
        written = {}
        command.app.preferencesUiManager = SimpleNamespace(
            save_defaults=lambda: written.update(units=defaults['units'], global_gridx=defaults['global_gridx']))
        TclCommandSaveSys.execute(command, {}, [])
        self.assertEqual(written, {'units': other, 'global_gridx': 2.5})

# ---- check_rules --------------------------------------------------------------------------------------------------
def make_check_rules(tmp_objects):
    from tclCommands.TclCommandCheckRules import TclCommandCheckRules
    from test_tcl_audit_regressions import object_app
    app = object_app(None)
    app.collection.get_by_name.side_effect = tmp_objects.get
    app.app_obj = Mock()
    return TclCommandCheckRules(app), app


def rules_objects():
    from shapely import Point, box
    top = SimpleNamespace(obj_options={'name': 'top.gtl'}, tools={},
                          solid_geometry=[box(0, 0, 1, 1), box(1.1, 0, 2, 1)])
    exc = SimpleNamespace(obj_options={'name': 'drl'}, solid_geometry=[],
                          tools={1: {'tooldia': 0.3, 'drills': [Point(0, 0), Point(1, 0)], 'slots': []}})
    return {'top.gtl': top, 'drl': exc}


def test_check_rules_counts_violations_per_rule():
    command, _app = make_check_rules(rules_objects())
    text = command.execute({'copper_top': 'top.gtl', 'exc_1': 'drl', 'c2c': 0.2, 'dh': 0.5, 'h2h': 0.1}, [])
    lines = text.splitlines()
    assert any('FAILED (1)' in line for line in lines if 'lear' in line and 'Copper' in line)
    assert any('FAILED (2)' in line for line in lines if 'Drill' in line)
    assert any('PASSED' in line for line in lines if 'Hole' in line)
    assert lines[-1].endswith('3')


# ---- qrcode -------------------------------------------------------------------------------------------------------
def make_qrcode(units='MM', obj=None):
    from tclCommands.TclCommandQRCode import TclCommandQRCode
    from test_tcl_audit_regressions import object_app
    app = object_app(obj)
    app.app_units = units
    app.app_obj = Mock()
    return TclCommandQRCode(app), app


def qr_gerber():
    from shapely import box
    board = box(-5, -5, 60, 60)
    obj = Mock()
    obj.kind = 'gerber'
    obj.obj_options = {'name': 'board'}
    obj.solid_geometry = [board]
    obj.tools = {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': board, 'follow': board.exterior}]}}
    obj.follow_geometry = []
    obj.bounds.return_value = (-5, -5, 60, 60)
    return obj


def test_qrcode_source_cuts_copper_and_adds_the_code():
    from shapely.ops import unary_union
    obj = qr_gerber()
    command, _app = make_qrcode(obj=obj)
    before = unary_union(obj.solid_geometry).area
    command.execute({'name': 'board', 'data': 'hello', 'x': 10, 'y': 10, 'version': 0, 'polarity': 'neg'}, [])
    assert unary_union(obj.solid_geometry).area < before
    assert len(obj.solid_geometry) > 1
    obj.regenerate_source.assert_called_once()


@pytest.mark.slow
def test_qrcode_source_insert_is_one_undo_step():
    import sys
    from PyQt6 import QtWidgets
    from shapely import box
    from shapely.ops import unary_union
    import appHandlers.appHistory as appHistory
    from appMain import App
    from test_tcl_audit_regressions import FCShell
    from tclCommands.TclCommandQRCode import TclCommandQRCode
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    fc = App(qapp=qapp, headless=True)
    appHistory.RECORD_IN_HEADLESS = True
    try:
        def init(obj, _app):
            board = box(0, 0, 50, 50)
            obj.solid_geometry = [board]
            obj.tools = {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': board, 'follow': board.exterior}]}}
        obj = fc.app_obj.new_object('gerber', 'board', init, plot=False)
        area = unary_union(obj.solid_geometry).area
        steps = len(fc.history.undo_stack)
        command = TclCommandQRCode(fc)
        command.execute({'name': 'board', 'data': 'hello', 'x': 10, 'y': 10, 'polarity': 'neg'}, [])
        assert unary_union(obj.solid_geometry).area < area
        assert len(fc.history.undo_stack) == steps + 1
        fc.history.undo()
        assert unary_union(obj.solid_geometry).area == pytest.approx(area)
    finally:
        appHistory.RECORD_IN_HEADLESS = False


# ---- subtract_objects ---------------------------------------------------------------------------------------------
def make_subtract(objects):
    from tclCommands.TclCommandSubtractObjects import TclCommandSubtractObjects
    from test_tcl_audit_regressions import object_app
    app = object_app(None)
    app.collection.get_by_name.side_effect = objects.get
    app.app_obj = Mock()
    return TclCommandSubtractObjects(app), app


def sub_gerber(name, geo):
    return SimpleNamespace(kind='gerber', multigeo=False, obj_options={'name': name, 'units': 'MM'},
                           solid_geometry=[geo],
                           tools={10: {'type': 'C', 'size': 1.0, 'geometry': [{'solid': geo, 'follow': geo.exterior}]}})


def sub_excellon(name, x, y):
    from shapely import Point
    return SimpleNamespace(kind='excellon', multigeo=False, obj_options={'name': name, 'units': 'MM'},
                           solid_geometry=[],
                           tools={1: {'tooldia': 1.0, 'drills': [Point(x, y)], 'slots': [], 'solid_geometry': []}})


def run_init(app, kind='gerber'):
    args, kwargs = app.app_obj.new_object.call_args
    assert args[0] == kind and kwargs['plot'] is False
    new = Mock()
    new.obj_options = {}
    new.bounds.return_value = (0, 0, 1, 1)
    args[2](new, app)
    return args[1], new


def test_subtract_objects_gerber_minus_gerber_creates_sub_object_with_the_cut_copper():
    from shapely import box
    from shapely.ops import unary_union
    objects = {'cu': sub_gerber('cu', box(0, 0, 10, 10)), 'mask': sub_gerber('mask', box(5, -1, 11, 11))}
    command, app = make_subtract(objects)
    command.execute({'target': 'cu', 'subtractors': 'mask'}, [])
    name, new = run_init(app)
    assert name == 'cu_sub'
    assert unary_union(new.solid_geometry).area == pytest.approx(50)
    new.regenerate_source.assert_called_once()
    assert objects['cu'].solid_geometry[0].area == 100  # the target is untouched


def test_subtract_objects_bad_input_is_a_tcl_error_and_creates_nothing():
    from shapely import box
    from test_tcl_audit_regressions import FCShell
    objects = {'cu': sub_gerber('cu', box(0, 0, 10, 10)), 'mask': sub_gerber('mask', box(5, 0, 11, 10)),
               'drl': sub_excellon('drl', 1, 1), 'far': sub_gerber('far', box(50, 50, 51, 51)),
               'nothing': SimpleNamespace(kind='excellon', multigeo=False, obj_options={'name': 'nothing'},
                                          solid_geometry=[], tools={})}
    command, app = make_subtract(objects)
    for args in ({'target': 'cu', 'subtractors': 'cu,mask'},
                 {'target': 'cu', 'subtractors': 'nope'},
                 {'target': 'nope', 'subtractors': 'mask'},
                 {'target': 'drl', 'subtractors': 'mask', 'op': 'xor'},
                 {'target': 'cu', 'subtractors': 'mask', 'op': 'merge'},
                 {'target': 'cu', 'subtractors': 'mask', 'target_tools': '99'},
                 {'target': 'cu', 'subtractors': 'nothing'}):
        with pytest.raises(FCShell.TclErrorException):
            command.execute(args, [])
    app.app_obj.new_object.assert_not_called()
    app.collection.delete_by_name.assert_not_called()


# ---- invert_gerber ------------------------------------------------------------------------------------------------
def make_invert(objects):
    from tclCommands.TclCommandInvertGerber import TclCommandInvertGerber
    from test_tcl_audit_regressions import object_app
    app = object_app(None)
    app.collection.get_by_name.side_effect = objects.get
    app.app_obj = Mock()
    return TclCommandInvertGerber(app), app


def invert_source(name, copper, bounds):
    return SimpleNamespace(kind='gerber', multigeo=False, obj_options={'name': name, 'units': 'MM'},
                           solid_geometry=[copper], bounds=lambda: bounds)


def test_invert_gerber_new_object_is_the_margin_box_minus_the_copper():
    from shapely import box
    from shapely.ops import unary_union
    objects = {'board': invert_source('board', box(2, 2, 8, 8), (0, 0, 10, 10))}
    command, app = make_invert(objects)
    command.execute({'name': 'board', 'margin': 1, 'join': 's'}, [])
    name, new = run_init(app)
    assert name == 'board_inverted'
    assert unary_union(new.solid_geometry).area == pytest.approx(144 - 36)  # 12 x 12 box minus 6 x 6 copper
    new.regenerate_source.assert_called_once()
    assert objects['board'].solid_geometry[0].area == 36  # the source is untouched


# ---- the project commands (Project tab) ---------------------------------------------------------------------------
class TclError(Exception):
    pass


def make_projects_app():
    first = SimpleNamespace(name='Board_A')
    second = SimpleNamespace(name='Board_B')
    contexts = [first, second]
    projects = SimpleNamespace(
        active=first, names=Mock(return_value=['Board_A', 'Board_B']),
        get_by_name=Mock(side_effect=lambda name: next((c for c in contexts if c.name == name), None)),
        switch=Mock(return_value=True), close=Mock(return_value=True), is_dirty=Mock(return_value=False))

    def raise_tcl_error(text):
        raise TclError(text)

    return SimpleNamespace(log=Mock(), projects=projects, shell=SimpleNamespace(raise_tcl_error=raise_tcl_error))


def test_list_and_select_project():
    from tclCommands.TclCommandListProjects import TclCommandListProjects
    from tclCommands.TclCommandSelectProject import TclCommandSelectProject
    app = make_projects_app()
    assert TclCommandListProjects(app).execute({}, []) == 'Board_A\nBoard_B'
    TclCommandSelectProject(app).execute({'name': 'Board_B'}, [])
    app.projects.switch.assert_called_once_with(app.projects.get_by_name('Board_B'))
    app.projects.switch.reset_mock()
    with pytest.raises(TclError):
        TclCommandSelectProject(app).execute({'name': 'Missing'}, [])
    app.projects.switch.assert_not_called()


def test_close_project_by_default_and_by_name():
    from tclCommands.TclCommandCloseProject import TclCommandCloseProject
    app = make_projects_app()
    TclCommandCloseProject(app).execute({}, [])
    app.projects.close.assert_called_once_with(app.projects.active, force=True)
    app.projects.close.reset_mock()
    TclCommandCloseProject(app).execute({'name': 'Board_B'}, [])
    app.projects.close.assert_called_once_with(app.projects.get_by_name('Board_B'), force=True)


def test_dirty_project_is_closed_only_with_force():
    from tclCommands.TclCommandCloseProject import TclCommandCloseProject
    app = make_projects_app()
    app.projects.is_dirty.return_value = True
    with pytest.raises(TclError):
        TclCommandCloseProject(app).execute({}, [])
    app.projects.close.assert_not_called()
    for value in ('1', 'True', 'true', None):
        app.projects.close.reset_mock()
        TclCommandCloseProject(app).execute({'force': value}, [])
        app.projects.close.assert_called_once_with(app.projects.active, force=True)


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot, connection_type=None):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


def make_open_app(result, delay=0.0):
    import threading
    app = make_projects_app()
    app.options = {}
    app.defaults = {}
    signal = FakeSignal()

    def open_project(filename, **kwargs):
        # the load ends later, on another thread
        threading.Timer(delay, lambda: signal.emit(*result)).start()
        return True

    app.f_handlers = SimpleNamespace(project_load_done=signal, open_project=open_project)
    return app, signal


def test_open_project_failed_and_partial_load_tell_the_user():
    from tclCommands.TclCommandOpenProject import TclCommandOpenProject
    app, _signal = make_open_app((False, 'a.FlatPrj', []), 0.01)
    app.f_handlers.last_load_error = 'File no longer available.'
    with pytest.raises(TclError) as ctx:
        TclCommandOpenProject(app).execute({'filename': 'a.FlatPrj'}, [])
    assert 'File no longer available.' in str(ctx.value)

    app, signal = make_open_app((True, 'a.FlatPrj', ['Bad_1', 'Bad_2']), 0.05)
    out = TclCommandOpenProject(app).execute({'filename': 'a.FlatPrj'}, [])
    assert signal.slots == []
    assert 'Bad_1' in str(out) and 'Bad_2' in str(out)

