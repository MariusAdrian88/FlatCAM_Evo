"""Calibrated dispensing: real geometry, volume arithmetic, controller output and UI."""
import copy
import math
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from shapely import LineString, Point, Polygon, box
from shapely.ops import unary_union

from tests.test_solderpaste import make_app, make_job, make_tool
from appPlugins.ToolSolderPaste import SolderPaste
from appCommon.solderpaste import parse_numbers, extract_pads, pad_paths, parameters, generate_program


def settings(**kwargs):
    data = copy.deepcopy(make_app().options)
    data.update({
        'tools_solderpaste_volume_mode': 'Thickness',
        'tools_solderpaste_placement': 'Auto',
        'tools_solderpaste_controller': 'Mach3',
        'tools_solderpaste_drive': 'Motor',
        'tools_solderpaste_thickness': 0.1,
        'tools_solderpaste_flow_rate': 0.01,
        'tools_solderpaste_overlap': 30.0,
        'tools_solderpaste_dot_dia': 0.2,
        'tools_solderpaste_z_travel': 2.0,
        'tools_solderpaste_z_toolchange': 5.0,
        'tools_solderpaste_z_rise': False,
        'tools_solderpaste_prime': 0.0,
        'tools_solderpaste_dwellrev': 0.1,
    })
    data.update({'tools_solderpaste_' + name: value for name, value in kwargs.items()})
    return data


def program(data, paths=None, volumes=None, units='MM'):
    job = make_job()
    job.units = units
    tool = {'data': data, 'tooldia': 0.3,
            'solid_geometry': paths or [LineString([(1, 1), (2, 1)])],
            'dispense_volumes': volumes or [0.1]}
    return generate_program(job, tool, is_first=True)


