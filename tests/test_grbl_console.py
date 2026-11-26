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

from PyQt6 import QtCore, QtGui, QtWidgets, QtTest

import operator

from appPlugins.ToolGcodeSender.Sender import GrblConsole, WIDGET_ACTIONS
import appPlugins.Common.grbl.grbl_client  # noqa: F401
from tests.grbl_stream_helpers import CheckFake, make_stream_tool, make_levelled_stream_tool, \
    drop_console_event_filters
from tests.test_levelling_audit import FakeGrbl

# appPlugins/__init__.py shadows the package name with a class, so take the module from sys.modules
grbl_client_module = sys.modules['appPlugins.Common.grbl.grbl_client']


_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestGrblConfiguration(unittest.TestCase):
    def make_configuration(self):
        from tests.test_grbl_sender_construct import make_app
        from tests.test_levelling_audit import FakeGrbl
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender

        class SettingsPort(FakeGrbl):
            def __init__(self):
                super().__init__()
                self.settings = {'13': '0', '100': '250', '101': '250', '700': 'router.local'}

            def _answer(self, cmd):
                if cmd == '$$':
                    self._send(*['$%s=%s' % pair for pair in self.settings.items()], 'ok')
                elif cmd.startswith('$') and '=' in cmd:
                    number, value = cmd[1:].split('=', 1)
                    if number == '101':
                        self._send('error:3')
                    else:
                        self.settings[number] = value
                        self._send('ok')
                else:
                    super()._answer(cmd)

        tool = ToolGCodeSender(make_app())
        tool.build_ui()
        port = SettingsPort()
        tool.grbl_ser_port = port
        tool._grbl_connected = True
        tool._grbl_set_ui_connected(True)
        dock = QtWidgets.QDockWidget()
        self.addCleanup(dock.deleteLater)
        tool.app.ui.grbl_console_dock = dock
        tool._console()
        page = tool.ui.configuration
        tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
        self.addCleanup(drop_console_event_filters)
        return tool, port, page

    def test_explicit_apply_reads_back_partial_success_and_preserves_string_setting(self):
        tool, port, page = self.make_configuration()
        page.refresh_button.click()
        self.assertEqual(page.table.rowCount(), 4)
        self.assertEqual(page.table.item(3, 2).text(), 'router.local')
        port.written.clear()
        original = port._answer

        def reset_notice(cmd):
            if cmd == '$100=300':
                port._send('[MSG:Reboot required]')
            original(cmd)
        port._answer = reset_notice
        page.table.item(1, 3).setText('300')
        page.table.item(2, 3).setText('400')
        self.assertEqual(port.written, [])
        page.apply_changes()
        self.assertEqual(port.commands(), ['?', '$100=300', '$101=400', '$$'])
        self.assertEqual(page.table.item(1, 2).text(), '300')
        self.assertIn('Reboot required', page.table.item(1, 4).text())
        self.assertEqual(page.table.item(2, 2).text(), '250')
        self.assertIn('error:3', page.table.item(2, 4).text())

    def test_busy_injection_and_replaced_connection_cannot_write(self):
        tool, port, page = self.make_configuration()
        page.refresh_button.click()
        port.written.clear()
        page.table.item(1, 3).setText('300\n$H')
        page.apply_changes()
        self.assertEqual(port.written, [])
        page.table.item(1, 3).setText('300')
        op = tool._grbl_reserve('home', motion=True)
        page.apply_changes()
        page.refresh_button.click()
        self.assertEqual(port.written, [])
        tool._grbl_release(op)
        tool.grbl_ser_port = type(port)()
        page.apply_changes()
        self.assertEqual(tool.grbl_ser_port.written, [])

    def test_old_read_result_is_discarded_on_reconnect(self):
        tool, port, page = self.make_configuration()
        tasks = []
        tool.app.worker_task.emit.side_effect = tasks.append
        page.refresh_button.click()
        tool._grbl_set_ui_connected(False)
        tool.grbl_ser_port = type(port)()
        tool._grbl_set_ui_connected(True)
        tasks[0]['fcn']()
        self.assertEqual(page.table.rowCount(), 0)
        self.assertEqual(tool.grbl_ser_port.written, [])

    def test_readback_failure_does_not_confirm_or_retry_acknowledged_write(self):
        tool, port, page = self.make_configuration()
        page.refresh_button.click()
        port.written.clear()
        page.table.item(1, 3).setText('300')
        original = port._answer

        def fail_readback(command):
            if command == '$$':
                port._send('error:3')
            else:
                original(command)
        port._answer = fail_readback
        page.apply_changes()
        self.assertEqual(port.settings['100'], '300')
        self.assertEqual(page.table.item(1, 2).text(), '250')
        self.assertIn('Readback failed', page.table.item(1, 4).text())
        port.written.clear()
        page.apply_changes()
        self.assertEqual(port.written, [])

    def test_unknown_string_edit_and_external_motion_ownership(self):
        tool, port, page = self.make_configuration()
        self.assertEqual(port.written, [])
        page.refresh_button.click()
        port.written.clear()
        page.table.item(3, 3).setText('new-router.local')
        port.state = 'Run'
        page.apply_changes()
        self.assertEqual(port.commands(), ['?'])
        port.state = 'Idle'
        page.update_controls()
        page.refresh_button.click()
        port.written.clear()
        page.apply_changes()
        self.assertEqual(port.commands(), ['?', '$700=new-router.local', '$$'])
        self.assertEqual(page.table.item(3, 2).text(), 'new-router.local')
        tool._grbl_report_inches = False
        tool.ui.grbl_command_entry.set_value('$13=1')
        tool.on_grbl_send_command()
        self.assertIs(tool._grbl_report_inches, True)

    def test_acknowledged_value_missing_from_readback_remains_unverified(self):
        tool, port, page = self.make_configuration()
        page.refresh_button.click()
        page.table.item(1, 3).setText('300')
        original = port._answer

        def missing_setting(command):
            original(command)
            if command == '$100=300':
                del port.settings['100']
        port._answer = missing_setting
        page.apply_changes()
        self.assertIn('not reported', page.table.item(1, 4).text().lower())
        self.assertNotEqual(page.table.item(1, 2).text(), '300')


