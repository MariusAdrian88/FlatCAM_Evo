"""Headless regressions for Tcl validation and command completion contracts."""
import collections
import time
import tkinter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets

from appPlugins.ToolShell import FCShell
from tclCommands.TclCommand import TclCommand, TclCommandSignaled
from tclCommands.TclCommandAddAperture import TclCommandAddAperture
from tclCommands.TclCommandAddPolygon import TclCommandAddPolygon
from tclCommands.TclCommandAddPolyline import TclCommandAddPolyline
from tclCommands.TclCommandAddCircle import TclCommandAddCircle
from tclCommands.TclCommandAddRectangle import TclCommandAddRectangle
from tclCommands.TclCommandSetSys import TclCommandSetSys
from tclCommands.TclCommandSetPath import TclCommandSetPath
from tclCommands.TclCommandGetActive import TclCommandGetActive
from tclCommands.TclCommandSaveProject import TclCommandSaveProject
from tclCommands.TclCommandSaveSys import TclCommandSaveSys
from tclCommands.TclCommandVersion import TclCommandVersion
from tclCommands.TclCommandQuit import TclCommandQuit


class Signals(QtCore.QObject):
    shell_command_finished = QtCore.pyqtSignal(object)
    thread_exception = QtCore.pyqtSignal(object)
    worker_task = QtCore.pyqtSignal(object)


def new_tcl(attempts=5):
    """tkinter.Tcl(), tried again: under the load of the parallel workers Windows can fail the read of init.tcl
    ("couldn't read file ... init.tcl: No error"); the next try reads it."""
    for attempt in range(attempts):
        try:
            return tkinter.Tcl()
        except tkinter.TclError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


# every stub app stays alive until the end: app and app.shell are a reference cycle, and a garbage collection of an
# old Signals while a new one is in use gives "wrapped C/C++ object of type Signals has been deleted" under xdist
KEEP_ALIVE = []


def make_app():
    app = Signals()
    KEEP_ALIVE.append(app)
    app.log = Mock()
    # a long default: a busy machine (parallel test workers) delays the queued work; the timeout tests pass -timeout
    app.options = {'global_background_timeout': 5000, 'global_verbose_error_level': 0}
    shell = SimpleNamespace(app=app, tcl=new_tcl(), TclErrorException=FCShell.TclErrorException,
                            open_processing=Mock())
    shell.raise_tcl_error = lambda text: FCShell.raise_tcl_error(shell, text)
    shell.display_tcl_error = lambda error, info: FCShell.display_tcl_error(shell, error, info)
    app.shell = shell
    return app


class Command(TclCommand):
    aliases = ['test_command']
    required = []
    arg_names = collections.OrderedDict()
    option_types = {'number': float, 'dpp': float, 'all': str}

    def execute(self, args, unnamed_args):
        return args.get('number', 'ordinary text')


class BackgroundCommand(TclCommandSignaled, Command):
    def execute(self, args, unnamed_args):
        return args.get('number', 'ordinary text')


@pytest.mark.parametrize('command_name, keys', [
    ('Delete', ('f',)),
    ('CloseProject', ('force',)),
    ('PlotAll', ('plot_status',)),
    ('PlotObjects', ('plot_status',)),
    ('Paint', ('all',)),
    ('CopperClear', ('all',)),
    ('Cncjob', ('dpp', 'toolchangez', 'dwelltime', 'extracut_length', 'startz', 'spindlespeed')),
    ('Drillcncjob', ('dpp', 'toolchangez', 'dwelltime', 'drillz', 'startz', 'endz')),
])
def test_actual_commands_preserve_supported_bare_options(command_name, keys):
    from importlib import import_module

    cls = getattr(import_module('tclCommands.TclCommand' + command_name), 'TclCommand' + command_name)
    command = cls(make_app())
    help_text = command.get_decorated_help()
    usage = next(line for line in help_text.splitlines() if line.startswith('> '))
    for key, value_type in command.option_types.items():
        if key not in command.help['args']:
            continue
        value = '<%s>' % value_type.__name__
        if key in keys:
            value = '[' + value + ']'
        assert '[-%s %s]' % (key, value) in usage
        assert '[-%s %s:' % (key, value) in help_text
    positional = tuple('object' for key in command.required)
    for key in keys:
        args, extra = command.check_args(positional + ('-' + key,))
        assert args[key] is None
        assert extra == []
        args, _ = command.check_args(positional + ('-' + key, '1'))
        assert args[key] == command.option_types[key]('1')
    for key in command.option_types.keys() - set(keys):
        with pytest.raises(FCShell.TclErrorException, match='Missing option value'):
            command.check_args(positional + ('-' + key,))