class CalibratedPasteTests(unittest.TestCase):
    def test_literal_coordinates_and_rejection(self):
        self.assertEqual(parse_numbers('(1.5, -2)', count=2), [1.5, -2.0])
        self.assertEqual(parse_numbers([1, 2], count=2), [1.0, 2.0])
        self.assertEqual(parse_numbers('', count=2, allow_empty=True), [])
        for value in ('1+2, 3', '__import__("os").getcwd()', '[1, float("inf")]',
                      '1, 2, 3', [True, 2], [1, math.nan]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_numbers(value, count=2)

    def test_regions_clear_areas_and_overlaps(self):
        source = SimpleNamespace(tools={
            0: {'geometry': [{'solid': box(0, 0, 4, 4), 'follow': box(0, 0, 4, 4).exterior}]},
            1: {'geometry': [{'solid': box(3, 0, 5, 4), 'follow': Point(4, 2)},
                             {'clear': box(1, 1, 2, 2), 'follow': Point(1.5, 1.5)}]},
            2: {'geometry': [{'solid': box(20, 20, 21, 21), 'follow': LineString([(20, 20), (21, 21)])}]}
        })
        pads = extract_pads(source)
        self.assertEqual(len(pads), 1)
        self.assertAlmostEqual(pads[0].area, 19.0)
        self.assertFalse(pads[0].covers(Point(1.5, 1.5)))

    def test_raster_is_inside_pad_and_preserves_total_volume(self):
        pad = Polygon(box(0, 0, 5, 4).exterior.coords, [box(2, 1, 3, 3).exterior.coords])
        paths, volumes = pad_paths(pad, 0.3, settings(placement='Raster'))
        self.assertGreater(len(paths), 5)
        self.assertAlmostEqual(sum(volumes), pad.area * 0.1)
        for path in paths:
            self.assertTrue(pad.buffer(-0.15).buffer(1e-9).covers(path))
        coverage = unary_union([path.buffer(0.15) for path in paths])
        self.assertLess(pad.buffer(-0.3).difference(coverage).area, 0.01)

    def test_fixed_volume_and_inches_use_physical_units(self):
        paths, volumes = pad_paths(box(0, 0, 1, 1), 0.1, settings(), units='IN')
        self.assertAlmostEqual(sum(volumes), 25.4 ** 2 * 0.1)
        paths, volumes = pad_paths(box(0, 0, 2, 2), 0.3,
                                  settings(volume_mode='Fixed volume', volume=0.75))
        self.assertAlmostEqual(sum(volumes), 0.75)

    def test_all_motor_controllers_and_dwell_units(self):
        for controller in ('Mach3', 'Mach4', 'LinuxCNC', 'GRBL', 'Marlin'):
            with self.subTest(controller=controller):
                code = program(settings(controller=controller))
                self.assertIn('G90', code)
                self.assertIn('S300', code)
                self.assertNotIn('F1.0000\nG1 Z0.1000', code)
                self.assertIn('PASTE_ON', code)
                self.assertIn('PASTE_OFF', code)
                self.assertIn('G1 X2.0000 Y1.0000 F6.0000', code)
        self.assertIn('G4 P100.0000', program(settings(mach3_dwell='Milliseconds')))
        self.assertIn('G4 S0.1000', program(settings(controller='Marlin')))


    def test_marlin_stepper_and_retraction_recovery(self):
        data = settings(controller='Marlin', drive='Stepper', axis='E', axis_mode='Linear',
                        syringe_dia=10, retract=0.02, marlin_cold=True)
        code = program(data, paths=[Point(1, 1), Point(2, 1)], volumes=[0.01, 0.01])
        self.assertIn('M82', code)
        self.assertIn('G92 E0', code)
        self.assertIn('E-', code)
        self.assertNotIn('G93', code)
        self.assertEqual(code.count('PASTE_ON'), 2)

    def test_invalid_settings_fail_before_output(self):
        # flow_rate=100: an excess flow must fail, not overdose the paste silently
        invalid = [dict(flow_rate=0), dict(flow_rate=100), dict(calibration=math.nan), dict(overlap=100),
                   dict(drive='Stepper', controller='GRBL'), dict(axis='X', drive='Stepper'),
                   dict(controller='Marlin', drive='Stepper', axis='E', marlin_cold=False),
                   dict(controller='GRBL', grbl_mode='Laser'), dict(z_travel=0.01),
                   dict(controller='Unsupported'), dict(drive='Stepper', syringe_dia=0)]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                program(settings(**changes))


    def test_motor_volume_from_actual_commands_includes_prime(self):
        data = settings(prime=0.01, calibration=1.2)
        job = make_job()
        job._bed_skew_x = 5.0
        path = LineString([(1, 1), (2, 2), (3, 2)])
        code = generate_program(job, {'tooldia': 0.3, 'data': data,
                                      'solid_geometry': [path], 'dispense_volumes': [0.1]}, True)
        x, y = 0.0, 0.0
        dispensing = False
        elapsed = 0.0
        for line in code.splitlines():
            if line == '(PASTE_ON)':
                dispensing = True
            elif line == '(PASTE_OFF)':
                dispensing = False
            move = re.match(r'G1 X([-\d.]+) Y([-\d.]+) F([\d.]+)', line)
            if move:
                nx, ny, feed = map(float, move.groups())
                if dispensing:
                    elapsed += math.hypot(nx - x, ny - y) * 60 / feed
                x, y = nx, ny
            dwell = re.match(r'G4 P([\d.]+)', line)
            if dwell and dispensing:
                elapsed += float(dwell[1])
        self.assertAlmostEqual(elapsed * 0.01, 0.12, places=5)

    def test_saved_cncjob_restores_paste_role_and_dialect(self):
        job = make_job()
        job.special_group = 'solder_paste_tool'
        job.pp_solderpaste_name = 'Paste_Marlin'
        job.tools = {1: {'tooldia': 0.3, 'type': 'SolderPaste',
                         'data': settings(controller='Marlin'),
                         'solid_geometry': [Point(1, 1)], 'dispense_volumes': [0.01]}}
        saved = job.to_dict()
        restored = make_job()
        restored.from_dict(saved)
        self.assertEqual(getattr(restored, 'special_group', None), 'solder_paste_tool')
        self.assertEqual(restored.pp_solderpaste_name, 'Paste_Marlin')
        self.assertEqual(restored.tools[1]['dispense_volumes'], [0.01])

if __name__ == '__main__':
    unittest.main()