class TestGrblConsole(unittest.TestCase):

    def make_console(self, tool=None):
        if tool is None:
            tool = MagicMock()
            tool.app.options = {}
        console = GrblConsole(tool)
        self.addCleanup(drop_console_event_filters)
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
                         ['Program', 'Console'])
        console.set_program(['G21', 'G1 X2'])
        console.append('ok')
        self.assertTrue(console.program_view.isReadOnly())
        self.assertEqual(console.program_view.toPlainText(), 'G21\nG1 X2')
        self.assertEqual(console.log_view.toPlainText(), 'ok')
        self.assertIs(console.program_view.gutter.edit, console.program_view)

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

    def test_program_page_loads_in_blocks(self):
        console = self.make_console()
        console.set_program(['G1 X%d' % i for i in range(50000)])
        self.assertLessEqual(console.program_view.document().blockCount(), 20000)
        console.update_progress(30000)
        self.assertIn('30000 / 50000', console.program_status.text())
        for _ in range(100):
            if console.program_view.document().blockCount() >= 50000:
                break
            _qapp.processEvents()
        self.assertEqual(console.program_view.document().blockCount(), 50000)
        self.assertEqual(self.selected_lines(console), [29999])
        self.assertEqual(console.program_view.textCursor().selectedText(), 'G1 X29999')

        console.set_program(['G1 X%d' % i for i in range(50000)])
        console.set_program(['G90', 'G91'])
        for _ in range(20):
            _qapp.processEvents()
        self.assertEqual(console.program_view.toPlainText(), 'G90\nG91')


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

    def test_a_new_plugin_takes_over_the_console_of_the_dock(self):
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender

        old_tool, new_tool = MagicMock(), MagicMock()
        old_tool.app.options = new_tool.app.options = {}
        dock = QtWidgets.QDockWidget()
        self.addCleanup(dock.deleteLater)
        console = self.make_console(old_tool)
        dock.setWidget(console)
        new_tool.app.ui.grbl_console_dock = dock
        new_tool.ui = None
        new_tool.sender_io.new_page.return_value = QtWidgets.QWidget()
        # a new project made a new plugin: the console of the dock must work for it, not for the deleted one
        self.assertIs(ToolGCodeSender._console(new_tool), console)
        console.program_line_selected.emit(3)
        new_tool.on_program_line_selected.assert_called_once_with(3)
        old_tool.on_program_line_selected.assert_not_called()

    def test_a_hidden_console_of_an_inactive_plugin_filters_no_app_event(self):
        class Counting(GrblConsole):
            calls = 0

            def eventFilter(self, obj, event):
                if obj is other:
                    Counting.calls += 1
                return super().eventFilter(obj, event)

        other = QtWidgets.QWidget()
        self.addCleanup(other.deleteLater)
        tool = MagicMock()
        tool.app.options = {}
        console = Counting(tool)
        self.addCleanup(console.deleteLater)
        self.addCleanup(drop_console_event_filters)
        QtWidgets.QApplication.sendEvent(other, QtCore.QEvent(QtCore.QEvent.Type.User))
        self.assertEqual(Counting.calls, 0)
        # the active plugin needs the filter (the Tab jog shortcut works with the dock hidden)
        tool.__dict__['_plugin_active'] = True
        console.sync_event_filter()
        QtWidgets.QApplication.sendEvent(other, QtCore.QEvent(QtCore.QEvent.Type.User))
        self.assertEqual(Counting.calls, 1)
        tool.__dict__['_plugin_active'] = False
        console.sync_event_filter()
        QtWidgets.QApplication.sendEvent(other, QtCore.QEvent(QtCore.QEvent.Type.User))
        self.assertEqual(Counting.calls, 1)

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
                self.assertEqual(console.tabs.currentIndex(), 1)     # a new program does not change the page
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

    def test_ecut_dialog_sensor_home_sends_the_home_command(self):
        from tests.test_levelling_audit import make_audit_tool, run_worker

        class Dialog(QtWidgets.QDialog):
            apply_requested = QtCore.pyqtSignal(dict)
            calibration_requested = QtCore.pyqtSignal(str, float, float, int)
            output_requested = QtCore.pyqtSignal(int, bool)
            stop_requested = QtCore.pyqtSignal()
            reference_requested = QtCore.pyqtSignal(list)
            sensor_home_requested = QtCore.pyqtSignal()

            def __init__(self, result, parent=None):
                super().__init__()

            def set_machine_profile(self, profile):
                pass

        tool = make_audit_tool()
        fake = FakeGrbl()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool._ecut_motion_io_timer = MagicMock()
        tool._refresh_ecut_motion_io = MagicMock()
        with patch('appPlugins.ToolGcodeSender.SenderMotion.EcutMotionDialog', Dialog):
            tool._on_ecut_motion_result((fake, None, 'load', {'revision': 1}, None))
        dialog = tool._ecut_motion_dialog
        self.addCleanup(dialog.deleteLater)
        dialog.sensor_home_requested.emit()
        run_worker(tool)
        self.assertIn('$H', fake.commands())


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
        drop_console_event_filters()
        _qapp.processEvents()

    def console(self):
        return self.dock.widget()

    def test_dock_is_visible_after_run_and_follows_the_machine_panel_checkbox(self):
        cb = self.tool.ui.console_cb
        self.assertTrue(self.dock.isVisible())
        self.assertTrue(cb.isVisible())
        cb.set_value(False)
        self.assertFalse(self.dock.isVisible())
        cb.set_value(True)
        self.assertTrue(self.dock.isVisible())
        self.assertTrue(self.console().jog_block_visible())

    def test_camera_page_is_current_with_the_jog_block_and_without_the_view(self):
        console = self.console()
        ui = self.tool.ui
        self.assertTrue(console.is_page_current(ui.camera_page))
        self.assertTrue(ui.jog_wdg.isVisible())
        self.assertTrue(ui.camera_widget.preview_button.isVisible())
        # the camera image is never in the dock: it lives in the camera pane only
        self.assertFalse(ui.camera_view.isVisible())
        self.assertEqual(ui.camera_page_layout.indexOf(ui.camera_view), -1)

    def test_a_short_dock_scrolls_as_a_whole_and_a_tall_dock_grows_the_text(self):
        console = self.console()
        console.show_page('console')
        bar = console.scroll_area.verticalScrollBar()
        self.window.resize(900, 300)
        _qapp.processEvents()
        self.assertGreater(bar.maximum(), 0)        # header, jog block and tabs scroll together
        self.window.resize(900, 1600)
        _qapp.processEvents()
        self.assertEqual(bar.maximum(), 0)
        tall = console.log_view.height()
        self.window.resize(900, 1900)
        _qapp.processEvents()
        self.assertGreater(console.log_view.height(), tall)     # the text fills the free height

    def test_a_normal_dock_height_has_no_scroll_bar_and_no_empty_space(self):
        console = self.console()
        bar = console.scroll_area.verticalScrollBar()
        for key in console.PAGE_ORDER:
            if not console.tabs.isTabVisible(console.tab_index(key)):
                continue
            console.show_page(key)
            self.window.resize(1200, 900)
            _qapp.processEvents()
            viewport = console.scroll_area.viewport()
            self.assertEqual(bar.maximum(), 0, key)
            bottom = console.tabs.mapTo(viewport, QtCore.QPoint(0, console.tabs.height())).y()
            self.assertLessEqual(viewport.height() - bottom, 6, key)

    def test_orientation_controls_show_only_while_the_camera_runs(self):
        camera = self.tool.ui.camera_widget
        self.assertFalse(camera.orientation_widget.isVisibleTo(camera.preview_controls))
        camera.camera = object()
        camera._sync_orientation_visibility()
        self.assertTrue(camera.orientation_widget.isVisibleTo(camera.preview_controls))
        camera.camera = None
        camera.stop_preview()
        self.assertFalse(camera.orientation_widget.isVisibleTo(camera.preview_controls))

    def test_preview_runs_while_the_camera_pane_is_open_and_stops_with_it(self):
        console = self.console()
        with patch.object(self.tool.ui.camera_widget, 'stop_preview') as stop:
            console.show_page('program')
            self.dock.hide()
            _qapp.processEvents()
            self.assertEqual(stop.call_count, 0)    # the camera shows in its pane, not in the dock page
            self.tool._camera_stop_preview()
            self.assertEqual(stop.call_count, 1)

    def test_a_second_set_tool_ui_leaves_one_camera_page(self):
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            self.tool.set_tool_ui()
        _qapp.processEvents()
        console = self.console()
        self.assertEqual(console.tabs.count(), len([k for k in console._pages]))
        self.assertEqual([console.tabs.tabText(i) for i in range(console.tabs.count())].count('Camera'), 1)
        self.assertIs(console.page('camera'), self.tool.ui.camera_page)

    def test_plugin_cleanup_hides_the_dock(self):
        self.assertTrue(self.dock.isVisible())
        self.tool.on_plugin_cleanup()
        self.assertFalse(self.dock.isVisible())

    def test_tab_shortcut_shows_the_block_and_collapses_it(self):
        self.connect(DockJogFake())
        console = self.console()
        self.dock.hide()
        for unused in range(2):
            self.tool.show_jog_page()
            self.assertTrue(self.dock.isVisible())
            self.assertTrue(console.jog_block_visible())
        self.tool.toggle_jog_page()
        self.assertTrue(self.dock.isVisible())
        self.assertFalse(console.jog_block_visible())
        self.tool.toggle_jog_page()
        self.assertTrue(console.jog_block_visible())
        self.dock.hide()
        self.tool.toggle_jog_page()
        self.assertTrue(self.dock.isVisible())
        self.assertTrue(console.jog_block_visible())

    def test_jog_pad_is_visible_with_the_program_page_current(self):
        console = self.console()
        self.tool.show_jog_page()
        console.show_page('program')
        _qapp.processEvents()
        self.assertTrue(self.tool.ui.jog_wdg.isVisible())
        self.assertTrue(console.jog_block_visible())

    def test_zero_home_and_spindle_sit_in_the_dock_jog_block(self):
        # the camera workflow of run(): Home stays, zero X/Y/Z/All and the spindle are out
        ui = self.tool.ui
        zero = ui.zero_axs_wdg
        for button in (zero.grbl_zerox_button, zero.grbl_zeroy_button, zero.grbl_zeroz_button,
                       zero.grbl_zero_all_button, ui.spindle_button):
            self.assertFalse(button.isVisible(), button)
        self.assertTrue(zero.grbl_homing_button.isVisible())
        # the full workflow: all of them inside the dock, with the Program page current
        self.tool._camera_public_mode = False
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            self.tool.set_tool_ui()
        self.tool.show_jog_page()
        console = self.console()
        console.show_page('program')
        _qapp.processEvents()
        ui = self.tool.ui
        for widget in (ui.zero_axs_wdg, ui.zero_axs_wdg.grbl_zerox_button, ui.zero_axs_wdg.grbl_homing_button,
                       ui.spindle_button, ui.spindle_rpm_entry):
            self.assertTrue(widget.isVisible(), widget)
            self.assertTrue(console.isAncestorOf(widget), widget)

    def test_collapsed_jog_block_is_remembered_and_stops_continuous_jog(self):
        from PyQt6 import QtTest

        port = DockJogFake()
        self.connect(port)
        self.tool.ui.continuous_jog_cb.setChecked(True)
        self.tool.show_jog_page()
        console = self.console()
        button = self.tool.ui.jog_wdg.jog_right_button
        QtTest.QTest.mousePress(button, QtCore.Qt.MouseButton.LeftButton)
        _qapp.processEvents()
        self.assertEqual(len(port.started), 1)
        console.jog_collapse_button.click()
        self.assertEqual(port.stopped, 1)
        self.assertIsNone(self.tool._continuous_jog)
        self.assertFalse(self.tool.ui.jog_wdg.isVisible())
        self.assertTrue(self.tool.app.options['global_grbl_jog_collapsed'])
        console.jog_collapse_button.click()
        self.assertTrue(self.tool.ui.jog_wdg.isVisible())
        self.assertFalse(self.tool.app.options['global_grbl_jog_collapsed'])

    def test_rebuilt_sender_ui_replaces_the_jog_block(self):
        console = self.console()
        old_page = self.tool.ui.jog_page
        self.tool.set_tool_ui()
        self.tool._console()
        self.assertIsNot(self.tool.ui.jog_page, old_page)
        blocks = console.findChildren(QtWidgets.QWidget, 'jog_block_body')[0].children()
        blocks = [w for w in blocks if isinstance(w, QtWidgets.QWidget)]
        self.assertEqual(blocks, [self.tool.ui.jog_page])

    def test_job_buttons_stay_visible_when_another_notebook_tab_is_current(self):
        self.app.ui.notebook.setCurrentIndex(1)     # not the Plugin tab
        _qapp.processEvents()
        ui = self.tool.ui
        self.assertFalse(self.app.ui.plugin_tab.isVisible())
        # the camera workflow of run() keeps the job row and the progress out; the rest of the header stays
        for widget in (ui.status_strip, ui.status_label, ui.estop_dock_button):
            self.assertTrue(widget.isVisible(), widget)
        for widget in (ui.hold_button, ui.stop_button, ui.progress_bar):
            self.assertFalse(widget.isVisible(), widget)
        # the full workflow shows the job row in the dock
        self.tool._camera_public_mode = False
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            self.tool.set_tool_ui()
        _qapp.processEvents()
        ui = self.tool.ui
        for widget in (ui.hold_button, ui.stop_button, ui.progress_bar, ui.status_label, ui.estop_dock_button):
            self.assertTrue(widget.isVisible(), widget)

    def test_rebuilt_sender_ui_replaces_the_dock_header(self):
        console = self.console()
        old_header = self.tool.ui.operate_header
        self.tool.set_tool_ui()
        _qapp.processEvents()
        header = self.tool.ui.operate_header
        self.assertIsNot(header, old_header)
        self.assertTrue(console.isAncestorOf(header))
        self.assertTrue(header.isVisible())
        self.assertFalse(old_header in console.findChildren(QtWidgets.QWidget))

    def full_mode(self):
        self.tool._camera_public_mode = False
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            self.tool.set_tool_ui()
        _qapp.processEvents()
        return self.console()

    def test_job_page_holds_the_job_controls(self):
        self.full_mode()
        ui = self.tool.ui
        bar = ui.al_toolbar
        self.assertTrue(bar.isTabEnabled(bar.indexOf(ui.job_tab)))
        for widget in (ui.object_combo, ui.fit_view_cb, ui.check_mode_cb, ui.autolevel_cb, ui.sim_button,
                       ui.view3d_button, ui.save_levelled_button):
            self.assertTrue(ui.job_tab.isAncestorOf(widget))

    def test_check_mode_is_shown_in_the_header(self):
        self.full_mode()
        ui = self.tool.ui
        ui.job_row.show()
        ui.check_mode_cb.setChecked(True)
        _qapp.processEvents()
        self.assertTrue(ui.check_badge.isVisible())
        self.assertEqual(ui.start_button.text(), "Start (check)")
        ui.check_mode_cb.setChecked(False)
        _qapp.processEvents()
        self.assertFalse(ui.check_badge.isVisible())
        self.assertEqual(ui.start_button.text(), "Start")

    def test_probing_page_holds_the_probing_controls_and_the_camera_mode_keeps_only_the_edge_probe(self):
        ui = self.tool.ui
        ui.al_toolbar.setCurrentWidget(ui.probing_tab)
        _qapp.processEvents()
        self.assertTrue(ui.edge_probe_frame.isVisible())
        self.assertFalse(ui.heightmap_frame.isVisible())
        self.assertFalse(ui.probe_points_frame.isVisible())
        self.full_mode()
        ui = self.tool.ui
        ui.al_toolbar.setCurrentWidget(ui.probing_tab)
        _qapp.processEvents()
        for widget in (ui.edge_probe_frame, ui.grbl_get_heightmap_button, ui.stop_probing_button,
                       ui.grbl_save_height_map_button, ui.al_frame, ui.precision_probe_cb):
            self.assertTrue(ui.probing_tab.isAncestorOf(widget))
        self.assertTrue(ui.heightmap_frame.isVisible())
        ui.autolevel_cb.setChecked(True)
        _qapp.processEvents()
        self.assertTrue(ui.al_frame.isVisible())
        self.assertFalse(ui.al_hint_label.isVisible())
        ui.autolevel_cb.setChecked(False)
        _qapp.processEvents()
        self.assertFalse(ui.al_frame.isVisible())
        self.assertTrue(ui.al_hint_label.isVisible())

    def test_plugin_tab_has_five_pages_and_the_configuration_table_is_not_in_the_dock(self):
        console = self.full_mode()
        ui = self.tool.ui
        bar = ui.al_toolbar
        pages = [bar.widget(i) for i in range(bar.count())]
        self.assertEqual(pages, [ui.gr_conn_tab, ui.job_tab, ui.probing_tab, ui.toolchange_tab, ui.config_tab])
        self.assertIsNone(console.page('configuration'))
        self.assertTrue(ui.config_tab.isAncestorOf(ui.configuration))
        for widget in (ui.grbl_parameter_entry, ui.grbl_get_param_button, ui.grbl_report_button):
            self.assertTrue(ui.config_tab.isAncestorOf(widget))
        self.assertTrue(ui.gr_conn_tab.isAncestorOf(ui.controller_reset_button))
        self.tool._grbl_set_ui_connected(False)
        for page in (ui.probing_tab, ui.toolchange_tab, ui.config_tab):
            self.assertFalse(bar.isTabEnabled(bar.indexOf(page)))
        self.connect(DockJogFake())
        for page in (ui.probing_tab, ui.toolchange_tab, ui.config_tab):
            self.assertTrue(bar.isTabEnabled(bar.indexOf(page)))

    def test_overrides_page_is_in_the_dock_once(self):
        console = self.full_mode()
        self.tool.set_tool_ui()
        _qapp.processEvents()
        titles = [console.tabs.tabText(i) for i in range(console.tabs.count())]
        self.assertEqual(titles.count('Overrides'), 1)
        self.assertEqual(titles.index('Overrides'), titles.index('IO') + 1)
        self.assertEqual(titles.index('IO'), titles.index('Console') + 1)
        page = console.page('overrides')
        button = next(iter(self.tool.ui.ov_buttons.values()))[0]
        self.assertTrue(page.isAncestorOf(button))
        self.assertTrue(page.isAncestorOf(self.tool.ui.ov_values_label))

    def visible_pages(self):
        ui = self.tool.ui
        bar = ui.al_toolbar
        plugin = {name for name in ('gr_conn_tab', 'job_tab', 'probing_tab', 'toolchange_tab', 'config_tab')
                  if bar.isTabVisible(bar.indexOf(getattr(ui, name)))}
        console = self.console()
        dock = {key for key in ('camera', 'program', 'console', 'overrides')
                if console.tabs.isTabVisible(console.tab_index(key))
                and console.tabs.isTabEnabled(console.tab_index(key))}
        return plugin, dock, ui.job_row.isVisible()

    def test_the_mode_table_sets_the_pages_of_the_plugin_tab_and_the_dock(self):
        every_dock_page = {'camera', 'program', 'console', 'overrides'}
        every_plugin_page = {'gr_conn_tab', 'job_tab', 'probing_tab', 'toolchange_tab', 'config_tab'}
        with self.subTest(mode='camera'):
            self.assertEqual(self.visible_pages(), (every_plugin_page - {'job_tab'},
                                                    {'camera', 'program'}, False))
        # the dock is not rebuilt: the full mode must set its pages back
        self.full_mode()
        with self.subTest(mode='full'):
            self.assertEqual(self.visible_pages(), (every_plugin_page, every_dock_page, True))

    def test_new_project_leaves_no_widget_of_the_old_ui_in_the_dock(self):
        console = self.full_mode()
        old_ui = self.tool.ui
        old_parts = [old_ui.operate_header, old_ui.jog_page, old_ui.command_row, old_ui.camera_page,
                     old_ui.overrides_page, old_ui.estop_dock_button, old_ui.start_button, old_ui.jog_wdg]
        self.assertTrue(all(console.isAncestorOf(part) for part in old_parts))
        self.tool.detach_for_reset()
        _qapp.processEvents()
        self.assertFalse(self.app.ui.grbl_console_dock.isVisible())
        for part in old_parts:
            try:
                self.assertFalse(console.isAncestorOf(part))
            except RuntimeError:
                pass    # already deleted
        self.assertIsNone(console.page('camera'))
        # the next UI mounts its own parts
        self.tool.set_tool_ui()
        _qapp.processEvents()
        self.assertTrue(console.isAncestorOf(self.tool.ui.operate_header))
        self.assertTrue(console.isAncestorOf(self.tool.ui.camera_page))

    def test_the_command_entry_is_the_only_one_and_sits_in_the_console_page(self):
        console = self.full_mode()
        self.tool.set_tool_ui()
        _qapp.processEvents()
        page = console.page('console')
        ui = self.tool.ui
        self.assertTrue(page.isAncestorOf(ui.grbl_command_entry))
        self.assertTrue(page.isAncestorOf(ui.grbl_send_button))
        self.assertEqual(len(page.findChildren(QtWidgets.QLineEdit)), 1)
        # Send runs the typed command through the controller path and clears the entry
        with patch.object(self.tool, '_grbl_start_op') as start_op, \
                patch.object(self.tool, 'action_allowed', return_value=None):
            self.tool._apply_machine_state()
            ui.grbl_command_entry.set_value('  $G  ')
            ui.grbl_send_button.click()
        start_op.assert_called_once()
        self.assertEqual(ui.grbl_command_entry.get_value(), '')

    def test_disconnected_job_keeps_offline_actions_available(self):
        self.tool._grbl_set_ui_connected(False)
        for button in (self.tool.ui.start_button, self.tool.ui.hold_button,
                       self.tool.ui.resume_button, self.tool.ui.spindle_button,
                       self.tool.ui.unlock_button):
            self.assertFalse(button.isEnabled())
        for button in (self.tool.ui.sim_button, self.tool.ui.save_levelled_button,
                       self.tool.ui.view3d_button):
            self.assertTrue(button.isEnabled())

    def test_estop_button_in_status_bar_follows_connection(self):
        button = QtWidgets.QPushButton('E-Stop')
        self.window.statusBar().addPermanentWidget(button)
        button.hide()
        self.app.ui.sender_estop_button = button
        self.dock.hide()
        self.app.ui.notebook.setCurrentIndex(1)     # another plugin tab
        self.connect(DockJogFake())
        _qapp.processEvents()
        self.assertTrue(button.isVisible())
        self.tool._grbl_set_ui_connected(False)
        _qapp.processEvents()
        self.assertFalse(button.isVisible())

    def test_estop_key_works_in_a_text_field(self):
        from appGUI.MainGUI import MainGUI
        self.connect(DockJogFake())
        self.tool.on_emergency_stop = MagicMock()
        self.app.grbl_sender_tool = self.tool
        window = SimpleNamespace(app=self.app, isAncestorOf=lambda widget: True,
                                 app_focus_in_text=MainGUI.app_focus_in_text)
        field = QtWidgets.QLineEdit(self.window)

        def press(key):
            event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, key, QtCore.Qt.KeyboardModifier.NoModifier)
            with patch.object(QtWidgets.QApplication, 'focusWidget', return_value=field):
                return MainGUI.eventFilter(window, field, event)

        self.assertTrue(press(QtCore.Qt.Key.Key_Pause))
        self.tool.on_emergency_stop.assert_called_once_with()
        self.tool._grbl_connected = False           # no port: the key does nothing
        press(QtCore.Qt.Key.Key_Pause)
        self.tool.on_emergency_stop.assert_called_once_with()
        self.app.grbl_sender_tool = None            # no Sender tool: nothing happens
        press(QtCore.Qt.Key.Key_Pause)

    def test_work_position_identifies_converted_job_units_and_reported_spindle(self):
        self.connect(DockJogFake())
        self.tool._target = lambda: MagicMock(units='IN')
        self.tool._on_status_report({'state': 'Idle', 'wpos': (25.4, 50.8, 76.2),
                                    'ov': (100, 100, 100), 'accessory': 'S'})
        text = self.tool.ui.status_label.text()
        self.assertIn('Work position (in)', text)
        self.assertIn('X 1.000', text)
        self.assertEqual(self.tool.ui.spindle_button.text(), 'Spindle ON')
        self.assertTrue(self.tool.ui.spindle_button.isChecked())
        self.tool._on_status_report({'state': 'Idle', 'ov': (100, 100, 100)})
        self.assertEqual(self.tool.ui.spindle_button.text(), 'Spindle OFF')

    def test_abort_banner_stays_until_recovery_ends(self):
        self.connect(DockJogFake())
        strip = self.tool.ui.status_strip
        self.tool.on_grbl_reset = MagicMock()
        self.tool._grbl_recovery = 'needs_reset'
        self.tool._stream_result = {'last_text': 'error:20', 'line': 5}
        self.tool._on_stream_ended('error')
        self.assertFalse(strip.recovery_frame.isHidden())
        self.assertIn('Unsupported', strip.recovery_label.text())
        for state in ('Hold', 'Hold', 'Hold', 'Idle', 'Idle', 'Idle'):
            self.tool._on_status_report({'state': state})
            self.assertFalse(strip.recovery_frame.isHidden())
        self.tool.ui.recovery_button.click()
        self.tool.on_grbl_reset.assert_called_once_with()
        self.tool._grbl_recovery = None
        self.tool._on_status_report({'state': 'Idle'})
        self.assertTrue(strip.recovery_frame.isHidden())

    def test_unlock_clears_alarm_on_gui_thread_after_ok(self):
        self.connect(DockJogFake())
        strip = self.tool.ui.status_strip
        tasks = []
        self.tool._grbl_start_op = lambda name, task, **kwargs: tasks.append(task)
        replies = [('error:8', 'error'), ('ok', 'ok')]
        self.tool._grbl_transact = lambda op, command, timeout=None: replies.pop(0)
        strip.set_alarm('ALARM:1')
        self.tool.on_unlock()
        self.assertFalse(strip.alarm_frame.isHidden())      # not cleared before the result
        tasks[-1](None)
        self.assertFalse(strip.alarm_frame.isHidden())      # a failed $X keeps the alarm row
        self.tool.on_unlock()
        tasks[-1](None)
        self.assertTrue(strip.alarm_frame.isHidden())

    def test_status_strip_state_alarm_position_and_disconnect(self):
        self.connect(DockJogFake())
        strip = self.tool.ui.status_strip
        self.tool._on_status_report({'state': 'Alarm'})
        self.assertEqual(strip.state_label.text(), 'Alarm')
        self.tool._stream_result = {'last_text': 'ALARM:3'}
        self.tool._on_stream_ended('alarm')
        self.assertFalse(strip.alarm_frame.isHidden())
        self.assertIn('ALARM:3', strip.alarm_label.text())
        self.tool._on_status_report({'state': 'Idle'})
        self.assertTrue(strip.alarm_frame.isHidden())
        self.tool._on_status_report({'state': 'Idle', 'wpos': (1.0, 2.5, -0.25)})
        self.assertEqual([strip.dro_x.text(), strip.dro_y.text(), strip.dro_z.text()],
                         ['1.000', '2.500', '-0.250'])
        self.tool._report_scale = lambda: None
        self.tool._on_status_report({'state': 'Idle', 'wpos': (1.0, 2.5, -0.25)})
        self.assertEqual([strip.dro_x.text(), strip.dro_y.text(), strip.dro_z.text()], ['---'] * 3)
        self.tool._grbl_set_ui_connected(False)
        self.assertEqual(strip.state_label.text(), 'Not connected')
        self.assertEqual(strip.dro_x.text(), '---')

    def test_ecut_queue_is_shown_only_for_an_ecut_port_and_ends_idle(self):
        class QueuePort(DockJogFake):
            state = {'idle': False, 'lines_waiting': 2, 'program': False, 'blocks_waiting': 3,
                     'blocks_active': 1, 'records_unsent': 4500, 'board_queued': True}

            def get_queue_state(self):
                return dict(self.state)

        self.connect(DockJogFake())
        self.tool._on_status_report({'state': 'Run'})
        self.assertNotIn('Queue', self.tool.ui.status_label.text())
        port = QueuePort()
        self.connect(port)
        self.tool._on_status_report({'state': 'Run'})
        text = self.tool.ui.status_label.text()
        for number in ('2 lines', '3 blocks', '4500 records'):
            self.assertIn(number, text)
        port.state = dict(port.state, idle=True, lines_waiting=0, blocks_waiting=0, blocks_active=0,
                          records_unsent=0, board_queued=False)
        self.tool._on_status_report({'state': 'Idle'})
        text = self.tool.ui.status_label.text()
        self.assertIn('idle', text)
        self.assertNotIn('4500', text)

    def test_connection_action_explains_pending_and_cancelled_attempt(self):
        self.tool._grbl_connect_attempt = object()
        self.tool._grbl_set_ui_connected(False)
        self.assertIn('Cancel', self.tool.ui.com_connect_button.text())
        self.assertTrue(self.tool.ui.com_connect_button.isEnabled())
        self.tool.ui.com_connect_button.click()
        self.assertIsNone(self.tool._grbl_connect_attempt)
        self.assertEqual(self.tool.ui.com_connect_button.text(), 'Connect')
        self.tool._grbl_connect_attempt = object()
        self.connect(DockJogFake())
        self.assertEqual(self.tool.ui.com_connect_button.text(), 'Disconnect')

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
        self.tool.show_jog_page()
        QtTest.QTest.mouseClick(self.tool.ui.jog_wdg.jog_right_button,
                                QtCore.Qt.MouseButton.LeftButton)
        _qapp.processEvents()
        jogs = [cmd for cmd in port.commands() if cmd.startswith('$J')]
        self.assertEqual(jogs, ['$J=G91 G21 X1.5000 F200.0'])

    def test_console_history(self):
        from PyQt6 import QtTest

        entry = self.tool.ui.grbl_command_entry
        with patch.object(self.tool, '_grbl_start_op'), patch.object(self.tool, 'action_allowed', return_value=None):
            for command in ('$$', '$G', '$G'):
                entry.set_value(command)
                self.tool.on_grbl_send_command()
        self.assertEqual(self.tool.app.options['tools_grbl_console_history'], ['$$', '$G'])
        for key, shown in ((QtCore.Qt.Key.Key_Up, '$G'), (QtCore.Qt.Key.Key_Up, '$$'),
                           (QtCore.Qt.Key.Key_Down, '$G')):
            QtTest.QTest.keyClick(entry, key)
            self.assertEqual(entry.get_value(), shown)

    def test_diagonal_jog_command(self):
        from tests.test_levelling_audit import FakeGrbl

        port = FakeGrbl()
        self.connect(port)
        self.tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
        self.tool.ui.jog_step_entry.set_value(1)
        self.tool.ui.jog_fr_entry.set_value(1500)
        self.tool.on_grbl_jog('xplus_yminus')
        self.tool.on_grbl_jog('xminus_yplus')
        jogs = [cmd for cmd in port.commands() if cmd.startswith('$J')]
        self.assertEqual(jogs, ['$J=G91 G21 X1.0000 Y-1.0000 F1500.0', '$J=G91 G21 X-1.0000 Y1.0000 F1500.0'])

    def test_jog_defaults_in_inch(self):
        from appPlugins.ToolGcodeSender.Sender import AppDefaults
        from tests.test_levelling_audit import FakeGrbl

        tool = self.tool
        tool.units = tool.app.app_units = 'IN'
        tool.app.decimals = 4
        tool.ui.jog_step_entry.set_precision(4)  # the dock was built with the decimals of the test app
        tool.ui.jog_step_entry.slider.set_precision(4)
        tool.ui.jog_step_entry.set_range(0.0001, 20)
        tool.app.options.update({'tools_grbl_jog_step': 5, 'tools_grbl_jog_fr': 600, 'tools_grbl_travelz': 15})
        port = FakeGrbl()
        self.connect(port)
        tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
        tool._jog_defaults_to_form(AppDefaults.factory_defaults)
        self.assertAlmostEqual(tool.ui.jog_step_entry.get_value(), 0.1969, places=4)
        tool.on_grbl_jog('origin')
        self.assertTrue(any('Z0.5906' in c for c in port.commands()))
        # a rebuild without an edit keeps the stored value exactly
        tool._jog_defaults_to_form(AppDefaults.factory_defaults)
        self.assertEqual(tool.app.options['tools_grbl_jog_step'], 5)
        tool.ui.jog_step_entry.set_value(0.5)
        tool.ui.jog_step_entry.spinner.editingFinished.emit()
        self.assertEqual(tool.app.options['tools_grbl_jog_step'], 12.7)
        self.assertEqual(tool.app.options['tools_grbl_jog_fr'], 600)
        # the jog word has no exponent form and a step below the resolution is refused
        tool.ui.jog_step_entry.set_value(0.0001)
        tool.on_grbl_jog('xplus')
        self.assertTrue(any('X0.0001' in c for c in port.commands()))
        sent = len(port.commands())
        with patch.object(tool.ui.jog_step_entry, 'get_value', return_value=0.00004):
            tool.on_grbl_jog('xplus')
        self.assertEqual(len(port.commands()), sent)
        self.assertIn('resolution', tool.app.inform.emit.call_args.args[0])
        self.assertFalse(any('e-' in c.lower() for c in port.commands() if c.startswith('$J')))

    def native_hold(self, port=None):
        from PyQt6 import QtTest

        port = self.native_ready(port)
        button = self.tool.ui.jog_wdg.jog_right_button
        QtTest.QTest.mousePress(button, QtCore.Qt.MouseButton.LeftButton)
        return port, button

    def native_ready(self, port=None):
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
        self.tool.show_jog_page()
        self.window.activateWindow()
        if _qapp.platformName() == 'offscreen':
            # Offscreen has no desktop activation; model the held jog's active application.
            self.enterContext(patch.object(QtWidgets.QApplication, 'applicationState',
                                           return_value=QtCore.Qt.ApplicationState.ApplicationActive))
            QtWidgets.QApplication.setActiveWindow(self.window)
        QtTest.QTest.qWait(30)
        self.assertEqual(port.written, [])
        return port

    def send_key(self, target, key, kind=QtCore.QEvent.Type.KeyPress, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
        event = QtGui.QKeyEvent(kind, key, modifiers, '', False, 1)
        with patch.object(QtWidgets.QApplication, 'focusWidget', return_value=target):
            consumed = self.console().eventFilter(target, event)
        return consumed, event

    def test_sender_keys(self):
        from appGUI import shortcuts
        Key = QtCore.Qt.Key
        self.connect(DockJogFake())
        self.tool._plugin_active = True
        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
        self.tool.show_jog_page()
        self.addCleanup(shortcuts.registry().set_key, 'sender.jog_x_plus', 'Right')
        with patch.object(self.tool, 'on_grbl_jog') as jog:
            self.assertTrue(self.send_key(self.dock, Key.Key_Right)[0])
            jog.assert_called_once_with('xplus')
            jog.reset_mock()
            entry = self.tool.ui.grbl_command_entry
            self.assertFalse(self.send_key(entry, Key.Key_Right)[0])
            jog.assert_not_called()
            shortcuts.registry().set_key('sender.jog_x_plus', 'D')
            self.assertTrue(self.send_key(self.dock, Key.Key_D)[0])
            jog.assert_called_once_with('xplus')
            jog.reset_mock()
            self.assertFalse(self.send_key(self.dock, Key.Key_Right)[0])
            jog.assert_not_called()

    def test_key_release_stops_continuous_jog(self):
        Key = QtCore.Qt.Key
        Release = QtCore.QEvent.Type.KeyRelease
        port = self.native_ready()
        self.tool._plugin_active = True
        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
        self.send_key(self.dock, Key.Key_Right)
        self.wait_native(lambda: len([d for d in port.written if d.startswith(b'$J')]) >= 1)
        # no check for "no 0x85 yet": under load the dead-man guard (0.15 s of real time) can stop the jog
        # itself; a release that did nothing keeps the key down, so the jog never ends and the wait fails
        # the release goes through, whatever the focus is now
        self.assertTrue(self.send_key(self.tool.ui.grbl_command_entry, Key.Key_Right, Release)[0])
        self.wait_native(lambda: self.tool._grbl_op is None)
        self.assertIn(b'\x85', port.written)
        self.wait_native(lambda: self.tool.ui.jog_fr_entry.isEnabled())
        # the window loses the focus while the key is down: no release will come
        port.written.clear()
        self.send_key(self.dock, Key.Key_Right)
        self.wait_native(lambda: len([d for d in port.written if d.startswith(b'$J')]) >= 1)
        self.console().eventFilter(self.dock, QtCore.QEvent(QtCore.QEvent.Type.WindowDeactivate))
        self.wait_native(lambda: self.tool._grbl_op is None)
        self.assertIn(b'\x85', port.written)

    def wait_native(self, predicate, timeout=10000):
        # the jog thread runs in the background; under the parallel load of the gate 2 s was too short
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
        # the release of the operation reaches the GUI thread as a queued signal
        self.wait_native(lambda: self.tool.ui.jog_fr_entry.isEnabled())

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

        for exit_name in ('hide', 'collapse', 'close', 'focus', 'mode', 'units', 'feed',
                          'disconnect', 'stop', 'rebuild', 'cleanup', 'reset'):
            with self.subTest(exit=exit_name):
                port, button = self.native_hold()
                self.wait_native(lambda: any(d.startswith(b'$J') for d in port.written))
                if exit_name == 'hide':
                    self.dock.hide()
                elif exit_name == 'collapse':
                    self.console().collapse_jog()
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
                    self.tool._reset_now()      # the Reset button is refused during a jog: Stop is the way
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

        # the guard timer (50 ms) may fire inside processEvents; the OS focus of the test run must not decide
        for focus in (patch.object(QtWidgets.QApplication, 'applicationState',
                                   return_value=QtCore.Qt.ApplicationState.ApplicationActive),
                      patch.object(QtWidgets.QWidget, 'isActiveWindow', return_value=True)):
            focus.start()
            self.addCleanup(focus.stop)
        port = DockJogFake()
        self.connect(port)
        self.tool.ui.jog_fr_entry.set_value(150)
        self.tool.ui.continuous_jog_cb.setChecked(True)
        self.tool.show_jog_page()
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
        self.console().collapse_jog()
        self.assertEqual(port.stopped, 3)
        self.assertIsNone(self.tool._continuous_jog)
        release_op()

        self.console().expand_jog()
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


class TestDockTabWrap(unittest.TestCase):
    TITLES = {'camera': 'Camera calibration', 'io': 'Inputs and outputs of the machine',
              'overrides': 'Feed and spindle overrides'}

    def setUp(self):
        tool = MagicMock()
        tool.app.options = {}
        self.console = GrblConsole(tool)
        self.addCleanup(drop_console_event_filters)
        self.addCleanup(self.console.deleteLater)
        self.addCleanup(self.console.close)
        for key, title in self.TITLES.items():
            self.console.set_page(key, title, QtWidgets.QWidget())
        self.console.show()
        self.strip = self.console._tab_strip
        self.buttons = self.strip._buttons
        # the dock cannot get narrower than the strip
        self.assertGreaterEqual(self.console.minimumSizeHint().width(), self.strip.minimumSizeHint().width())
        # alone, the strip can be sized freely (the other dock content has its own minimum width)
        self.strip.setParent(None)
        self.addCleanup(self.strip.deleteLater)
        self.strip.show()

    def at(self, width):
        self.strip.resize(width, self.strip.heightForWidth(width))
        _qapp.processEvents()
        self.assertEqual(self.strip.width(), width)
        return [b for b in self.buttons if b.isVisibleTo(self.strip)]

    @staticmethod
    def rows(buttons):
        return sorted({b.y() for b in buttons})

    def assert_inside(self, buttons, width):
        for b in buttons:
            self.assertTrue(0 <= b.x() and b.x() + b.width() <= width, (b.text(), b.geometry()))
        for a in buttons:
            for b in buttons:
                if a is not b and a.y() == b.y():
                    self.assertFalse(a.geometry().intersects(b.geometry()), (a.text(), b.text()))

    def test_wrap_wide_narrow_wide_with_two_rows_at_most(self):
        wide = self.strip.sizeHint().width()
        self.assertTrue(self.console.tabs.tabBar().isHidden())
        self.assertEqual(len(self.rows(self.at(wide))), 1)
        mid = self.strip.minimumSizeHint().width() + 20
        self.assertEqual(len(self.rows(self.at(mid))), 2)
        self.assertEqual(len(self.rows(self.at(wide))), 1)
        smallest = self.strip.minimumSizeHint().width()
        shown = self.at(smallest)
        self.assertLessEqual(len(self.rows(shown)), 2)
        self.assert_inside(shown, smallest)
        self.assertLess(smallest, wide)

    def test_hidden_and_disabled_tabs(self):
        self.console.set_page_visible('camera', False)
        self.console.set_page_enabled('console', False)
        shown = self.at(self.strip.sizeHint().width())
        self.assertEqual(len(shown), len(self.buttons) - 1)
        index = self.console.tab_index('console')
        self.assertFalse(self.buttons[index].isEnabled())
        before = self.console.tabs.currentIndex()
        QtTest.QTest.mouseClick(self.buttons[index], QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.console.tabs.currentIndex(), before)

    def test_narrow_strip_elides_and_every_tab_stays_clickable(self):
        wide = self.strip.sizeHint().width()
        self.assertTrue(all('…' not in b.text() for b in self.at(wide)))
        smallest = self.strip.minimumSizeHint().width()
        shown = self.at(smallest)
        self.assertTrue(any('…' in b.text() for b in shown))
        self.assertTrue(all(b.toolTip() for b in shown if '…' in b.text()))
        self.assertEqual(len(shown), self.console.tabs.count())
        self.assert_inside(shown, smallest)
        for i, button in enumerate(self.buttons):
            QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
            self.assertEqual(self.console.tabs.currentIndex(), i)
            self.assertTrue(button.isChecked())


ALL_ACTIONS = ('connect', 'start', 'hold', 'resume', 'next_block', 'stop', 'estop', 'reset', 'unlock', 'home', 'jog',
               'zero', 'wcs', 'spindle', 'spindle_off', 'override', 'rapid_override', 'console', 'macro', 'probe', 'edit', 'config')
IDLE_NO = ('resume', 'next_block', 'hold')


class TestActionRules(unittest.TestCase):
    """Which action the machine situation permits (the rows of ACTION_RULES)."""

    def make(self):
        fake = FakeGrbl()
        tool = make_stream_tool(fake)
        tool._plugin_active = True
        tool.op_changed_sig.emit.side_effect = tool._apply_machine_state
        fake.written.clear()
        return tool, fake

    def situation(self, tool, name):
        if name == 'not_connected':
            tool.grbl_ser_port = None
            tool._grbl_connected = False
        elif name == 'needs_reset':
            tool._grbl_recovery = 'needs_reset'
        elif name == 'braking':
            tool._grbl_stop_phase = 'braking'
        elif name == 'reset_pending':
            tool._grbl_reset_pending = True
        elif name == 'needs_home':
            tool._grbl_recovery = 'needs_home'
        elif name == 'alarm':
            tool._controller_state = 'Alarm'
        elif name == 'held':
            tool._controller_state = 'Hold'
        elif name == 'probe':
            tool._grbl_reserve('probe', motion=True)
        elif name == 'tool_pause':
            tool._tool_pause = {}
        elif name == 'tool_pause_probe':
            tool._tool_pause = {}
            tool._grbl_reserve('probe', motion=True)
        elif name.startswith('stream'):
            tool._grbl_reserve('stream', motion=True)
            tool._controller_state = {'stream_check': 'Check', 'stream_hold': 'Hold', 'stream_door': 'Door',
                                      'stream_tool': 'Tool', 'stream_step': 'Idle', 'stream_run': 'Run'}[name]
            tool._step_waiting = name == 'stream_step'

    ROWS = (
        ('not_connected', ('connect', 'estop')),
        ('needs_reset', ('reset', 'stop', 'estop', 'spindle_off')),
        ('braking', ('reset', 'stop', 'estop', 'spindle_off')),
        ('reset_pending', ('reset', 'stop', 'estop', 'spindle_off')),
        ('held', ('resume', 'stop', 'estop', 'reset', 'spindle_off', 'console')),
        ('needs_home', ('home', 'reset', 'unlock', 'estop', 'spindle_off')),
        ('alarm', ('unlock', 'home', 'reset', 'stop', 'estop', 'console', 'spindle_off')),
        ('probe', ('stop', 'estop')),
        ('tool_pause_probe', ('stop', 'estop')),
        ('tool_pause', ('resume', 'stop', 'estop', 'reset', 'jog', 'probe', 'zero', 'console', 'spindle',
                        'macro', 'home', 'spindle_off')),
        ('stream_check', ('stop', 'estop')),
        ('stream_hold', ('resume', 'stop', 'estop', 'override', 'rapid_override')),
        ('stream_door', ('resume', 'stop', 'estop', 'override', 'rapid_override')),
        ('stream_tool', ('jog', 'console', 'resume', 'stop', 'estop')),
        ('stream_step', ('next_block', 'hold', 'stop', 'estop', 'override', 'rapid_override')),
        ('stream_run', ('hold', 'stop', 'estop', 'override', 'rapid_override')),
        ('idle', tuple(name for name in ALL_ACTIONS if name not in IDLE_NO)),
    )

    def test_action_rules(self):
        for name, allowed in self.ROWS:
            with self.subTest(situation=name):
                tool, _fake = self.make()
                self.situation(tool, name)
                for action in ALL_ACTIONS:
                    reason = tool.action_allowed(action)
                    if action in allowed:
                        self.assertIsNone(reason, action)
                    else:
                        self.assertTrue(reason, action)
                # each operator widget is enabled exactly when its action is allowed
                tool.ui.spindle_button.isChecked.return_value = False
                tool._apply_machine_state()
                for path, action in WIDGET_ACTIONS.items():
                    widget = operator.attrgetter(path)(tool.ui)
                    self.assertEqual(widget.setEnabled.call_args.args[0], action in allowed, path)
                self.assertEqual(tool.ui.jog_wdg.setEnabled.call_args.args[0], 'jog' in allowed)
                for button, byte in tool.ui.ov_buttons.values():
                    action = 'rapid_override' if byte in (b'\x95', b'\x96', b'\x97') else 'override'
                    self.assertEqual(button.setEnabled.call_args.args[0], action in allowed)

    def test_rapid_override_refused_for_an_ecut_stream(self):
        tool, fake = self.make()
        fake.prepare_program = MagicMock()
        self.situation(tool, 'stream_run')
        self.assertIsNone(tool.action_allowed('override'))
        self.assertTrue(tool.action_allowed('rapid_override'))
        tool.on_grbl_override(b'\x96')
        tool.on_grbl_override(b'\x91')
        self.assertNotIn(b'\x96', b''.join(fake.written))
        self.assertIn(b'\x91', b''.join(fake.written))

    def test_handlers_refuse_without_widgets(self):
        for name in ('probe', 'stream_run', 'needs_reset'):
            with self.subTest(situation=name):
                tool, fake = self.make()
                self.situation(tool, name)
                tool.app.worker_task.emit.reset_mock()
                tool.on_grbl_homing()
                tool.on_grbl_jog('xplus')
                tool.on_grbl_zero('x')
                tool.on_start()
                self.assertEqual(tool.app.worker_task.emit.call_count, 0)
                self.assertEqual(fake.written, [])

    def test_operation_during_tool_pause_blocks_the_pause_actions(self):
        tool, fake = self.make()
        self.situation(tool, 'tool_pause_probe')
        for action in ('resume', 'home', 'probe', 'macro'):
            self.assertTrue(tool.action_allowed(action), action)
        tool._on_resume_button()
        tool.on_grbl_homing()
        self.assertEqual(fake.written, [])
        for action in ('stop', 'estop'):
            self.assertIsNone(tool.action_allowed(action), action)

    def test_status_poll_does_not_block_actions(self):
        tool, fake = self.make()
        tool.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
        tool.app.app_units = tool.units = 'MM'
        tool.ui.jog_step_entry.get_value.return_value = 1.0
        tool.ui.jog_fr_entry.get_value.return_value = 100.0
        poll = tool._grbl_reserve(grbl_client_module.STATUS_POLL, read_only=True)
        self.assertIsNotNone(poll)
        self.assertIsNone(tool.action_allowed('start'))
        self.assertIsNone(tool.action_allowed('jog'))
        tool._grbl_release(poll)
        for _poll in range(3):
            poll = tool._grbl_reserve(grbl_client_module.STATUS_POLL, read_only=True)
            tool._grbl_release(poll)
        # no operator widget changed during the polls
        self.assertEqual(tool.op_changed_sig.emit.call_count, 0)
        tool.on_grbl_jog('xplus')
        self.assertEqual(sum(cmd.startswith('$J=') for cmd in fake.commands()), 1)
        tool, fake = self.make()
        self.situation(tool, 'probe')
        self.assertTrue(tool.action_allowed('start'))
        self.assertTrue(tool.action_allowed('jog'))

    def test_ui_rebuild_keeps_machine_state(self):
        from tests.test_grbl_sender_construct import make_app
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
        tool = ToolGCodeSender(make_app())
        tool.build_ui()
        self.addCleanup(drop_console_event_filters)
        tool.grbl_ser_port = FakeGrbl()
        tool._grbl_connected = True
        tool._plugin_active = True
        self.assertIsNotNone(tool._grbl_reserve('stream', motion=True))
        tool.build_ui()
        self.assertFalse(tool.ui.start_button.isEnabled())
        self.assertFalse(tool.ui.jog_wdg.isEnabled())
        self.assertTrue(tool.ui.estop_button.isEnabled())
        self.assertFalse(tool.ui.reset_button.isEnabled())
        # app.cleanup during the job does not rebuild the UI
        ui = tool.ui
        tool.on_reset_tool()
        self.assertIs(tool.ui, ui)

    def test_refused_zero_keeps_sessions(self):
        import threading
        for handler in ('zero', 'home'):
            for accepted in (False, True):
                with self.subTest(handler=handler, accepted=accepted):
                    tool, _fake = self.make()
                    threads = []
                    tool._camera_new_session = MagicMock(side_effect=lambda *_a: threads.append(threading.current_thread()))
                    tool._toolchange_new_session = MagicMock()
                    tool._invalidate_coordinates = MagicMock()
                    tool._grbl_start_op = MagicMock(return_value=accepted)
                    tool._last_wco = (1.0, 2.0, 3.0)
                    if handler == 'zero':
                        tool.on_grbl_zero('x')
                    else:
                        tool.on_grbl_homing()
                    self.assertEqual(tool._camera_new_session.called, accepted)
                    self.assertEqual(tool._toolchange_new_session.called, accepted)
                    self.assertEqual(tool._invalidate_coordinates.called, accepted)
                    if handler == 'zero':
                        self.assertEqual(tool._last_wco is None, accepted)
                    if accepted:
                        self.assertEqual(threads, [threading.main_thread()])


class TestIOPage(unittest.TestCase):
    def test_output_click_requests_the_toggled_state_and_input_click_does_nothing(self):
        from appPlugins.ToolGcodeSender.SenderIOUI import IOPage
        page = IOPage()
        self.addCleanup(page.deleteLater)
        page.set_signals([('limit_x', 'X', 'X limit')], [('flood', 'F', 'Flood'), ('mist', 'M', 'Mist')])
        page.set_outputs_enabled(True)
        page.show()
        page.update({'flood': (True, '', 'readback'), 'mist': (None, 'no report', ''),
                     'limit_x': (False, '', 'readback')})
        requests = []
        page.output_requested.connect(lambda name, wanted: requests.append((name, wanted)))
        QtTest.QTest.mouseClick(page.outputs['flood'], QtCore.Qt.MouseButton.LeftButton)
        QtTest.QTest.mouseClick(page.outputs['mist'], QtCore.Qt.MouseButton.LeftButton)
        QtTest.QTest.mouseClick(page.inputs['limit_x'], QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [('flood', False), ('mist', True)])


class MonitorFake(FakeGrbl):
    """
    A FakeGrbl for the IO status monitor. Only the real-time status byte `rt_byte` (b'?', or b'\\x80'
    on a grblHAL with RT-) gets a report; the other byte is ignored. Each report has other inputs.
    `$H` answers its ok after `home_delay` s. `state`/`extra` set the state and the extra report fields.
    """

    def __init__(self, rt_byte=b'?', home_delay=0.0, extra='', **kwargs):
        super().__init__(**kwargs)
        self.rt_byte = rt_byte
        self.home_delay = home_delay
        self.extra = extra
        self.queries = 0
        self.home_due = None
        self.on_ok = None
        self.full_report = None         # the answer to the grblHAL real-time 0x87

    def write(self, data):
        if data == b'\x87' and self.full_report is not None:
            self.written.append(data)
            self._send(self.full_report)
            return
        if data in (b'?', b'\x80'):
            self.written.append(data)
            if data == self.rt_byte:
                self.queries += 1
                self._send('<%s|MPos:0.000,0.000,0.000|Pn:%s%s>' % (
                    self.state, 'XP' if self.queries % 2 else 'Z', self.extra))
            return
        super().write(data)

    def readline(self):
        if self.home_due is not None and time.monotonic() >= self.home_due:
            self.home_due = None
            if self.on_ok is not None:
                self.on_ok()
            self._send('ok')
        if not self.rx:
            time.sleep(0.005)
        return super().readline()

    def _answer(self, cmd):
        if cmd == '$H' and self.home_delay:
            self.home_due = time.monotonic() + self.home_delay
            return
        super()._answer(cmd)


class TestIOStatusMonitor(unittest.TestCase):
    def make(self, **kwargs):
        fake = MonitorFake(**kwargs)
        tool = make_stream_tool(fake)
        tool._plugin_active = True
        tool._io_monitor_active = True
        tool.grbl_ser_port.written.clear()
        return tool, fake

    @staticmethod
    def reports(tool):
        return [call.args[0] for call in tool.status_report_sig.emit.call_args_list]

    @staticmethod
    def wait_idle(tool):
        deadline = time.monotonic() + 3.0
        while tool._grbl_op is not None and time.monotonic() < deadline:
            time.sleep(0.005)

    def read_status(self, tool):
        op = tool._grbl_reserve('IO status', read_only=True)
        self.assertIsNotNone(op)
        try:
            return tool._grbl_query_status(op, timeout=1.0, observe=True)
        finally:
            tool._grbl_release(op)

    def test_alarm_in_recovery_updates_the_inputs_and_home_still_runs(self):
        tool, fake = self.make(state='Alarm')
        fake.rx.append(b'<Alarm|MPos:0.000,0.000,0.000>\r\n')
        tool._grbl_recovery = 'needs_home'
        tool._grbl_stream_dirty = True
        generation = tool._grbl_safety_generation
        for _ in range(3):
            self.assertTrue(self.read_status(tool).startswith('<Alarm'))
        self.assertEqual(tool._grbl_recovery, 'needs_home')
        self.assertEqual(tool._grbl_safety_generation, generation)
        # the queued report is published too (no pins), then the 3 answers
        self.assertEqual([r.get('pins') for r in self.reports(tool)], [None, 'XP', 'Z', 'XP'])
        # the same polls through the timer slot
        tool._poll_idle_status()
        self.wait_idle(tool)
        self.assertEqual(fake.queries, 4)
        self.assertEqual(tool._grbl_recovery, 'needs_home')
        # Home is not blocked by the monitor
        tool._grbl_stream_dirty = False
        fake.state = 'Home'
        op = tool._grbl_reserve('home', motion=True, allow_in_recovery=True)
        self.assertIsNotNone(op)
        self.assertEqual(tool._grbl_transact(op, '$H')[1], 'ok')
        tool._grbl_release(op)

    def test_a_new_alarm_without_recovery_is_still_latched(self):
        tool, fake = self.make(state='Alarm')
        self.read_status(tool)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertEqual(tool._grbl_safety_generation, 1)

    def test_reply_pending_does_not_stop_the_query_but_stops_a_command(self):
        tool, fake = self.make()
        tool._grbl_rx_port = fake                       # the port of the earlier command
        tool._grbl_reply_pending = True
        self.assertTrue(self.read_status(tool).startswith('<Idle'))
        self.assertEqual(fake.queries, 1)
        self.assertTrue(tool._grbl_reply_pending)       # a report is not the acknowledgement
        tool._grbl_stale_notified = True                # the user was told: no wait for the old reply
        op = tool._grbl_reserve('command')
        self.assertEqual(tool._grbl_transact(op, 'G4 P0')[1], 'timeout')
        tool._grbl_release(op)
        self.assertNotIn(b'G4 P0\n', fake.written)
        fake.rx.append(b'ok\r\n')                       # the earlier ok
        self.read_status(tool)
        self.assertFalse(tool._grbl_reply_pending)      # consumed once
        op = tool._grbl_reserve('command')
        self.assertEqual(tool._grbl_transact(op, 'G4 P0')[1], 'ok')
        tool._grbl_release(op)
        self.assertEqual(fake.rx, [])

    def test_external_owner_does_not_stop_the_monitor_and_its_release_is_seen(self):
        tool, fake = self.make(extra='|MPG:1')
        self.read_status(tool)
        self.assertTrue(tool._grbl_mpg_owned)
        self.assertEqual(self.reports(tool)[-1]['pins'], 'XP')
        self.assertIsNone(tool._grbl_reserve('command', motion=True))
        fake.extra = '|MPG:0'
        self.read_status(tool)
        self.assertFalse(tool._grbl_mpg_owned)
        self.assertEqual(len(self.reports(tool)), 2)
        op = tool._grbl_reserve('command', motion=True)
        self.assertIsNotNone(op)
        tool._grbl_release(op)

    def test_reset_pending_refuses_the_monitor_without_a_query(self):
        tool, fake = self.make()
        tool._grbl_reset_pending = True
        tool._poll_idle_status()
        self.assertEqual(fake.written, [])

    def test_long_home_publishes_inputs_and_the_status_is_not_its_acknowledgement(self):
        for rt_byte, top_bit in ((b'?', False), (b'\x80', True)):
            with self.subTest(rt_byte=rt_byte):
                tool, fake = self.make(rt_byte=rt_byte, home_delay=0.5, state='Home')
                tool._grbl_top_bit_realtime = top_bit
                seen = []
                fake.on_ok = lambda: seen.append(len(self.reports(tool)))
                op = tool._grbl_reserve('home', motion=True, allow_in_recovery=True)
                started = time.monotonic()
                with patch.object(grbl_client_module, 'IO_STATUS_PERIOD', 0.05):
                    text, status = tool._grbl_transact(op, '$H', timeout=5.0)
                self.assertEqual(status, 'ok')
                self.assertGreaterEqual(time.monotonic() - started, 0.45)
                self.assertGreaterEqual(seen[0], 3)         # the inputs changed before $H ended
                self.assertEqual(fake.written[0], b'$H\n')
                self.assertEqual(set(fake.written[1:]), {rt_byte})
                self.assertNotIn('<', text)
                self.assertIs(tool._grbl_op, op)            # no other operation, no second reader
                tool._grbl_release(op)

    def test_transact_without_monitor_sends_no_status_request(self):
        tool, fake = self.make(home_delay=0.3)
        tool._io_monitor_active = False
        op = tool._grbl_reserve('home', motion=True, allow_in_recovery=True)
        with patch.object(grbl_client_module, 'IO_STATUS_PERIOD', 0.05):
            self.assertEqual(tool._grbl_transact(op, '$H', timeout=5.0)[1], 'ok')
        self.assertEqual(fake.written, [b'$H\n'])
        tool._grbl_release(op)

    def test_guard_reason_writes_nothing(self):
        tool, fake = self.make()
        op = tool._grbl_reserve('IO output', motion=True)
        self.assertEqual(tool._grbl_transact(op, 'M3 S100', guard=lambda: 'Alarm'), ('Alarm', 'error'))
        self.assertEqual(fake.written, [])
        self.assertFalse(tool._grbl_reply_pending)
        self.assertEqual(tool._grbl_transact(op, 'M3 S100', guard=lambda: None)[1], 'ok')
        self.assertEqual(fake.written, [b'M3 S100\n'])
        tool._grbl_release(op)

    def test_a_command_waits_at_most_for_one_query_and_the_timer_follows_the_monitor(self):
        tool, fake = self.make()
        tool._poll_idle_status()
        op = tool._grbl_reserve('command', motion=True)
        self.assertIsNotNone(op)
        tool._grbl_release(op)
        self.assertLessEqual(fake.written.count(b'?'), 1)
        tool._status_poll_timer = MagicMock()
        tool.set_io_monitor(True)
        tool._status_poll_timer.setInterval.assert_called_with(500)
        tool.set_io_monitor(False)
        tool._status_poll_timer.setInterval.assert_called_with(1000)
        self.assertFalse(tool._io_monitor_active)


class TestIOTab(unittest.TestCase):
    """The IO tab: discovery, input and accessory states, requests, the output enable rule and the timers."""

    IDENT = ['[VER:1.1h.20190825:]', '[OPT:VM,15,128]']

    def make(self, ident=None, hal=False, **fake_args):
        from appPlugins.Common.grbl.grbl_protocol import parse_grbl_identity, grbl_capabilities
        fake = MonitorFake(**fake_args)
        tool = make_stream_tool(fake)
        tool._plugin_active = True
        tool._status_poll_timer = MagicMock()
        tool.set_io_monitor = MagicMock()
        tool.ui.spindle_rpm_entry.get_value.return_value = 1000
        tool._grbl_grblhal = hal
        tool._grbl_caps = grbl_capabilities(parse_grbl_identity('\n'.join(ident or self.IDENT)))
        tool.io_discovery_sig = MagicMock()
        tool.io_result_sig = MagicMock()
        io = tool.sender_io
        tool.io_result_sig.emit.side_effect = io.on_result
        self.clock = [0.0]
        io.clock = lambda: self.clock[0]
        io._page_shown = lambda: True
        self.page = io.new_page()
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(lambda: io._timer is not None and io._timer.stop())
        io.discover(fake)
        self.discovery_written = list(fake.written)
        fake.written.clear()
        return tool, fake, io

    def feed(self, io, text):
        from appPlugins.Common.grbl.grbl_protocol import parse_grbl_status_report
        io.on_status_report(parse_grbl_status_report(text))

    @staticmethod
    def shown(page, name):
        indicator = page.outputs.get(name) or page.inputs[name]
        return indicator.state

    def test_unknown_accessory_state_stays_disabled_after_a_global_enable(self):
        from appPlugins.ToolGcodeSender.SenderIO import ACCESSORY
        tool, fake, io = self.make()
        io.refresh()
        names = [name for name in ACCESSORY if name in self.page.outputs]
        self.assertTrue(names)
        self.page.set_outputs_enabled(True)
        self.page.set_output_supported(names[0], True)
        for name in names:
            self.assertFalse(self.page.outputs[name].isEnabled(), name)
        self.page.set_output_blocked(names[0], '')
        self.assertTrue(self.page.outputs[names[0]].isEnabled())

    def test_discovery_falls_back_to_plain_i_and_reads_the_pin_report(self):
        fake = MonitorFake(replies={'$I+': ['error:3'], '$I': self.IDENT + ['ok']})
        tool = make_stream_tool(fake)
        tool._grbl_grblhal = False
        op = tool._grbl_reserve('units')
        tool._grbl_read_after_connect(op)
        tool._grbl_release(op)
        self.assertEqual(fake.commands()[:2], ['$I+', '$I'])
        self.assertIs(tool._grbl_caps['mist'], True)
        for hal, setting, expected in ((True, '$10=3', False), (True, '$10=19', True), (False, '$10=3', True)):
            with self.subTest(hal=hal, setting=setting):
                fake = MonitorFake(replies={'$$': [setting, '$13=0', 'ok']})
                tool = make_stream_tool(fake)
                tool._grbl_grblhal = hal
                op = tool._grbl_reserve('units')
                tool._grbl_read_report_units(op)
                tool._grbl_release(op)
                self.assertIs(tool.sender_io.pin_report, expected)

    def test_signals_follow_the_capabilities_and_a_new_port_replaces_a_stale_one(self):
        tool, fake, io = self.make(ident=['[VER:1.1f:]', '[OPT:VD,15,128]', '[SIGNALS:RHSD]', '[AUX IO:0,2,0,0]',
                                          '[FIRMWARE:grblHAL]'], hal=True)
        self.assertEqual(list(self.page.inputs), ['X Limit', 'Y Limit', 'Z Limit', 'Probe', 'Door', 'Hold', 'Reset',
                                                  'Cycle Start'])
        # D: the direction pin is the enable pin: no CCW; M is absent: no Mist
        self.assertEqual(list(self.page.outputs), ['Spindle CW', 'Flood', 'Aux 0', 'Aux 1'])
        before = dict(self.page.outputs)
        io.discover(MonitorFake())          # not the port of the tool
        self.assertEqual(self.page.outputs, before)
        tool._grbl_caps = {'probe': None, 'mist': True, 'spindle_dir': True, 'control_signals': None, 'aux_out': 0}
        tool._grbl_grblhal = False
        io.discover(fake)                   # a reconnect with other capabilities rebuilds the page
        self.assertEqual(list(self.page.outputs), ['Spindle CW', 'Spindle CCW', 'Flood', 'Mist'])
        tool._grbl_connected = False
        io.on_disconnect()
        self.assertEqual((list(self.page.inputs), list(self.page.outputs)), ([], []))
        self.assertIn('Not connected', self.page.message.text())

    def test_pins_are_evidence_only_when_reported_and_the_feed_goes_stale(self):
        tool, fake, io = self.make(hal=True)
        io.pin_report = False
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000>')
        self.assertIsNone(self.shown(self.page, 'X Limit'))         # pin reporting off: gray, not red
        self.assertIn('pin reporting off', self.page.inputs['X Limit'].toolTip())
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Pn:XP>')
        self.assertEqual([self.shown(self.page, n) for n in ('X Limit', 'Y Limit', 'Probe')], [True, False, True])
        for second in (0.5, 1.0, 2.0, 2.5):                         # a healthy 500 ms feed never flickers
            self.clock[0] = second
            io.tick()
            self.assertTrue(self.shown(self.page, 'X Limit'))
        io.aux['Aux 0'] = True
        self.clock[0] = 2.6                                         # no report for more than 2.5 s
        io.tick()
        self.assertIsNone(self.shown(self.page, 'X Limit'))
        self.assertIsNone(self.shown(self.page, 'Flood'))

    def test_activation_makes_all_unknown_then_the_first_report_with_ov_gives_red_not_gray(self):
        tool, fake, io = self.make()
        io.evaluate()
        tool.set_io_monitor.assert_called_with(True)
        self.assertIsNone(self.shown(self.page, 'Flood'))
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Pn:X>')          # no Ov: no accessory evidence
        self.assertIsNone(self.shown(self.page, 'Flood'))
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Pn:X|Ov:100,100,100>')   # GRBL: Ov without A: = all off
        self.assertEqual([self.shown(self.page, n) for n in ('Spindle CW', 'Flood', 'Mist')], [False] * 3)
        io._page_shown = lambda: False                                # the dock hides: the monitor stops
        io.evaluate()
        tool.set_io_monitor.assert_called_with(False)
        io._page_shown = lambda: True                                 # and shows again: a new sync starts
        io.evaluate()
        tool.set_io_monitor.assert_called_with(True)
        self.assertIsNone(self.shown(self.page, 'Flood'))

    def test_grblhal_activation_asks_for_a_full_report(self):
        tool, fake, io = self.make(hal=True)        # the discovery activates the visible IO tab
        self.assertIn(b'\x87', self.discovery_written)
        # its reply reaches the page through the preparation of the next transaction, before any idle poll
        io.pin_report = True
        fake.full_report = '<Idle|MPos:0.000,0.000,0.000|Pn:X|A:F>'
        tool.status_report_sig.emit.side_effect = io.on_status_report
        io.request_full_report()
        op = tool._grbl_reserve('read')
        with tool._grbl_op_lock:
            tool._grbl_prepare_locked(op, fake)
        tool._grbl_release(op)
        self.assertEqual([self.shown(self.page, n) for n in ('Flood', 'Mist', 'Spindle CW')], [True, False, False])
        self.assertIs(self.shown(self.page, 'X Limit'), True)

    def test_a_status_report_drained_before_a_guarded_write_sets_the_owner_and_blocks_it(self):
        tool, fake, io = self.make()
        io.evaluate()
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100>')
        fake.rx.append(b'<Idle|MPos:0.000,0.000,0.000|MPG:1>\r\n')
        io.on_output_requested('Spindle CW', True)
        self.assertTrue(tool._grbl_mpg_owned)
        self.assertEqual(fake.commands(), [])

    def test_a_replaced_page_drops_its_queued_request(self):
        for replace in (lambda io: (io.detach(), io.new_page()), lambda io: io.new_page()):
            tool, fake, io = self.make()
            io.evaluate()
            self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100>')
            tasks = []
            tool._grbl_start_op = lambda name, task, **kwargs: tasks.append(task) or True
            io.on_output_requested('Spindle CW', True)
            replace(io)
            self.assertIsNone(io.pending)
            op = tool._grbl_reserve('IO output', motion=True)
            tasks[0](op)
            tool._grbl_release(op)
            self.assertEqual(fake.written, [])

    def test_an_unread_grblhal_reporting_mode_is_unknown_not_off(self):
        tool, fake, io = self.make(hal=True)
        io.pin_report = True                                        # the value of an older connection
        fake.replies = {'$$': ['error:3']}
        op = tool._grbl_reserve('units')
        tool._grbl_read_report_units(op)
        tool._grbl_release(op)
        self.assertIsNone(io.pin_report)
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000>')
        self.assertIsNone(self.shown(self.page, 'X Limit'))
        self.assertIn('unknown', self.page.inputs['X Limit'].toolTip())
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Pn:XP>')
        self.assertEqual([self.shown(self.page, n) for n in ('X Limit', 'Y Limit', 'Probe')], [True, False, True])

    def test_coolant_sends_one_toggle_and_an_unconfirmed_change_is_not_retried(self):
        tool, fake, io = self.make()
        io.evaluate()
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100|A:FM>')
        self.assertEqual([self.shown(self.page, n) for n in ('Flood', 'Mist')], [True, True])
        io.on_output_requested('Flood', False)
        self.assertEqual(fake.written, [b'\xa0'])                      # only the flood toggle
        io.on_output_requested('Mist', False)                         # 1 request at a time
        self.assertEqual(fake.written, [b'\xa0'])
        self.clock[0] = 11.0
        io.tick()
        self.assertIsNotNone(io.pending)
        self.clock[0] = 13.0
        io.tick()
        self.assertIsNone(io.pending)
        self.assertIsNone(self.shown(self.page, 'Flood'))              # gray: not yet confirmed
        self.assertEqual(fake.written, [b'\xa0'])                      # no retry
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100|A:M>')   # a late report still sets the color
        self.assertIs(self.shown(self.page, 'Flood'), False)

    def test_spindle_ok_is_not_on_and_an_error_keeps_the_output(self):
        tool, fake, io = self.make()
        io.evaluate()
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100>')
        fake.replies = {'M3': ['error:9']}
        io.on_output_requested('Spindle CW', True)
        self.assertEqual(fake.commands(), ['M3 S1000'])
        self.assertIsNone(io.pending)
        self.assertIs(self.shown(self.page, 'Spindle CW'), False)     # unchanged
        fake.replies = {}
        io.on_output_requested('Spindle CW', True)                    # a later valid request works
        self.assertIsNotNone(io.pending)
        self.assertIs(self.shown(self.page, 'Spindle CW'), False)     # ok is "accepted", not "On"
        io.on_output_requested('Spindle CW', True)
        self.assertEqual(fake.commands(), ['M3 S1000', 'M3 S1000'])   # the 2nd click sends nothing
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100|A:>')    # M3 S0: the report has no S
        self.assertIsNone(io.pending)
        self.assertEqual([self.shown(self.page, n) for n in ('Spindle CW', 'Spindle CCW')], [False, False])

    def test_error_20_marks_the_output_not_supported(self):
        tool, fake, io = self.make(ident=self.IDENT + ['[FIRMWARE:grblHAL]', '[AUX IO:0,1,0,0]'], hal=True)
        io.evaluate()
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|A:>')
        fake.replies = {'M64': ['error:20']}
        io.on_output_requested('Aux 0', True)
        self.assertFalse(self.page.outputs['Aux 0'].isEnabled())
        fake.replies = {}
        io.on_output_requested('Flood', True)                           # the other outputs go on working
        self.assertIsNotNone(io.pending)

    def test_aux_output_is_commanded_and_cleared_by_a_job_a_reset_an_alarm_and_an_owner(self):
        tool, fake, io = self.make(ident=self.IDENT + ['[FIRMWARE:grblHAL]', '[AUX IO:0,1,0,0]'], hal=True)
        for clear in (io.on_job_start, io.clear_commanded,
                      lambda: self.feed(io, '<Alarm|MPos:0.000,0.000,0.000>'),
                      lambda: (setattr(tool, '_grbl_mpg_owned', True),
                               self.feed(io, '<Idle|MPos:0.000,0.000,0.000|MPG:1>'))):
            io.evaluate()
            self.feed(io, '<Idle|MPos:0.000,0.000,0.000|A:>')
            tool._controller_state = 'Idle'
            tool._grbl_mpg_owned = False
            io.on_output_requested('Aux 0', True)
            self.assertEqual(self.page.outputs['Aux 0'].source, 'commanded')
            self.assertIs(self.shown(self.page, 'Aux 0'), True)
            clear()
            self.assertIsNone(self.shown(self.page, 'Aux 0'))

    def test_outputs_are_disabled_and_write_nothing_during_a_job_a_pause_an_alarm_or_recovery(self):
        def running(tool):
            tool._grbl_op = SimpleNamespace(name='stream')

        def paused(tool):
            tool._program_paused = True

        def alarm(tool):
            tool._controller_state = 'Alarm'

        def recovery(tool):
            tool._grbl_recovery = 'needs_home'

        for block in (running, paused, alarm, recovery):
            with self.subTest(block=block.__name__):
                tool, fake, io = self.make()
                io.evaluate()
                self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100|A:F>')
                self.assertTrue(self.page.outputs['Flood'].isEnabled())
                block(tool)
                io.refresh()
                self.assertFalse(self.page.outputs['Flood'].isEnabled())
                io.on_output_requested('Flood', False)
                self.assertEqual(fake.written, [])

    def test_a_request_that_became_blocked_before_the_write_writes_nothing(self):
        tool, fake, io = self.make()
        io.evaluate()
        self.feed(io, '<Idle|MPos:0.000,0.000,0.000|Ov:100,100,100>')
        tasks = []
        tool._grbl_start_op = lambda name, task, **kwargs: tasks.append(task) or True
        io.on_output_requested('Spindle CW', True)
        tool._controller_state = 'Alarm'                              # the Alarm comes before the write
        op = tool._grbl_reserve('IO output', motion=True)
        tasks[0](op)
        tool._grbl_release(op)
        self.assertEqual(fake.written, [])
        io.on_disconnect()                                            # a disconnect drops the old request
        self.assertIsNone(io.pending)


if __name__ == '__main__':
    unittest.main()
