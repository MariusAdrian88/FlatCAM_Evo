import unittest

from appPlugins.ToolGcodeSender.SenderSimulation import VirtualGrbl, speed_multiplier


class TestVirtualGrbl(unittest.TestCase):

    def test_speed_uses_integer_steps_then_a_linear_upper_range(self):
        self.assertEqual([speed_multiplier(n) for n in range(25, 35)], list(range(1, 11)))
        self.assertEqual(speed_multiplier(67), 55)
        self.assertAlmostEqual(speed_multiplier(35) - speed_multiplier(34),
                               speed_multiplier(100) - speed_multiplier(99))

    def test_motion_pause_speed_and_replies(self):
        now = [0.0]
        controller = VirtualGrbl(clock=lambda: now[0])
        controller.write(b'G21 G90 G1 X60 F60\n')
        controller.advance()
        now[0] = 10
        controller.advance()
        self.assertAlmostEqual(controller.position[0], 10)
        controller.set_paused(True)
        now[0] = 20
        controller.advance()
        self.assertAlmostEqual(controller.position[0], 10)
        controller.set_speed(100)
        controller.set_paused(False)
        now[0] = 20.5
        controller.advance()
        self.assertAlmostEqual(controller.position[0], 60)
        self.assertEqual(controller.readline(), b'ok\n')
        controller.write(b'?')
        self.assertIn(b'<Idle|MPos:60.000000,0.000000,0.000000', controller.readline())

    def test_units_relative_arc_and_dwell(self):
        now = [0.0]
        controller = VirtualGrbl(clock=lambda: now[0])
        for command, seconds in ((b'G20 G91 G1 X1 F60\n', 1),
                                 (b'G21 G90 G3 X0 Y25.4 I-25.4 J0 F60\n', 40),
                                 (b'G4 P2\n', 2)):
            controller.write(command)
            controller.advance()
            now[0] += seconds
            controller.advance()
            self.assertEqual(controller.readline(), b'ok\n')
        self.assertAlmostEqual(controller.position[0], 0)
        self.assertAlmostEqual(controller.position[1], 25.4)

    def test_stop_cancels_pending_motion(self):
        controller = VirtualGrbl()
        controller.write(b'G1 X100 F1\n')
        controller.stop()
        self.assertEqual(controller.write(b'?'), 'cancelled')
        self.assertEqual(controller.readline(), b'')

    def test_arc_interpolates_and_unsupported_motion_is_rejected(self):
        import math
        now = [0.0]
        controller = VirtualGrbl(clock=lambda: now[0])
        controller.write(b'G2 X2 Y0 I1 J0 F60\n')
        controller.advance()
        now[0] = math.pi / 2
        controller.advance()
        self.assertAlmostEqual(controller.position[0], 1)
        self.assertAlmostEqual(controller.position[1], 1)
        now[0] = math.pi
        controller.advance()
        self.assertEqual(controller.readline(), b'ok\n')
        controller.write(b'G38.2 Z-1 F10\n')
        self.assertEqual(controller.readline(), b'error:20\n')

    def test_streamer_uses_virtual_replies(self):
        from appPlugins.Common.grbl.grbl_client import stream_lines
        controller = VirtualGrbl()
        controller.set_speed(100)
        result = stream_lines(['G21 G90', 'G1 X0.01 F600', 'G4 P0.01'],
                              controller.write, controller.readline, poll_status=True)
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['done'], 3)

    def test_virtual_probe_reports_contact_in_mm_for_inch_commands(self):
        from collections import deque
        now = [0.0]
        controller = VirtualGrbl(clock=lambda: now[0])
        controller.position = (0, 0, 1)
        controller.probe_heights = deque([0.25])
        controller.write(b'G20 G90 G38.2 Z-0.1 F60\n')
        controller.advance()
        now[0] = 1
        controller.advance()
        self.assertEqual(controller.position, (0, 0, 0.25))
        self.assertEqual(controller.readline(), b'[PRB:0.000000,0.000000,0.250000:1]\n')
        self.assertEqual(controller.readline(), b'ok\n')