def test_real_tcl_catch_preserves_literal_error_message():
    app = make_app()
    command = Command(app)
    app.shell.tcl.createcommand('test_command', command.execute_wrapper)
    assert app.shell.tcl.eval('catch {test_command -number} message') == '1'
    assert 'number' in app.shell.tcl.eval('set message')
    text = 'bad [value] $variable; "quoted" \\path\nnext'
    app.shell.tcl.createcommand('bad', lambda: app.shell.display_tcl_error(text, None))
    assert app.shell.tcl.eval('catch {bad} message') == '1'
    assert app.shell.tcl.eval('set message') == text
    assert app.shell.tcl.eval('test_command') == 'ordinary text'


@pytest.fixture
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_script_timeout_blocks_new_execution_until_late_work_finishes(qt_app):
    from appMain import App
    app = make_app()
    app.ui = SimpleNamespace(shell_dock=Mock())
    app.inform = Mock()
    app.shell.close_processing = Mock()
    app.shell.append_output = Mock()
    app.shell.append_error = Mock()
    command = BackgroundCommand(app)
    pending = []
    app.worker_task.connect(pending.append)
    app.shell.tcl.createcommand('background', command.execute_wrapper)
    assert App.script_processing(app, 'background -timeout 1') == 'fail'
    assert app.script_state == 'timed_out_work'
    from appHandlers.appIO import appIO
    io = SimpleNamespace(app=app, log=app.log, inform=app.inform)
    assert appIO.on_file_new_project(io) == 'fail'
    assert App.install_tools(app, init_tcl=True) == 'fail'
    assert App.script_processing(app, 'set changed 1') == 'fail'
    assert app.shell.tcl.eval('info exists changed') == '0'
    pending[0]['fcn'](*pending[0]['params'])
    assert App.script_processing(app, 'set changed 1') == '1'
    assert app.script_state == 'completed'


def object_app(obj):
    app = make_app()
    app.collection = Mock()
    app.collection.get_by_name.return_value = obj
    app.collection.get_active.return_value = obj
    app.inform_shell = Mock()
    app.inform = Mock()
    return app


def test_aperture_ids_are_integer_unique_and_start_at_ten():
    obj = SimpleNamespace(kind='gerber', tools={})
    command = TclCommandAddAperture(object_app(obj))
    command.execute({'name': 'g'}, [])
    assert 10 in obj.tools
    command.execute({'name': 'g'}, [])
    assert 11 in obj.tools
    with pytest.raises(FCShell.TclErrorException):
        command.execute({'name': 'g', 'apid': 10}, [])
    with pytest.raises(FCShell.TclErrorException):
        command.execute({'name': 'g', 'type': 'R', 'width': 1, 'height': 0}, [])


@pytest.mark.parametrize('cls,args,extra', [
    (TclCommandAddPolyline, {'name': 'g'}, []),
    (TclCommandAddCircle, {'name': 'g', 'center_x': 0, 'center_y': 0, 'radius': -1}, []),
    (TclCommandAddCircle, {'name': 'g', 'center_x': 0, 'center_y': 0, 'radius': 1}, ['extra']),
    (TclCommandAddRectangle, {'name': 'g', 'x0': 0, 'y0': 0, 'x1': 0, 'y1': 1}, [])])
def test_invalid_shapes_do_not_mutate(cls, args, extra):
    obj = Mock(kind='geometry')
    with pytest.raises(FCShell.TclErrorException):
        cls(object_app(obj)).execute(args, extra)
    obj.add_polygon.assert_not_called()
    obj.add_polyline.assert_not_called()
    obj.add_circle.assert_not_called()


def test_real_worker_validation_exception_becomes_tcl_error_without_excepthook(qt_app):
    class FailingCommand(BackgroundCommand):
        def execute(self, args, unnamed_args):
            self.raise_tcl_error('worker validation failed')

    class Worker(QtCore.QObject):
        @QtCore.pyqtSlot(object)
        def run(self, task):
            task['fcn'](*task['params'])

    app = make_app()
    thread = QtCore.QThread()
    worker = Worker()
    worker.moveToThread(thread)
    thread.finished.connect(worker.deleteLater)
    app.worker_task.connect(worker.run)
    app.shell.tcl.createcommand('background', FailingCommand(app).execute_wrapper)
    thread.start()
    try:
        from unittest.mock import patch
        with patch('sys.excepthook') as hook:
            assert app.shell.tcl.eval('catch {background -timeout 1000} message') == '1'
            assert app.shell.tcl.eval('set message') == 'worker validation failed'
            hook.assert_not_called()
        assert app.receivers(app.shell_command_finished) == 0
    finally:
        thread.quit()
        assert thread.wait(3000)


