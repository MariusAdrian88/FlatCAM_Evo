#!/usr/bin/env python
"""Program display in the sender dock follows GRBL acknowledgements, not execution."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import queue
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6 import QtCore, QtGui, QtWidgets

from appPlugins.ToolGcodeSender.Sender import GrblConsole
from tests.grbl_stream_helpers import CheckFake, make_stream_tool, make_levelled_stream_tool


_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestGrblConsole(unittest.TestCase):

    def make_console(self, tool=None):
        if tool is None:
            tool = MagicMock()
            tool.app.options = {}
        console = GrblConsole(tool)
        self.addCleanup(console.deleteLater)
        self.addCleanup(console.close)
        return console

    def selected_lines(self, console):
        return [s.cursor.blockNumber() for s in console.program_view.extraSelections()]

    def attach_console(self, tool):
        dock = QtWidgets.QDockWidget()
        self.addCleanup(dock.deleteLater)
        tool.app.ui.grbl_console_dock = dock
        console = self.make_console(tool)
        dock.setWidget(console)
        tool.stream_progress_sig.emit.side_effect = tool._on_stream_progress
        tool.console_line_sig.emit.side_effect = console.append
        return console

    def test_program_and_console_tabs_keep_commands_and_log_separate(self):
        console = self.make_console()
        self.assertEqual([console.tabs.tabText(i) for i in range(console.tabs.count())],
                         ['Program', 'Console', 'Jog'])
        self.assertIs(console.tabs.widget(2), console.jog_page)
        console.set_program(['G21', 'G1 X2'])
        console.append('ok')
        self.assertTrue(console.program_view.isReadOnly())
        self.assertEqual(console.program_view.toPlainText(), 'G21\nG1 X2')
        self.assertEqual(console.log_view.toPlainText(), 'ok')
        self.assertIs(console.program_area.number_bar.edit, console.program_view)
        console.cmd_entry.setText('  $G  ')
        console.send_btn.click()
        console.tool.send_console_command.assert_called_once_with('$G')

    def test_progress_highlights_acknowledged_line_and_follow_can_be_disabled(self):
        console = self.make_console()
        console.resize(600, 250)
        console.show()
        console.set_program(['G1 X%d' % i for i in range(1000)])
        _qapp.processEvents()
        console.follow_cb.setChecked(False)
        cursor = console.program_view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
        console.program_view.setTextCursor(cursor)
        scroll = console.program_view.verticalScrollBar().value()

        console.update_progress(800)
        self.assertEqual(self.selected_lines(console), [799])
        self.assertIn('800 / 1000', console.program_status.text())
        self.assertEqual(console.program_view.textCursor().blockNumber(), 799)
        self.assertEqual(console.program_view.textCursor().selectedText(), 'G1 X799')
        self.assertEqual(console.program_view.verticalScrollBar().value(), scroll)

        console.follow_cb.setChecked(True)
        self.assertEqual(console.program_view.textCursor().blockNumber(), 799)
        self.assertEqual(console.program_view.textCursor().selectedText(), 'G1 X799')
        self.assertGreater(console.program_view.verticalScrollBar().value(), scroll)


    def test_click_selects_whole_line_and_progress_advances_selection(self):
        from PyQt6 import QtCore, QtTest

        console = self.make_console()
        lines = ['G21', 'G90', 'G1 X2', 'M2']
        console.set_program(lines)
        console.resize(600, 250)
        console.show()
        _qapp.processEvents()
        for index in (2, 0, 3):
            cursor = QtGui.QTextCursor(console.program_view.document().findBlockByNumber(index))
            point = console.program_view.cursorRect(cursor).center() + QtCore.QPoint(2, 0)
            QtTest.QTest.mouseClick(console.program_view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                   pos=point)
            self.assertEqual(console.program_view.textCursor().selectedText(), lines[index])
            console.tool.on_program_line_selected.assert_called_with(index)
        for done in (1, 2, 3, 4):
            console.update_progress(done)
            self.assertEqual(console.program_view.textCursor().selectedText(), lines[done - 1])
        console.update_progress(3, [1])
        self.assertIn(1, self.selected_lines(console))
        self.assertEqual(console.program_view.extraSelections()[-1].format.background().color(),
                         QtGui.QColor('#c62828'))
        console.set_program(['G90'])
        self.assertFalse(console.program_view.textCursor().hasSelection())

    def test_ctrl_and_shift_selection_flash_all_selected_moves(self):
        import numpy as np
        from PyQt6 import QtCore, QtTest
        from tests.test_grbl_view3d import show_tool, SPLIT_ARC_JOB

        tool = show_tool(SPLIT_ARC_JOB, 'send')
        console = self.attach_console(tool)
        tool._console = MagicMock(return_value=console)
        tool.on_show_3d()
        self.addCleanup(tool.view3d.clear)
        dock = tool.app.ui.grbl_console_dock
        dock.resize(600, 300)
        dock.show()
        _qapp.processEvents()

        def click(index, expected, modifier=QtCore.Qt.KeyboardModifier.NoModifier):
            cursor = QtGui.QTextCursor(console.program_view.document().findBlockByNumber(index))
            point = console.program_view.cursorRect(cursor).center() + QtCore.QPoint(2, 0)
            QtTest.QTest.mouseClick(console.program_view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                   modifier, pos=point)
            self.assertEqual(set(self.selected_lines(console)), expected)
            points = [p for i, a, b, rapid in tool.view3d._segments if i in expected for p in (a, b)]
            np.testing.assert_allclose(tool.view3d.selected.pos, np.array(points).reshape(-1, 3), atol=1e-6)
            self.assertEqual(tool.view3d._selection_timer.isActive(), bool(points))

        ctrl = QtCore.Qt.KeyboardModifier.ControlModifier
        shift = QtCore.Qt.KeyboardModifier.ShiftModifier
        click(3, {3})
        click(4, {3, 4}, ctrl)
        tool.view3d._flash_selection()
        self.assertFalse(tool.view3d.selected.visible)
        click(3, {4}, ctrl)
        self.assertTrue(tool.view3d.selected.visible)
        click(4, {3, 4}, shift)
        click(1, {1, 2, 3}, shift)
        click(4, {4})
        click(4, set(), ctrl)
        click(0, {0})
        click(4, {0, 1, 2, 3, 4}, shift)
        console.clear_program_selection()
        self.assertEqual(self.selected_lines(console), [])
        self.assertFalse(tool.view3d._selection_timer.isActive())

        tool._program_paused = True
        click(3, {3})
        click(4, {3, 4}, ctrl)
        console.update_progress(1, [1])
        self.assertEqual(set(self.selected_lines(console)), {0, 1, 3, 4})
        self.assertEqual(console.program_view.extraSelections()[-1].format.background().color(),
                         QtGui.QColor('#c62828'))
        tool._on_status_report({'state': 'Run'})
        self.assertEqual(set(self.selected_lines(console)), {0, 1})
        self.assertFalse(tool.view3d._selection_timer.isActive())
        console.set_program(['G90'])
        self.assertEqual(self.selected_lines(console), [])

    def test_preview_console_and_manual_plot_selection_share_prepared_lines(self):
        import numpy as np
        from tests.test_grbl_view3d import show_tool, SPLIT_ARC_JOB

        for mode in ('send', 'split'):
            with self.subTest(mode=mode):
                tool = show_tool(SPLIT_ARC_JOB, mode)
                console = self.attach_console(tool)
                tool._console = MagicMock(return_value=console)
                tool.on_show_3d()
                self.assertEqual(console.program_view.toPlainText().splitlines(), tool.job_lines)
                index = tool.view3d._segments[0][0]
                console.program_line_selected.emit(index)
                expected = [p for i, a, b, rapid in tool.view3d._segments if i == index for p in (a, b)]
                np.testing.assert_allclose(tool.view3d.selected.pos, expected)
                console.program_line_selected.emit(len(tool.job_lines) - 1)
                self.assertEqual(tool.view3d.selected.pos.shape, (2, 3))
                console.program_line_selected.emit(0)
                self.assertEqual(tool.view3d.selected.pos.shape, (0, 3))

    def test_paused_selection_preserves_green_progress_and_resume_clears_red(self):
        from tests.test_grbl_view3d import show_tool, SPLIT_ARC_JOB

        tool = show_tool(SPLIT_ARC_JOB, 'send')
        console = self.attach_console(tool)
        tool._console = MagicMock(return_value=console)
        tool.on_show_3d()
        op = tool._grbl_reserve('stream', motion=True)
        self.addCleanup(tool._grbl_release, op)
        tool._on_status_report({'state': 'Run'})
        tool._on_stream_progress(4, 5)
        console.program_line_selected.emit(4)
        self.assertEqual(tool.view3d.selected.pos.shape, (0, 3))
        self.assertEqual(console.program_view.textCursor().blockNumber(), 3)
        tool._on_status_report({'state': 'Hold:0'})
        console.program_line_selected.emit(4)
        self.assertEqual(tool.view3d.selected.pos.shape, (2, 3))
        accepted = tool.view3d.accepted.pos.copy()
        console.program_line_selected.emit(3)
        self.assertGreater(tool.view3d.selected.pos.shape[0], 2)
        tool._on_preview_ready(tool.preview_ready_sig.emit.call_args.args[0])
        self.assertEqual(tool.view3d.selected.pos.shape, accepted.shape)
        self.assertEqual(tool.view3d.accepted.pos.shape, accepted.shape)
        tool._on_stream_progress(5, 5)
        self.assertEqual(tool.view3d.selected.pos.shape, accepted.shape)
        self.assertEqual(tuple(tool.view3d.accepted.color), (0.1, 0.7, 0.3, 1.0))
        tool._on_status_report({'state': 'Run'})
        self.assertEqual(tool.view3d.selected.pos.shape, (0, 3))
        self.assertEqual(console.program_view.textCursor().blockNumber(), 4)

    def test_start_rebuilds_plot_from_stream_program_and_clears_manual_selection(self):
        from appPlugins.Common.gcode_segment import parse_toolpath
        from tests.test_grbl_view3d import show_tool, SPLIT_ARC_JOB

        tool = show_tool(SPLIT_ARC_JOB, 'split')
        console = self.attach_console(tool)
        tool._console = MagicMock(return_value=console)
        tool.on_show_3d()
        tool.view3d.set_toolpath([(0, (100, 100, 0), (200, 200, 0), False)])
        tool.view3d.set_selected_line(0)
        tool._program_paused = True
        tool.on_start()
        self.assertEqual(tool.view3d._segments, parse_toolpath(tool.job_lines))
        self.assertEqual(tool.view3d.selected.pos.shape, (0, 3))
        self.assertFalse(tool._program_paused)
        self.assertEqual(console.program_view.toPlainText().splitlines(), tool.job_lines)

    def test_program_stays_read_only_with_editor_shortcuts(self):
        from PyQt6 import QtCore, QtTest

        console = self.make_console()
        console.set_program(['G21', 'G1 X2'])
        console.program_view.selectAll()
        QtTest.QTest.keyClicks(console.program_view, '(edited)')
        QtTest.QTest.keyClick(console.program_view, QtCore.Qt.Key.Key_Slash,
                             QtCore.Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(console.program_view.toPlainText(), 'G21\nG1 X2')

    def test_outside_click_clears_manual_selection_and_flashing(self):
        from PyQt6 import QtCore, QtTest
        from tests.test_grbl_view3d import show_tool, SPLIT_ARC_JOB

        tool = show_tool(SPLIT_ARC_JOB, 'send')
        console = self.attach_console(tool)
        tool._console = MagicMock(return_value=console)
        tool.on_show_3d()
        self.addCleanup(tool.view3d.clear)
        dock = tool.app.ui.grbl_console_dock
        dock.resize(600, 300)
        dock.show()
        outside = QtWidgets.QLabel('Outside the program')
        self.addCleanup(outside.deleteLater)
        _qapp.processEvents()
        index = tool.view3d._segments[0][0]
        for paused in (False, True):
            tool._program_paused = paused
            tool._on_stream_progress(4, len(tool.job_lines))
            for target in (console.follow_cb, outside):
                cursor = QtGui.QTextCursor(console.program_view.document().findBlockByNumber(index))
                point = console.program_view.cursorRect(cursor).center() + QtCore.QPoint(2, 0)
                QtTest.QTest.mouseClick(console.program_view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                       pos=point)
                self.assertTrue(console.program_view.textCursor().hasSelection())
                self.assertGreater(tool.view3d.selected.pos.size, 0)
                accepted = tool.view3d.accepted.pos.copy()
                QtTest.QTest.mouseClick(target, QtCore.Qt.MouseButton.LeftButton)
                _qapp.processEvents()
                self.assertFalse(console.program_view.textCursor().hasSelection())
                self.assertEqual(tool.view3d.selected.pos.shape, (0, 3))
                self.assertFalse(tool.view3d._selection_timer.isActive())
                self.assertEqual(tool.view3d.accepted.pos.tolist(), accepted.tolist())

        console.program_line_selected.emit(index)
        dock.hide()
        self.assertFalse(tool.view3d._selection_timer.isActive())

    def test_new_program_clears_progress_and_errors_but_keeps_log(self):
        console = self.make_console()
        console.append('error:20')
        console.set_program(['G21', 'G1 X1'])
        console.update_progress(2, [1])
        self.assertIn(1, self.selected_lines(console))
        console.set_program(['G90'])
        self.assertEqual(self.selected_lines(console), [])
        self.assertIn('0 / 1', console.program_status.text())
        self.assertEqual(console.log_view.toPlainText(), 'error:20')

    def test_stream_loads_prepared_lines_before_worker_and_tracks_replies(self):
        tool = make_stream_tool(CheckFake(), source='(header)\nG21\nG90\nG1 X2\nM2\n')
        console = self.attach_console(tool)
        run_worker = tool.app.worker_task.emit.side_effect

        def worker(task):
            self.assertEqual(console.program_view.toPlainText().splitlines(), tool.job_lines)
            self.assertEqual(self.selected_lines(console), [])
            run_worker(task)

        tool.app.worker_task.emit.side_effect = worker
        tool.on_start()
        self.assertEqual(self.selected_lines(console), [len(tool.job_lines) - 1])
        self.assertIn('4 / 4', console.program_status.text())
        self.assertIn('ok', console.log_view.toPlainText())

    def test_source_changes_reload_program_and_clear_missing_source(self):
        from tests.test_grbl_frame import patch_object_changed

        tool = make_stream_tool(CheckFake())
        patch_object_changed(tool)
        console = self.attach_console(tool)
        for source, expected in (('(header)\nG21\nG1 X3\n', ['G21', 'G1 X3']),
                                 ('G90\nG1 X8\n', ['G90', 'G1 X8']), ('', []), (None, [])):
            with self.subTest(source=source):
                target = SimpleNamespace(kind='cncjob', source_file=source, obj_options={'name': 'job'})
                tool.app.collection.get_by_name.return_value = target if source is not None else None
                console.tabs.setCurrentIndex(1)
                tool.on_object_changed()
                self.assertEqual(console.program_view.toPlainText().splitlines(), expected)
                self.assertEqual(console.tabs.currentIndex(), 0)
                self.assertEqual(self.selected_lines(console), [])

    def test_show_console_selects_program_and_focuses_gcode(self):
        tool = make_stream_tool(CheckFake())
        console = self.attach_console(tool)
        console.tabs.setCurrentIndex(1)
        tool.on_console_toggled(True)
        self.assertEqual(console.tabs.currentIndex(), 0)
        self.assertIs(tool.app.ui.grbl_console_dock.focusWidget(), console.program_view)

    def test_source_change_does_not_replace_a_running_stream_display(self):
        from tests.test_grbl_frame import patch_object_changed

        tool = make_stream_tool(CheckFake())
        patch_object_changed(tool)
        console = self.attach_console(tool)
        console.set_program(['G21', 'G1 X2'])
        console.update_progress(1)
        op = tool._grbl_reserve('stream', motion=True)
        self.assertIsNotNone(op)
        self.addCleanup(tool._grbl_release, op)
        tool.on_object_changed()
        self.assertEqual(console.program_view.toPlainText(), 'G21\nG1 X2')
        self.assertEqual(self.selected_lines(console), [0])

    def test_levelled_program_displays_transformed_lines(self):
        tool, target = make_levelled_stream_tool(CheckFake())
        console = self.attach_console(tool)
        tool.on_start()
        self.assertEqual(console.program_view.toPlainText().splitlines(), tool.job_lines)
        self.assertNotEqual(console.program_view.toPlainText(), target.source_file.strip())
        self.assertEqual(self.selected_lines(console), [len(tool.job_lines) - 1])

    def test_errors_highlight_stream_rows_instead_of_source_line_numbers(self):
        for check_mode in (False, True):
            with self.subTest(check_mode=check_mode):
                fake = CheckFake(replies={'G1': ['error:20']})
                tool = make_stream_tool(fake, source='(header)\nG21\nG90\nG1 X1\nG1 X2\nM2\n',
                                        check_mode=check_mode)
                console = self.attach_console(tool)
                tool.on_start()
                rows = self.selected_lines(console)
                error_rows = [s.cursor.blockNumber() for s in console.program_view.extraSelections()
                              if s.format.background().color() == QtGui.QColor('#c62828')]
                self.assertEqual(error_rows, [2, 3] if check_mode else [2])
                self.assertIn(2, rows)
                self.assertIn('error:20', console.log_view.toPlainText())


class DockJogFake:
    """A connected controller with native continuous jog, without a serial port."""

    def __init__(self):
        self.is_open = True
        self.started = []
        self.stopped = 0
        self.refreshed = 0
        self.written = []

    def start_jog(self, axis, direction, feed):
        self.started.append((axis, direction, feed))

    def refresh_jog(self):
        self.refreshed += 1
        return True

    def stop_jog(self):
        self.stopped += 1

    def write(self, data):
        self.written.append(data)
        return len(data)

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        pass


class NativeJogFake:
    """Serial-only GRBL: ok accepts motion; only a later status confirms completion."""

    def __init__(self, banner=b"Grbl 1.1h ['$' for help]\n"):
        self.is_open = True
        self.banner = banner
        self.written = []
        self.replies = queue.Queue()
        self.replies.put(banner)
        self._read_buffer = b''
        self.busy_until = 0
        self.overlap = False
        self.silent = False
        self.alarm = False

    def readlines(self):
        return self.read(self.in_waiting).splitlines(keepends=True)

    @property
    def in_waiting(self):
        with self.replies.mutex:
            return len(self._read_buffer) + sum(len(reply) for reply in self.replies.queue)

    def read(self, size=1):
        data = b''
        while len(data) < size:
            if not self._read_buffer:
                try:
                    self._read_buffer = self.replies.get(timeout=0.01)
                except queue.Empty:
                    break
            count = min(size - len(data), len(self._read_buffer))
            data += self._read_buffer[:count]
            self._read_buffer = self._read_buffer[count:]
        return data

    def readline(self):
        data = b''
        while not data.endswith(b'\n'):
            byte = self.read(1)
            if not byte:
                break
            data += byte
        return data

    def write(self, data):
        self.written.append(data)
        if data.startswith(b'$J'):
            self.overlap |= time.monotonic() < self.busy_until
            self.busy_until = time.monotonic() + 0.2
            if not self.silent:
                self.replies.put(b'ALARM:1\n' if self.alarm else b'ok\n')
        elif data == b'?':
            if not self.silent:
                state = 'Jog' if time.monotonic() < self.busy_until else 'Idle'
                self.replies.put(('<%s|MPos:0,0,0>\n' % state).encode())
        elif data == b'\x85':
            self.busy_until = 0
        return len(data)

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self._read_buffer = b''
        while not self.replies.empty():
            self.replies.get_nowait()


class TestDockedJogPage(unittest.TestCase):
    """The Sender's one set of jog controls, mounted in the console dock and reached with Tab."""

    def setUp(self):
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
        from tests.test_camera_calibration import make_public_app

        self.app = make_public_app()
        self.window = QtWidgets.QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.dock = QtWidgets.QDockWidget(self.window)
        self.window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.app.ui.grbl_console_dock = self.dock
        self.app.ui.notebook = QtWidgets.QTabWidget(self.window)
        self.app.ui.plugin_tab = QtWidgets.QWidget(self.app.ui.notebook)
        self.app.ui.notebook.addTab(self.app.ui.plugin_tab, 'Plugin')
        for index in range(2):
            self.app.ui.notebook.addTab(QtWidgets.QWidget(self.app.ui.notebook), 'page %d' % index)
        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
        self.window.setCentralWidget(self.app.ui.notebook)
        self.app.options['tools_grbl_console_visible'] = True
        self.tool = ToolGCodeSender(self.app)
        with patch('appPlugins.Common.levelling_core.ShapeCollection'), \
                patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                      return_value=[]):
            self.tool.run()
        self.tool._status_poll_timer.stop()
        self.tool.app.worker_task.emit.side_effect = lambda task: None
        self.window.show()
        _qapp.processEvents()

    def tearDown(self):
        tool = self.tool
        tool._grbl_port_ready = lambda: False
        if tool._grbl_op is not None:
            tool._grbl_release(tool._grbl_op)
        tool.on_plugin_cleanup()
        tool.close()
        tool.deleteLater()
        _qapp.processEvents()

    def console(self):
        return self.dock.widget()

    def connect(self, port):
        self.tool.grbl_ser_port = port
        self.tool._grbl_connected = True
        self.tool._grbl_report_inches = False
        self.tool._grbl_recovery = None
        self.tool._grbl_stream_dirty = False
        self.tool._grbl_set_ui_connected(True)

    def send_tab(self, target, modifiers=QtCore.Qt.KeyboardModifier.NoModifier, autorepeat=False):
        event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Tab, modifiers,
                                '', autorepeat, 1)
        with patch.object(QtWidgets.QApplication, 'focusWidget', return_value=target):
            QtWidgets.QApplication.sendEvent(target, event)

    def test_fixed_step_click_sends_one_bounded_jog(self):
        from PyQt6 import QtTest
        from tests.test_levelling_audit import FakeGrbl

        port = FakeGrbl()
        self.connect(port)
        self.tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
        self.tool.ui.jog_step_entry.set_value(1.5)
        self.tool.ui.jog_fr_entry.set_value(200)
        self.console().tabs.setCurrentWidget(self.console().jog_page)
        self.dock.show()
        QtTest.QTest.mouseClick(self.tool.ui.jog_wdg.jog_right_button,
                                QtCore.Qt.MouseButton.LeftButton)
        _qapp.processEvents()
        jogs = [cmd for cmd in port.commands() if cmd.startswith('$J')]
        self.assertEqual(jogs, ['$J=G91 G21 X1.5 F200.0'])

    def native_hold(self, port=None):
        from PyQt6 import QtTest

        port = port or NativeJogFake()
        self.tool.grbl_ser_port = port
        self.tool._grbl_rx_port = port
        self.tool._grbl_rx_buffer = b''
        self.tool._grbl_reply_pending = False
        self.tool._grbl_safety_generation += 1
        attempt = object()
        self.tool._grbl_connect_attempt = attempt
        self.tool._grbl_read_units_after_connect = MagicMock()
        self.tool._grbl_connect_finish('fake', port, attempt)
        # This fixture bypasses the discovery worker; model its successful completion.
        self.tool._grbl_identity_pending = False
        self.tool.ui.jog_fr_entry.set_value(120)
        self.tool.ui.jog_step_entry.set_value(1)
        self.tool.ui.continuous_jog_cb.setChecked(True)
        self.console().tabs.setCurrentWidget(self.console().jog_page)
        self.dock.show()
        self.window.activateWindow()
        if _qapp.platformName() == 'offscreen':
            # Offscreen has no desktop activation; model the held jog's active application.
            self.enterContext(patch.object(QtWidgets.QApplication, 'applicationState',
                                           return_value=QtCore.Qt.ApplicationState.ApplicationActive))
            QtWidgets.QApplication.setActiveWindow(self.window)
        QtTest.QTest.qWait(30)
        self.assertEqual(port.written, [])
        button = self.tool.ui.jog_wdg.jog_right_button
        QtTest.QTest.mousePress(button, QtCore.Qt.MouseButton.LeftButton)
        return port, button

    def wait_native(self, predicate, timeout=2000):
        from PyQt6 import QtTest

        deadline = time.monotonic() + timeout / 1000
        while not predicate() and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertTrue(predicate())

    def test_native_background_jog_is_bounded_and_release_is_only_cancel(self):
        from PyQt6 import QtTest

        reports = []
        self.tool.status_report_sig.connect(
            lambda report: reports.append((report, QtCore.QThread.currentThread())),
            QtCore.Qt.ConnectionType.QueuedConnection)
        port, button = self.native_hold()
        self.wait_native(lambda: len([d for d in port.written if d.startswith(b'$J')]) >= 2)
        self.assertFalse(port.overlap)
        self.wait_native(lambda: bool(reports))
        self.assertTrue(all(thread is _qapp.thread() for report, thread in reports))
        jogs = [d for d in port.written if d.startswith(b'$J')]
        self.assertTrue(all(d == b'$J=G91 G21 X0.400000000 F120.000000000\n' for d in jogs))
        self.assertFalse(self.tool.ui.jog_fr_entry.isEnabled())
        QtTest.QTest.mouseRelease(button, QtCore.Qt.MouseButton.LeftButton)
        self.wait_native(lambda: self.tool._grbl_op is None)
        self.assertIn(b'\x85', port.written)
        self.assertNotIn(b'!', port.written)
        cancel = port.written.index(b'\x85')
        self.assertFalse(any(d.startswith(b'$J') for d in port.written[cancel:]))
        self.assertTrue(self.tool.ui.jog_fr_entry.isEnabled())

    def test_invalid_jog_values_never_write_motion(self):
        from PyQt6 import QtTest

        self.connect(NativeJogFake())
        self.tool._native_jog_port = self.tool.grbl_ser_port
        self.tool._grbl_set_ui_connected(True)
        button = self.tool.ui.jog_wdg.jog_right_button
        for entry, value in ((self.tool.ui.jog_step_entry, float('nan')),
                             (self.tool.ui.jog_fr_entry, float('inf')),
                             (self.tool.ui.jog_fr_entry, 0)):
            with patch.object(entry, 'get_value', return_value=value):
                for continuous in (False, True):
                    self.tool.ui.continuous_jog_cb.setChecked(continuous)
                    QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.tool.grbl_ser_port.written, [])
        self.assertIsNone(self.tool._grbl_op)

    def test_live_units_mismatch_refuses_initial_jog(self):
        for continuous in (False, True):
            for units, live_units in (('MM', 'IN'), ('IN', 'MM'), ('MM', None), ('MM', 'bad')):
                with self.subTest(continuous=continuous, units=units, live_units=live_units):
                    port = NativeJogFake()
                    self.connect(port)
                    self.tool._native_jog_port = port
                    self.tool.units = units
                    self.app.app_units = live_units
                    self.tool.ui.continuous_jog_cb.setChecked(continuous)
                    if continuous:
                        self.tool._on_jog_pressed(self.tool.ui.jog_wdg.jog_right_button, 'xplus')
                    else:
                        self.tool.on_grbl_jog('xplus')
                    self.assertIsNone(self.tool._grbl_op)
                    self.assertEqual(port.written, [])

    def test_queued_fixed_jog_rejects_changed_context_and_can_retry(self):
        from tests.test_levelling_audit import FakeGrbl

        for change in ('units', 'session', 'port'):
            with self.subTest(change=change):
                port = FakeGrbl()
                self.connect(port)
                self.app.app_units = self.tool.units = 'MM'
                self.tool.on_grbl_jog('xplus')
                task = self.app.worker_task.emit.call_args.args[0]
                if change == 'units':
                    self.app.app_units = 'IN'
                elif change == 'session':
                    self.tool._start_seq += 1
                else:
                    self.tool.grbl_ser_port = FakeGrbl()
                task['fcn'](*task['params'])
                self.assertFalse(any(cmd.startswith('$J') for cmd in port.commands()))
                self.assertIsNone(self.tool._grbl_op)
                self.app.app_units = 'MM'
                self.connect(port)
                self.tool.on_grbl_jog('xplus')
                task = self.app.worker_task.emit.call_args.args[0]
                task['fcn'](*task['params'])
                self.assertEqual(sum(cmd.startswith('$J') for cmd in port.commands()), 1)

    def test_native_hold_all_ui_exits_cancel_without_a_later_segment(self):
        from PyQt6 import QtTest

        for exit_name in ('hide', 'switch', 'close', 'focus', 'mode', 'units', 'feed',
                          'disconnect', 'stop', 'rebuild', 'cleanup', 'reset'):
            with self.subTest(exit=exit_name):
                port, button = self.native_hold()
                self.wait_native(lambda: any(d.startswith(b'$J') for d in port.written))
                if exit_name == 'hide':
                    self.dock.hide()
                elif exit_name == 'switch':
                    self.console().tabs.setCurrentIndex(0)
                elif exit_name == 'close':
                    self.dock.close()
                elif exit_name == 'focus':
                    self.tool.event(QtCore.QEvent(QtCore.QEvent.Type.WindowDeactivate))
                elif exit_name == 'mode':
                    self.tool.ui.continuous_jog_cb.setChecked(False)
                elif exit_name == 'units':
                    self.tool.units = 'IN'
                elif exit_name == 'feed':
                    self.tool.ui.jog_fr_entry.set_value(240)
                elif exit_name == 'disconnect':
                    self.tool.on_grbl_connect()
                elif exit_name == 'stop':
                    self.tool.on_grbl_stop()
                elif exit_name == 'reset':
                    self.tool.on_grbl_reset()
                elif exit_name == 'rebuild':
                    with patch('appPlugins.Common.levelling_core.ShapeCollection'), patch(
                            'appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                            return_value=[]):
                        self.tool.set_tool_ui()
                else:
                    self.tool.on_plugin_cleanup()
                self.wait_native(lambda: self.tool._grbl_op is None)
                self.assertIn(b'\x85', port.written)
                if exit_name == 'disconnect':
                    self.assertFalse(port.is_open)
                index = port.written.index(b'\x85')
                QtTest.QTest.qWait(60)
                self.assertFalse(any(d.startswith(b'$J') for d in port.written[index:]))
                self.tool._stop_continuous_jog()
                self.tool._grbl_recovery = None
                self.tool._grbl_reset_pending = False
                self.tool._grbl_stream_dirty = False
                self.tool.units = 'MM'
                self.tool._plugin_active = True
                if exit_name != 'rebuild':
                    button.setDown(False)

    def test_native_alarm_and_lost_ack_latch_recovery(self):
        for alarm in (True, False):
            with self.subTest(alarm=alarm):
                port = NativeJogFake()
                port.alarm = alarm
                port, button = self.native_hold(port)
                if not alarm:
                    port.silent = True
                self.wait_native(lambda: self.tool._grbl_op is None)
                self.assertEqual(self.tool._grbl_recovery, 'needs_reset')
                self.assertIn(b'\x85', port.written)
                self.assertIsNone(self.tool._grbl_reserve('jog', motion=True))
                self.tool._stop_continuous_jog()
                button.setDown(False)
                self.tool._grbl_recovery = None

    def test_unidentified_or_old_grbl_cannot_offer_continuous(self):
        for banner in (b'ok\n', b"Grbl 0.9j ['$' for help]\n"):
            port = NativeJogFake(banner)
            self.tool.grbl_ser_port = port
            attempt = object()
            self.tool._grbl_connect_attempt = attempt
            self.tool._grbl_read_units_after_connect = MagicMock()
            self.tool._grbl_connect_finish('fake', port, attempt)
            self.assertFalse(self.tool.ui.continuous_jog_cb.isEnabled())
            self.assertTrue(self.tool.ui.continuous_jog_cb.toolTip())
            self.assertEqual(port.written, [])

    def test_continuous_press_release_hide_switch_focus_and_stop_all_brake(self):
        from PyQt6 import QtTest

        port = DockJogFake()
        self.connect(port)
        self.tool.ui.jog_fr_entry.set_value(150)
        self.tool.ui.continuous_jog_cb.setChecked(True)
        self.console().tabs.setCurrentWidget(self.console().jog_page)
        self.dock.show()
        button = self.tool.ui.jog_wdg.jog_right_button

        def press():
            # a hidden button keeps its pressed state; each press here is a fresh hold
            if button.isDown():
                button.setDown(False)
            QtTest.QTest.mousePress(button, QtCore.Qt.MouseButton.LeftButton)
            _qapp.processEvents()

        def release_op():
            # the queued wait_for_stop is not run here; free the port for the next press
            if self.tool._grbl_op is not None:
                self.tool._grbl_release(self.tool._grbl_op)

        press()
        self.assertEqual(port.started, [(0, 0, 150.0)])
        self.assertTrue(self.tool._continuous_jog_timer.isActive())
        self.assertFalse(any(b'$J' in data or b'G1' in data for data in port.written))
        QtTest.QTest.mouseRelease(button, QtCore.Qt.MouseButton.LeftButton)
        _qapp.processEvents()
        self.assertEqual(port.stopped, 1)
        self.assertIsNone(self.tool._continuous_jog)
        self.assertEqual(len(port.started), 1)  # a release is never a fixed step
        release_op()

        press()
        self.dock.hide()
        _qapp.processEvents()
        self.assertEqual(port.stopped, 2)
        self.assertIsNone(self.tool._continuous_jog)
        release_op()

        self.dock.show()
        press()
        self.console().tabs.setCurrentIndex(0)
        self.assertEqual(port.stopped, 3)
        self.assertIsNone(self.tool._continuous_jog)
        release_op()

        self.console().tabs.setCurrentWidget(self.console().jog_page)
        press()
        with patch.object(QtWidgets.QApplication, 'applicationState',
                          return_value=QtCore.Qt.ApplicationState.ApplicationInactive):
            self.tool._refresh_continuous_jog()
        self.assertEqual(port.stopped, 4)
        self.assertIsNone(self.tool._continuous_jog)
        release_op()

        press()
        self.tool._grbl_set_ui_connected(False)
        self.assertEqual(port.stopped, 5)
        self.assertIsNone(self.tool._continuous_jog)
        release_op()

        self.connect(port)
        self.tool.ui.continuous_jog_cb.setChecked(True)
        press()
        self.tool.ui.jog_stop_button.click()
        _qapp.processEvents()
        self.assertEqual(port.stopped, 6)
        self.assertIn(b'!', port.written)


if __name__ == '__main__':
    unittest.main()
