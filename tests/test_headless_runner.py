"""HeadlessRunner on a real Tcl interpreter and a fake app."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

from appCommon.Headless import HeadlessRunner
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


def test_exit_codes_of_a_failed_command_and_of_exit_n():
    code, out, err = _run(_app(command='no_such_command'))
    assert code == 1
    assert 'invalid command name "no_such_command"' in err
    code, out, err = _run(_app(command='exit 3; set never 1'))
    assert code == 3
    assert err == ''


def test_the_prompt_reads_complete_commands_from_stdin():
    stdin = 'set x {\n1 2\n}\nllength $x\nno_such_command\nset y 1\n'
    code, out, err = _run(_app(), stdin=stdin)
    assert '2\n' in out
    assert 'invalid command name' in err
    assert code == 1   # a command failed; the prompt went on with the next one


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


def test_quit_in_headless_mode_ends_the_run_and_saves_nothing():
    import pytest
    from appCommon.Headless import HeadlessExit
    from appHandlers.appLifecycle import AppLifecycle
    app = Mock()
    app.headless = True
    app.headless_exit_code = None
    lifecycle = AppLifecycle(app=app)
    with pytest.raises(HeadlessExit) as info:
        lifecycle.quit_application(silent=True)
    assert info.value.code == 0
    assert app.headless_exit_code == 0
    app.preferencesUiManager.save_defaults.assert_not_called()


def test_command_line_options_headless_and_command(tmp_path):
    """--headless and --command are parsed in the class body of App, before anything starts."""
    import os
    import subprocess
    import sys
    import textwrap
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = textwrap.dedent(f'''
        import sys
        sys.path.insert(0, {repo!r})
        sys.argv = ['flatcam.py'] + {['--headless', '--command=new_geometry g; puts ok', '--shellvar=1,2']!r}
        import appMain
        A = appMain.App
        print(repr((A.cmd_line_headless, A.cmd_line_command, A.cmd_line_shellvar)))
    ''')
    result = subprocess.run([sys.executable, '-c', code], cwd=str(tmp_path), capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "(True, 'new_geometry g; puts ok', '1,2')"


def test_headless_output_goes_to_stdout_and_errors_to_stderr():
    from appCommon.Headless import console_write

    def write(style, text):
        o, e = io.StringIO(), io.StringIO()
        console_write(style, text, out=o, err=e)
        return o.getvalue(), e.getvalue()

    assert write('out', 'hello\n') == ('hello\n', '')
    assert write('warning', '[WARNING] careful\n') == ('[WARNING] careful\n', '')
    assert write('err', 'ERROR Report: boom\n') == ('', 'ERROR Report: boom\n')
    assert write('out', '[ERROR_NOTCL] Failed\n') == ('', '[ERROR_NOTCL] Failed\n')


def test_page_down_in_the_command_line_scrolls_the_shell_output():
    # keyPressEvent of the output pane never passed a key on, so the PageUp / PageDown that the command line sends
    # to it did not scroll; S still toggles the shell
    from PyQt6 import QtCore, QtWidgets
    from PyQt6.QtTest import QTest
    from appGUI.GUIElements import _BrowserTextEdit, _ExpandableTextEdit
    toggles = []
    app = SimpleNamespace(ui=SimpleNamespace(toggle_shell_ui=lambda: toggles.append('S')))
    app.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    browser = _BrowserTextEdit(version='test', app=app)
    browser.setReadOnly(True)
    browser.resize(300, 100)
    browser.setPlainText('\n'.join('line %d' % i for i in range(200)))
    browser.show()
    app.qt_app.processEvents()
    bar = browser.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == 0
    command_line = _ExpandableTextEdit(SimpleNamespace(browser=lambda: browser))
    QTest.keyClick(command_line, QtCore.Qt.Key.Key_PageDown)
    assert bar.value() > 0
    QTest.keyClick(browser, QtCore.Qt.Key.Key_S)
    assert toggles == ['S']
    browser.close()


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
            output.clear()
            timer.start()
            run('puts -nonewline before; ' + script)
            timer.stop()
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
        assert pulses
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
         '_native_interrupt_child()'], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'native interruption and reuse passed' in result.stdout