class FakeCollection(QtCore.QObject):
    """A collection that, like the real one, takes the objects of a worker by a queued signal."""
    created = QtCore.pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.objects = []
        self.created.connect(self.objects.append)

    def get_list(self):
        return list(self.objects)


class MakeObjects(TclCommand):
    aliases = ['make_objects']
    required = []
    arg_names = collections.OrderedDict()
    option_types = {}

    def execute(self, args, unnamed_args):
        for name in self.names:
            self.app.collection.created.emit(SimpleNamespace(obj_options={'name': name}))


class MakeObjectsSignaled(TclCommandSignaled, MakeObjects):
    aliases = ['make_objects_async']

    def execute(self, args, unnamed_args):
        return MakeObjects.execute(self, args, unnamed_args)


class Worker(QtCore.QObject):
    @QtCore.pyqtSlot(object)
    def run(self, task):
        task['fcn'](*task['params'])


@pytest.mark.parametrize('names', [[], ['paint_1'], ['with space', 'plain'], ['fail'], ['failsafe_iso']])
@pytest.mark.parametrize('use_worker', [False, True])
def test_a_command_returns_the_names_of_the_objects_it_created(qt_app, names, use_worker):
    app = make_app()
    app.collection = FakeCollection()
    app.collection.objects.append(SimpleNamespace(obj_options={'name': 'old'}))
    app.inform = Mock()
    command = (MakeObjectsSignaled if use_worker else MakeObjects)(app)
    command.names = names
    thread = QtCore.QThread()
    worker = Worker()
    if use_worker:
        worker.moveToThread(thread)
        app.worker_task.connect(worker.run)
        thread.start()
    app.shell.tcl.createcommand('make', command.execute_wrapper)
    try:
        # a script chains the result: the names come back as a proper Tcl list
        length = app.shell.tcl.eval('set g [make]; llength $g')
        assert length == (str(len(names)) if names else '1')  # no object: None, as before
        assert [app.shell.tcl.eval('lindex $g %d' % i) for i in range(len(names))] == names
    finally:
        thread.quit()
        assert thread.wait(3000)
    warned = [call.args[0] for call in app.inform.emit.call_args_list]
    assert bool(warned) == (names == ['fail'])


def test_a_command_with_returns_value_or_its_own_result_keeps_it():
    app = make_app()
    app.collection = FakeCollection()
    app.inform = Mock()

    class Keeps(MakeObjects):
        def execute(self, args, unnamed_args):
            super().execute(args, unnamed_args)
            return 'mine'

    command = Keeps(app)
    command.names = ['x']
    assert command.execute_wrapper() == 'mine'



def test_a_command_warns_when_the_name_of_its_new_object_was_in_use(qt_app):
    """A script run again in the same session: the collection renames the new object (top_iso_1), and the next commands
    that use the name get the object of the first run. The command says so."""
    from appObjects.ObjectCollection import ObjectCollection
    app = make_app()
    app.inform = Mock()
    app.collection = ObjectCollection(Mock(resource_location='.', options={
        'global_allow_edit_in_project_tab': False,
        'global_proj_item_color_dark': '#FFFFFFFF', 'global_proj_item_dis_color_dark': '#808080FF',
        'global_proj_item_color_light': '#000000FF', 'global_proj_item_dis_color_light': '#808080FF'}))

    class Isolate(MakeObjects):
        def execute(self, args, unnamed_args):
            for name in self.names:
                self.app.collection.append(SimpleNamespace(kind='geometry', obj_options={'name': name, 'plot': True},
                                                           ui_type=Mock(), set_ui=Mock(), build_ui=Mock()))

    command = Isolate(app)
    command.names = ['top_iso']
    assert command.execute_wrapper() == 'top_iso'
    assert app.inform.emit.call_args_list == []

    assert command.execute_wrapper() == 'top_iso_1'
    warned, = [call.args[0] for call in app.inform.emit.call_args_list]
    assert warned.startswith('[WARNING_NOTCL]') and "'top_iso'" in warned and "'top_iso_1'" in warned
    assert app.collection.get_by_name('top_iso').requested_name is None
