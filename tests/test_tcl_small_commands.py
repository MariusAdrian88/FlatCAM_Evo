"""Small Tcl commands: help, open_gerber argument check, set_sys and save_sys."""
import os
import sys
import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from defaults import AppDefaults, AppOptions
from tclCommands.TclCommandSaveSys import TclCommandSaveSys
from tclCommands.TclCommandSetSys import TclCommandSetSys


class ShownText(HTMLParser):
    """The text of an HTML fragment as the shell shows it: the tags are dropped and the entities decoded."""

    def __init__(self, fragment):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(fragment)
        self.close()

    @property
    def text(self):
        return ''.join(self.parts)

    def handle_data(self, data):
        self.parts.append(data)


def make_set_sys_command():
    defaults = AppDefaults(beta=False)
    options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    app = SimpleNamespace(defaults=defaults, options=options)
    return SimpleNamespace(app=app, raise_tcl_error=Mock()), defaults, options


class TestTclSetSys(unittest.TestCase):
    def test_application_key_goes_to_defaults_and_propagates(self):
        command, defaults, _options = make_set_sys_command()
        with patch.object(AppDefaults, 'propagate_defaults') as propagate:
            TclCommandSetSys.execute(command, {'name': 'global_gridx', 'value': 2.5}, [])
        self.assertEqual(defaults['global_gridx'], 2.5)
        propagate.assert_called_once_with()
        command.raise_tcl_error.assert_not_called()

    def test_project_key_goes_to_project_and_converts_tcl_words(self):
        command, defaults, options = make_set_sys_command()
        before = defaults['units']
        TclCommandSetSys.execute(command, {'name': 'units', 'value': 'in' if before == 'MM' else 'mm'}, [])
        self.assertEqual(options['units'], 'IN' if before == 'MM' else 'MM')
        self.assertEqual(defaults['units'], before)
        command.raise_tcl_error.assert_not_called()

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


def test_check_rules_writes_csv_and_a_document_without_plotting(tmp_path):
    import csv
    command, app = make_check_rules(rules_objects())
    path = tmp_path / 'report.csv'
    command.execute({'copper_top': 'top.gtl', 'c2c': 0.2, 'csv': str(path), 'outname': 'drc'}, [])
    with open(path, newline='', encoding='utf-8') as f:
        rows = list(csv.reader(f))
    assert rows[0] == ['rule', 'layer', 'x', 'y', 'value', 'limit', 'status']
    assert len(rows) == 2 and rows[1][-1] == 'FAILED' and abs(float(rows[1][4]) - 0.1) < 1e-6
    args, kwargs = app.app_obj.new_object.call_args
    assert args[0] == 'document' and args[1] == 'drc' and kwargs['plot'] is False
    doc = SimpleNamespace()
    args[2](doc, app)
    assert 'FAILED' in doc.source_file


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


def test_qrcode_source_extends_follow_geometry_and_pcbdoc_uses_object_units():
    from shapely import Point
    from shapely.geometry import MultiPoint
    obj = qr_gerber()
    obj.follow_geometry = MultiPoint([(1, 1), (2, 2)])
    command, _app = make_qrcode('IN', obj)
    command.execute({'name': 'board', 'data': 'hi', 'x': 10, 'y': 10, 'version': 1, 'box_size': 10,
                     'border': 0, 'polarity': 'pos'}, [])
    assert isinstance(obj.follow_geometry, list) and len(obj.follow_geometry) > 2
    assert all(isinstance(p, Point) for p in obj.follow_geometry)
    in_width = max(p.x for p in obj.follow_geometry[2:]) - 10
    obj = qr_gerber()
    obj.follow_geometry = []
    obj.obj_options['pcbdoc_import'] = True
    obj.units = 'mm'
    command, _app = make_qrcode('IN', obj)
    command.execute({'name': 'board', 'data': 'hi', 'x': 10, 'y': 10, 'version': 1, 'box_size': 10,
                     'border': 0, 'polarity': 'pos'}, [])
    mm_width = max(g.bounds[2] for g in obj.solid_geometry[1:]) - 10
    assert in_width < 1.5 < mm_width  # 21 modules: 0.83 in in an inch project, 21 mm in a mm PCB document


def test_qrcode_source_cuts_copper_and_adds_the_code():
    from shapely.ops import unary_union
    obj = qr_gerber()
    command, _app = make_qrcode(obj=obj)
    before = unary_union(obj.solid_geometry).area
    command.execute({'name': 'board', 'data': 'hello', 'x': 10, 'y': 10, 'version': 0, 'polarity': 'neg'}, [])
    assert unary_union(obj.solid_geometry).area < before
    assert len(obj.solid_geometry) > 1
    obj.regenerate_source.assert_called_once()


def test_qrcode_overflow_and_bad_input_are_tcl_errors_without_change():
    from test_tcl_audit_regressions import FCShell
    obj = qr_gerber()
    geometry, tools = list(obj.solid_geometry), obj.tools
    command, _app = make_qrcode(obj=obj)
    for args in ({'name': 'board', 'data': 'x' * 500, 'version': 1},
                 {'name': 'board', 'data': 'x', 'rotation': 45},
                 {'name': 'board', 'data': 'x', 'anchor': 'zz'},
                 {'name': 'board', 'data': 'x', 'output': 'nowhere'}):
        with pytest.raises(FCShell.TclErrorException):
            command.execute(args, [])
    assert obj.solid_geometry == geometry and obj.tools is tools
    obj.regenerate_source.assert_not_called()
    command, _app = make_qrcode(obj=None)
    with pytest.raises(FCShell.TclErrorException):
        command.execute({'name': 'nope', 'data': 'x'}, [])
    obj.kind = 'excellon'
    command, _app = make_qrcode(obj=obj)
    with pytest.raises(FCShell.TclErrorException):
        command.execute({'name': 'board', 'data': 'x'}, [])


def test_qrcode_new_object_holds_only_the_code_and_does_not_plot():
    command, app = make_qrcode()
    command.execute({'data': 'hello', 'output': 'new', 'outname': 'qr_label', 'x': 5, 'y': 5, 'version': 1}, [])
    args, kwargs = app.app_obj.new_object.call_args
    assert args[0] == 'gerber' and args[1] == 'qr_label' and kwargs['plot'] is False
    new = Mock()
    new.obj_options = {}
    new.bounds.return_value = (5, 5, 30, 30)
    args[2](new, app)
    assert len(new.solid_geometry) > 1 and 10 in new.tools
    new.regenerate_source.assert_called_once()


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


def test_subtract_objects_takes_several_subtractors_including_an_excellon():
    from shapely import box
    from shapely.ops import unary_union
    objects = {'cu': sub_gerber('cu', box(0, 0, 10, 10)), 'a': sub_gerber('a', box(0, 0, 2, 10)),
               'drl': sub_excellon('drl', 8, 5)}
    command, app = make_subtract(objects)
    command.execute({'target': 'cu', 'subtractors': 'a,drl', 'outname': 'cut'}, [])
    name, new = run_init(app)
    assert name == 'cut'
    area = unary_union(new.solid_geometry).area
    assert 80 - 0.9 < area < 80 - 0.7  # minus the strip and the 1 mm hole (~0.78)


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