class TestSimulationControls(unittest.TestCase):
    def make_tool(self):
        from tests.test_grbl_sender_construct import make_app
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
        tool = ToolGCodeSender(make_app())
        tool.build_ui()
        return tool

    def test_simulated_probing_compensates_without_changing_real_map(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from shapely import Point
        for units, scale in (('MM', 1), ('IN', 25.4)):
            with self.subTest(units=units):
                tool = self.make_tool()
                target = SimpleNamespace(kind='cncjob', units=units, coords_decimals=6,
                                         obj_options={'name': 'simulation'}, is_segmented_gcode=True,
                                         source_file=('G20' if units == 'IN' else 'G21') +
                                         '\nG90\nG0 X0 Y0 Z0.1\nG1 Z-0.01 F600\nG1 X0.01\n')
                tool._target = lambda: target
                tool.ui.al_method_radio.set_value('b')
                tool.ui.autolevel_cb.setChecked(True)
                tool.ui.ptravelz_entry.set_value(1 / scale)
                tool.ui.pdepth_entry.set_value(-1 / scale)
                tool.ui.feedrate_probe_entry.set_value(600 / scale)
                tool.al_voronoi_geo_storage = {
                    i: {'point': Point(x, y), 'height': -0.3}
                    for i, (x, y) in enumerate(((0, 0), (0.01, 0), (0, 0.01), (0.01, 0.01)))}
                tool._al_data_units = units
                tool.al_heights_valid = units == 'MM'
                valid = tool.al_heights_valid
                tool._al_points = ('real interpolation cache',)
                original = {i: dict(v) for i, v in tool.al_voronoi_geo_storage.items()}
                tool.grbl_ser_port = MagicMock()
                tool._grbl_connected = True
                tool._console = MagicMock()
                tool.view3d = MagicMock()
                tool.on_simulation()
                dialog = tool._simulation
                dialog.slider.setValue(100)
                tool.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
                with patch('random.uniform', side_effect=[0.1, 0.2, 0.15, 0.25]) as detected:
                    tool._start_simulation()
                self.assertEqual(detected.call_count, 4)
                self.assertTrue(all(call.args == (0.0, 0.25) for call in detected.call_args_list))
                logged = '\n'.join(call.args[0] for call in tool._console().append.call_args_list)
                self.assertEqual(logged.count('[PRB:'), 4)
                self.assertEqual(tool.al_voronoi_geo_storage, original)
                self.assertEqual(tool.al_heights_valid, valid)
                self.assertEqual(tool._al_points, ('real interpolation cache',))
                self.assertEqual(target.source_file.splitlines()[-2], 'G1 Z-0.01 F600')
                self.assertFalse(dialog.running)
                self.assertIn('Finished', tool.ui.status_label.text())
                program = tool._console().set_program.call_args.args[0]
                from appPlugins.Common.gcode_segment import _words
                zs = [dict(_words(line))['Z'] for line in program
                      if line.startswith('G1') and 'Z' in dict(_words(line))]
                self.assertAlmostEqual(zs[0], -0.01 + 0.1 / scale, places=6)
                self.assertAlmostEqual(zs[-1], -0.01 + 0.2 / scale, places=6)
                tool.grbl_ser_port.write.assert_not_called()
                self.assertTrue(tool.view3d.set_height_map.called)
                # A cancelled probe pass must never publish a map or start the cutting program.
                tool.app.worker_task.emit.side_effect = None
                tool._console().reset_mock()
                tool.view3d.reset_mock()
                tool._start_simulation()
                task = tool.app.worker_task.emit.call_args.args[0]
                dialog.stop()
                task['fcn'](*task['params'])
                self.assertIn('Stopped', tool.ui.status_label.text())
                self.assertEqual(tool._console().set_program.call_count, 1)
                self.assertIsNone(tool.view3d.set_height_map.call_args.args[0])
                self.assertEqual(tool.al_voronoi_geo_storage, original)
                dialog.close()

    def test_simulated_autolevelling_requires_probe_points(self):
        from types import SimpleNamespace
        tool = self.make_tool()
        tool.ui.autolevel_cb.setChecked(True)
        tool._target = lambda: SimpleNamespace(units='MM')
        tool.build_job = lambda: True
        tool.job_lines = ['G21 G90', 'G0 X0 Y0 Z1']
        tool.on_simulation()
        dialog = tool._simulation
        self.addCleanup(dialog.close)
        tool._start_simulation()
        self.assertFalse(dialog.running)
        tool.app.worker_task.emit.assert_not_called()
        self.assertIn('Add probe points', tool.app.inform.emit.call_args.args[0])

    def test_height_map_checkbox_toggles_real_and_simulated_maps(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from tests.grbl_stream_helpers import make_storage
        from appPlugins.Common.levelling_interp import build_bilinear_grid
        tool = self.make_tool()
        tool.view3d = MagicMock()
        tool.al_voronoi_geo_storage = make_storage()
        tool.al_heights_valid = True
        checkbox = tool.ui.plot_height_map_cb
        self.assertTrue(checkbox.isChecked())
        self.assertTrue(checkbox.toolTip())
        tool._update_3d_height_map()
        self.assertIsNotNone(tool.view3d.set_height_map.call_args.args[0])
        checkbox.setChecked(False)
        self.assertIsNone(tool.view3d.set_height_map.call_args.args[0])
        checkbox.setChecked(True)
        self.assertIsNotNone(tool.view3d.set_height_map.call_args.args[0])
        grid = build_bilinear_grid([(0, 0, 0.1), (1, 0, 0.2), (0, 1, 0), (1, 1, 0.25)])
        dialog = SimpleNamespace(height_map=None, running=True)
        tool._simulation = dialog
        tool._console = MagicMock()
        checkbox.setChecked(False)
        tool._on_simulation_event(dialog, 'program', (['G21 G90', 'G1 X1 Y1 Z0.2'],
                                                       [(0, 0, 0.1), (1, 0, 0.2),
                                                        (0, 1, 0), (1, 1, 0.25)]))
        self.assertIsNone(tool.view3d.set_height_map.call_args.args[0])
        self.assertEqual(dialog.height_map, grid)
        for running in (True, False):
            dialog.running = running
            checkbox.setChecked(True)
            self.assertIs(tool.view3d.set_height_map.call_args.args[0], dialog.height_map)
            checkbox.setChecked(False)
            self.assertIsNone(tool.view3d.set_height_map.call_args.args[0])
        tool._simulation = None

    def test_autolevel_simulation_keeps_cutting_path_and_draws_probe_contacts_in_3d(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from shapely import Point
        from tests.test_grbl_view3d import fake_canvas
        from appPlugins.ToolGcodeSender.SenderView3d import SenderView3d
        tool = self.make_tool()
        target = SimpleNamespace(kind='cncjob', units='MM', coords_decimals=6,
                                 obj_options={'name': 'simulation'}, is_segmented_gcode=True,
                                 source_file='G21\nG90\nG0 X0 Y0 Z1\nG1 Z-0.01 F600\nG1 X1\n')
        tool._target = lambda: target
        tool.ui.al_method_radio.set_value('b')
        tool.ui.autolevel_cb.setChecked(True)
        tool.ui.ptravelz_entry.set_value(1)
        tool.ui.pdepth_entry.set_value(-1)
        tool.ui.feedrate_probe_entry.set_value(600)
        tool.al_voronoi_geo_storage = {
            i: {'point': Point(x, y), 'height': None}
            for i, (x, y) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1)))}
        tool._al_data_units = 'MM'
        tool.al_heights_valid = False
        tool._console = MagicMock()
        canvas = fake_canvas()
        tool.app.plotcanvas3d = canvas
        tool.view3d = SenderView3d(canvas)
        self.addCleanup(tool.view3d.clear)
        tool.on_simulation()
        dialog = tool._simulation
        self.addCleanup(dialog.close)
        dialog.slider.setValue(100)
        with patch('random.uniform', side_effect=[0.1, 0.2, 0.15, 0.25]):
            tool._start_simulation()
        task = tool.app.worker_task.emit.call_args.args[0]
        probe_program = tool._console().set_program.call_args.args[0]
        self.assertGreater(len(tool.view3d.path.pos), 0)
        contacts = [segment for segment in tool.view3d._segments
                    if segment[0] < len(probe_program) and not segment[3]]
        self.assertEqual([round(segment[2][2], 6) for segment in contacts], [0.1, 0.2, 0.15, 0.25])
        cutting = [segment for segment in tool.view3d._segments if segment[0] >= len(probe_program)]
        self.assertTrue(cutting)
        tool._on_simulation_event(dialog, 'progress', (4, len(probe_program)))
        self.assertGreater(len(tool.view3d.accepted.pos), 0)
        segments = list(tool.view3d._segments)
        # Opening Plot 3D during probing must retain the simulation instead of queuing a source preview.
        tool.on_show_3d()
        self.assertEqual(tool.view3d._segments, segments)
        self.assertGreater(len(tool.view3d.accepted.pos), 0)
        self.assertEqual(tool.app.worker_task.emit.call_count, 1)
        task['fcn'](*task['params'])
        self.assertFalse(dialog.running)
        self.assertGreater(len(tool.view3d.path.pos), 0)
        self.assertGreater(len(tool.view3d.accepted.pos), 0)
        self.assertIsNotNone(tool.view3d.mesh)
        self.assertTrue(any(not segment[3] and segment[2][0] == 1 for segment in tool.view3d._segments))
        dialog.close()
        tool.view3d.clear()
        tool.view3d = None
        tool.app.plotcanvas3d = None
        tool.app.worker_task.emit.reset_mock()

    def test_reset_speed_button_restores_slider_and_live_controller(self):
        tool = self.make_tool()
        tool.on_simulation()
        dialog = tool._simulation
        self.addCleanup(dialog.close)
        dialog.controller = VirtualGrbl()
        dialog.slider.setValue(100)
        self.assertEqual(dialog.controller.speed, 100)
        dialog.reset_speed_button.click()
        self.assertEqual(dialog.slider.value(), 25)
        self.assertEqual(dialog.controller.speed, 1)
        self.assertIn('1.00', dialog.speed_label.text())

    def test_fast_simulation_batches_gui_updates_without_dropping_console_lines(self):
        from unittest.mock import MagicMock
        tool = self.make_tool()
        tool.on_simulation()
        dialog = tool._simulation
        self.addCleanup(dialog.close)
        dialog.slider.setValue(100)
        tool.job_lines = ['G1 X0 F600'] * 1000
        tool.build_stream_job = lambda: True
        tool._target = lambda: type('Target', (), {'units': 'MM'})()
        tool._console = MagicMock()
        events = []
        tool.simulation_event_sig.disconnect(tool._on_simulation_event)
        tool.simulation_event_sig.connect(lambda _dialog, kind, value: events.append((kind, value)))
        tool.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
        tool._start_simulation()
        progress = [value for kind, value in events if kind == 'progress']
        console = [value for kind, value in events if kind == 'console']
        tool._on_simulation_event(dialog, 'finished', events[-1][1])
        self.assertLess(len(progress), 100)
        self.assertLess(len(console), 100)
        self.assertEqual(progress[-1], (1000, 1000))
        logged = '\n'.join(console).splitlines()
        self.assertEqual(logged.count('G1 X0 F600'), 1000)
        self.assertEqual(logged.count('ok'), 1000)


    def test_offline_simulation_and_physical_isolation(self):
        from unittest.mock import MagicMock
        tool = self.make_tool()
        tool.grbl_ser_port = MagicMock()
        tool._grbl_connected = True
        tool.on_simulation()
        dialog = tool._simulation
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.slider.value(), 25)
        tool.job_lines = ['G21 G90', 'G1 X0.01 F600']
        tool.build_stream_job = lambda: True
        tool._target = lambda: type('Target', (), {'units': 'MM'})()
        tool._console = MagicMock()
        tool.app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
        self.assertIsNone(tool._grbl_reserve('stream', motion=True))
        with tool._grbl_op_lock:
            self.assertFalse(tool._grbl_write_realtime_locked(b'~'))
        tool._start_simulation()
        self.assertFalse(dialog.running)
        self.assertEqual(tool.ui.progress_bar.value(), 100)
        tool.grbl_ser_port.write.assert_not_called()
        dialog.close()
        self.assertIsNone(tool._simulation)

    def test_offline_pause_stop_and_close_wait_for_worker(self):
        tool = self.make_tool()
        tool.on_simulation()
        dialog = tool._simulation
        tool.job_lines = ['G21 G90', 'G1 X60 F60']
        tool.build_stream_job = lambda: True
        tool._target = lambda: type('Target', (), {'units': 'MM'})()
        tool._start_simulation()
        self.assertTrue(dialog.running)
        dialog.pause_button.click()
        self.assertTrue(dialog.controller.paused)
        self.assertEqual(dialog.pause_button.text(), 'Resume')
        dialog.pause_button.click()
        self.assertFalse(dialog.controller.paused)
        task = tool.app.worker_task.emit.call_args.args[0]
        dialog.close()
        self.assertTrue(dialog.controller.cancel.is_set())
        self.assertIs(tool._simulation, dialog)
        task['fcn'](*task['params'])
        self.assertIsNone(tool._simulation)



if __name__ == '__main__':
    unittest.main()
