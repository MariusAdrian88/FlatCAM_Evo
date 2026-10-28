"""HeadlessRunner on a real Tcl interpreter and a fake app."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

from appCommon.Headless import HeadlessExit, HeadlessRunner
from tests.test_tcl_audit_regressions import new_tcl


def _app(**options):
    tcl = new_tcl()
    app = SimpleNamespace(
        shell=SimpleNamespace(tcl=tcl), headless_console=False, headless_exit_code=None,
        cmd_line_shellfile=options.get('shellfile', ''), cmd_line_command=options.get('command', ''),
        shellvar_commands=lambda: options.get('shellvar', []),
        workers=Mock(pending_count=Mock(return_value=0)), pool=Mock(),
    )
    return app


def _run(app, stdin=''):
    out, err = io.StringIO(), io.StringIO()
    code = HeadlessRunner(app, stdin=io.StringIO(stdin), out=out, err=err).run()
    return code, out.getvalue(), err.getvalue()


def test_a_command_runs_and_its_result_is_written():
    code, out, err = _run(_app(command='set a 5; expr {$a * 2}'))
    assert (code, out, err) == (0, '10\n', '')


def test_a_tcl_error_gives_exit_code_1_and_the_report_on_stderr():
    code, out, err = _run(_app(command='no_such_command'))
    assert code == 1
    assert 'invalid command name "no_such_command"' in err


def test_exit_n_gives_exit_code_n_and_stops_the_script():
    code, out, err = _run(_app(command='exit 3; set never 1'))
    assert code == 3
    assert err == ''


def test_quit_app_raising_headless_exit_in_a_callback_ends_the_run():
    app = _app(command='fake_quit; set never 1')

    def fake_quit():
        app.headless_exit_code = 0
        raise HeadlessExit(0)

    app.shell.tcl.createcommand('fake_quit', fake_quit)
    code, out, err = _run(app)
    assert code == 0
    assert app.shell.tcl.eval('info exists never') == '0'


def test_the_prompt_reads_complete_commands_from_stdin():
    stdin = 'set x {\n1 2\n}\nllength $x\nno_such_command\nset y 1\n'
    code, out, err = _run(_app(), stdin=stdin)
    assert '2\n' in out
    assert 'invalid command name' in err
    assert code == 1   # a command failed; the prompt went on with the next one


def test_shellfile_accepts_utf8_bom(tmp_path):
    path = tmp_path / 'unicode.tcl'
    path.write_text('set origin [info script]; set text {café}; string length $text', encoding='utf-8-sig')
    app = _app(shellfile=str(path))
    assert _run(app) == (0, '4\n', '')
    assert app.shell.tcl.eval('set origin') == str(path)
    assert app.shell.tcl.eval('info script') == ''


def test_headless_preflight_does_not_partially_execute_incomplete_input():
    app = _app(command='set changed 1\nset unfinished {')
    code, out, err = _run(app)
    assert code == 1
    assert 'incomplete' in err.lower()
    assert app.shell.tcl.eval('info exists changed') == '0'


def test_puts_preserves_procedure_loop_channel_and_nonewline_output(tmp_path):
    path = tmp_path / 'channel.txt'
    app = _app(command='''
        proc say {} { puts -nonewline stdout {<one>}; puts stderr warning }
        foreach item {a b} { say }
        set f [open {%s} w]; puts $f channel; close $f
    ''' % path.as_posix())
    code, out, err = _run(app)
    assert (code, out, err) == (0, '<one><one>', 'warning\nwarning\n')
    assert path.read_text() == 'channel\n'


def _gui_script_app():
    from unittest.mock import create_autospec
    from appCommon.Common import AppLogging
    app = _app()
    app.ui = SimpleNamespace(shell_dock=Mock())
    app.shell.open_processing = Mock()
    app.shell.close_processing = Mock()
    app.shell.append_output = Mock()
    app.shell.append_error = Mock()
    app.shell.flush_tcl_output = Mock()
    app.log = create_autospec(AppLogging, instance=True)
    app.inform = Mock()
    return app


def test_gui_runtime_error_stops_without_repeating_side_effects():
    from appMain import App
    app = _gui_script_app()
    app.shell.tcl.eval('set count 0')
    assert App.script_processing(app, 'incr count; error broken\nincr count') == 'fail'
    assert app.shell.tcl.eval('set count') == '1'
    assert app.script_state == 'failed'
    assert app.script_error_line == 1
    assert App.script_processing(app, 'set changed 1\nset incomplete {') == 'fail'
    assert app.shell.tcl.eval('info exists changed') == '0'


def test_gui_source_is_unchanged_and_fail_is_an_ordinary_result():
    from appMain import App
    app = _gui_script_app()
    text = 'set path {C:\\new\\open}\nset value {a\n\n#literal\nb}\nset result fail'
    assert App.script_processing(app, text) == 'fail'
    assert app.shell.tcl.eval('set path') == 'C:\\new\\open'
    assert app.shell.tcl.eval('set value') == 'a\n\n#literal\nb'
    assert app.script_state == 'completed'
    app.shell.append_error.assert_not_called()


def test_nested_script_and_reset_are_refused_during_a_qt_event_loop():
    from PyQt6 import QtCore, QtWidgets
    from appMain import App
    from appHandlers.appIO import appIO
    from appPlugins.ToolShell import FCShell
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = _gui_script_app()
    app.shell.app = app
    results = []

    def wait():
        loop = QtCore.QEventLoop()

        def nested():
            work = {'finished': False}
            app.script_pending_work = [work]
            results.append(App.script_processing(app, 'set nested 1'))
            io = SimpleNamespace(app=app, log=app.log, inform=app.inform)
            results.append(appIO.on_file_new_project(io))
            try:
                results.append(App.install_tools(app, init_tcl=True))
            except Exception as error:
                results.append(error)
            work['finished'] = True
            results.append(FCShell.exec_command_test(app.shell, 'set nested 1', reraise=False))
            loop.quit()

        QtCore.QTimer.singleShot(0, nested)
        loop.exec()
        return ''

    app.shell.tcl.createcommand('wait_for_work', wait)
    App.script_processing(app, 'wait_for_work; set outer done')
    assert results == ['fail', 'fail', 'fail', 'fail']
    assert app.shell.tcl.eval('info exists nested') == '0'
    assert app.shell.tcl.eval('set outer') == 'done'



def test_script_cannot_execute_against_another_active_project():
    from appMain import App
    app = _gui_script_app()
    app.collection = object()
    owner = SimpleNamespace(_script_collection=object())
    assert App.script_processing(app, 'set wrong_project 1', owner=owner) == 'fail'
    assert app.shell.tcl.eval('info exists wrong_project') == '0'


def test_abort_with_nothing_running_does_not_cancel_the_next_task():
    from appMain import App
    app = SimpleNamespace(abort_flag=False, script_interrupt=None, inform=Mock(), cleanup=Mock(),
                          proc_container=Mock(get_procs_count=Mock(return_value=0)),
                          workers=Mock(pending_count=Mock(return_value=0)))
    App.abort_all_tasks(app)    # Ctrl+Alt+X while idle
    assert app.abort_flag is False
    app.cleanup.emit.assert_called_once_with()  # the plugins are still reset (the G-code sender disconnects)
    # a running process is aborted, and its end clears the flag
    app.proc_container.get_procs_count.return_value = 1
    App.abort_all_tasks(app)
    assert app.abort_flag is True
    App.app_is_idle(app)
    assert app.abort_flag is False


def _native_interrupt_child():
    import time
    import threading
    from PyQt6 import QtCore, QtWidgets
    from appMain import App
    from appPlugins.ToolShell import FCShell
    from appHandlers.appIO import appIO
    from appHandlers.appLifecycle import AppLifecycle
    from appHandlers.appProjects import ProjectManager
    from appObjects.ObjectCollection import ObjectCollection
    from appHandlers.appObjectOps import AppObjectOps

    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = _gui_script_app()
    app.shell.app = app
    app.abort_flag = False
    app.cleanup = Mock()
    app.headless = False
    app.collection = object()
    app.shell._tcl_output = []
    app.shell._tcl_output_size = 0
    app.shell._tcl_output_channel = 'stdout'
    app.shell.flush_tcl_output = lambda: FCShell.flush_tcl_output(app.shell)
    from appCommon.Headless import install_tcl_puts
    install_tcl_puts(app.shell.tcl, lambda channel, text: FCShell.write_tcl_output(app.shell, channel, text))
    output = []
    app.shell.append_output.side_effect = lambda text: output.append(text)
    ticks = []
    timer = QtCore.QTimer()
    timer.setInterval(80)

    def interrupt():
        ticks.append(time.monotonic())
        # Tcl reentry is legal only through the registered Python-command trampoline.
        assert app.shell.tcl.eval('expr {6 * 7}') == '42'
        assert App.script_processing(app, 'set nested 1') == 'fail'
        assert FCShell.exec_command_test(app.shell, 'set nested 1', reraise=False) == 'fail'
        io_handler = SimpleNamespace(app=app, log=app.log, inform=app.inform)
        assert appIO.on_file_new_project(io_handler) == 'fail'
        assert appIO.on_file_new_project(io_handler, reset_tcl=False) == 'fail'
        assert App.install_tools(app, init_tcl=True) == 'fail'
        assert ProjectManager.is_busy(SimpleNamespace(app=app)) is True
        lifecycle = SimpleNamespace(app=app, inform=app.inform)
        assert AppLifecycle.quit_application(lifecycle) is False
        assert AppLifecycle.final_save(lifecycle) is False
        assert AppLifecycle.on_app_restart(lifecycle) is False
        collection = SimpleNamespace(app=app)
        assert ObjectCollection.delete_active(collection) == 'fail'
        assert ObjectCollection.delete_all(collection) == 'fail'
        assert ObjectCollection.delete_objects(collection, []) == 'fail'
        assert ObjectCollection.delete_by_name(collection, 'board') == 'fail'
        operations = SimpleNamespace(app=app, inform=app.inform)
        assert AppObjectOps.on_delete(operations) == 'fail'
        assert AppObjectOps.delete_objects_now(operations, []) == 'fail'
        assert AppObjectOps.delete_first_selected(operations) == 'fail'
        App.abort_all_tasks(app)
        if len(ticks) % 2:
            App.app_is_idle(app)  # Worker-idle must not lose an outstanding Tcl cancellation.

    timer.timeout.connect(interrupt)
    for run in (lambda text: App.script_processing(app, text),
                lambda text: FCShell.exec_command_test(app.shell, text, reraise=False)):
        for script in ('while {1} {}', 'catch {while {1} {}}; set escaped 1',
                       'while {1} {catch {while {1} {}}}'):
            start = time.monotonic()
            output.clear()
            timer.start()
            run('puts -nonewline before; ' + script)
            timer.stop()
            assert time.monotonic() - start < 3
            assert app.script_state == 'cancelled'
            assert app.abort_flag is False
            assert output == ['before']
            assert app.shell.tcl.eval('info exists escaped') == '0'
            assert app.shell.tcl.eval('info exists nested') == '0'
            assert app.shell.tcl.eval('info script') == ''
            assert run('return {still usable}') == 'still usable'
            assert app.script_state == 'completed'
            run('return -code error broken')
            assert app.script_state == 'failed'
            assert run('expr {20 + 22}') == '42'
        # Empty bytecode loops must pump events without cancellation too.
        heartbeat = QtCore.QTimer()
        heartbeat.setInterval(10)
        pulses = []
        heartbeat.timeout.connect(lambda: pulses.append(time.monotonic()))
        heartbeat.start()
        assert run('set end [expr {[clock milliseconds] + 100}]; '
                   'while {[clock milliseconds] < $end} {}; set done yes') == 'yes'
        heartbeat.stop()
        assert len(pulses) >= 3
        result = run('''
            proc preserve_error {} {
                set end [expr {[clock milliseconds] + 80}]
                while {[clock milliseconds] < $end} {}
                return -code error -errorcode {FLATCAM TEST} {preserved error}
            }
            catch {preserve_error} message options
            list $message [dict get $options -errorcode]
        ''')
        assert app.shell.tcl.splitlist(result) == ('preserved error', 'FLATCAM TEST')
        assert app.script_state == 'completed'
        assert app.shell.tcl.eval('info commands ::flatcam_async_*') == ''
        assert not any(thread.name == 'Tcl interrupt marker' for thread in threading.enumerate())
        assert not app._script_event_pump
        assert app.script_interrupt is None
    assert len(ticks) >= 4
    work = {'finished': False}

    def cancel_with_pending_work():
        app.script_pending_work = [work]
        app.workers.pending_count.return_value = 1
        App.abort_all_tasks(app)

    QtCore.QTimer.singleShot(80, cancel_with_pending_work)
    assert App.script_processing(app, 'while {1} {}') == 'fail'
    assert app.script_state == 'timed_out_work'
    assert app.abort_flag is True
    assert App.script_processing(app, 'set unsafe 1') == 'fail'
    assert app.shell.tcl.eval('info exists unsafe') == '0'
    work['finished'] = True
    app.workers.pending_count.return_value = 0
    App.app_is_idle(app)
    assert App.script_processing(app, 'expr {6 * 7}') == '42'
    flush = app.shell.flush_tcl_output

    def broken_output():
        if app._script_event_pump:
            raise RuntimeError('output widget unavailable')
        flush()

    app.shell.flush_tcl_output = broken_output
    assert App.script_processing(app, 'while {1} {}') == 'fail'
    assert app.script_state == 'cancelled'
    app.shell.flush_tcl_output = flush
    assert App.script_processing(app, 'expr {6 * 7}') == '42'
    assert not any(thread.name == 'Tcl interrupt marker' for thread in threading.enumerate())
    assert app.shell.tcl.eval('info commands ::flatcam_async_*') == ''
    assert App.script_processing(app, 'rename [lindex [info commands ::flatcam_async_*] 0] {}; '
                                 'while {1} {}') == 'fail'
    assert App.script_processing(app, 'expr {6 * 7}') == '42'
    print('native interruption and reuse passed', flush=True)


def test_native_tcl_interrupt_is_crash_isolated_and_interpreter_is_reusable():
    import subprocess
    import sys
    import pytest
    if sys.platform != 'win32':
        pytest.skip('Native execution is validated only on Windows.')
    result = subprocess.run(
        [sys.executable, '-c', 'from tests.test_headless_runner import _native_interrupt_child; '
         '_native_interrupt_child()'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'native interruption and reuse passed' in result.stdout

