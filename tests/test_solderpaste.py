"""Merged from: test_solderpaste: Solder-paste generation regressions. Run with python -m unittest tests.test_solderpaste.; test_solderpaste_calibrated: Calibrated dispensing: real geometry, volume arithmetic, controller output and UI.
"""

import copy
import logging
import os
import unittest
import math
import re
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch, mock_open
from PyQt6 import QtWidgets
from shapely import LineString, Point, Polygon, box
from shapely.affinity import rotate
from appPlugins.ToolSolderPaste import SolderPaste
from appGUI.GUIElements import GLay, FCSpinner, FCComboBox
from camlib import CNCjob
from defaults import AppDefaults
from preprocessors.Paste_1 import Paste_1
from preprocessors.Paste_GRBL import Paste_GRBL
from preprocessors.Paste_Marlin import Paste_Marlin
from shapely.ops import unary_union
from appCommon.solderpaste import parse_numbers, extract_pads, pad_paths, parameters, generate_program


# ==============================================================================================================
# test_solderpaste: Solder-paste generation regressions. Run with python -m unittest tests.test_solderpaste.
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

def make_app():
    options = copy.deepcopy(AppDefaults.factory_defaults)
    options['tools_solderpaste_volume_mode'] = 'Legacy'
    return SimpleNamespace(
        options=options, decimals=4, app_units='MM',
        log=logging.getLogger('solderpaste-test'), inform=Mock(), abort_flag=False, plotcanvas=Mock(),
        proc_container=MagicMock(), preprocessors={
            'default': Paste_1(), 'Paste_1': Paste_1(),
            'Paste_GRBL': Paste_GRBL(), 'Paste_Marlin': Paste_Marlin()
        }
    )

def make_tool(pads, diameters=(0.3,)):
    app = make_app()
    tool = SimpleNamespace(
        app=app, decimals=4, units='MM', flat_geometry=[], obj_options={},
        tooltable_tools={uid: {
            'tooldia': dia, 'data': copy.deepcopy(app.options), 'solid_geometry': []
        } for uid, dia in enumerate(diameters, 1)}
    )
    source = SimpleNamespace(tools={1: {'geometry': [
        {'solid': pad, 'follow': Point(pad.centroid)} for pad in pads
    ]}})
    objects = []

    def new_object(kind, name, initializer, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name}, solid_geometry=[])
        result = initializer(obj, app)
        if result != 'fail':
            objects.append(obj)
        return result

    tool.new_object = new_object
    tool.objects = objects
    tool.source = source
    return tool

def make_job(app=None):
    app = app or make_app()
    job = CNCjob(app, units='MM')
    job.obj_options = {'type': 'Geometry', 'xmin': 0, 'ymin': 0, 'xmax': 100, 'ymax': 100}
    job.coords_decimals = 4
    job.fr_decimals = 2
    job.tool = 1
    return job

def nozzle_data(job, paths):
    data = {'tooldia': 0.3, 'data': copy.deepcopy(job.app.options), 'solid_geometry': paths}
    job.tools = {1: data}
    return data

class SolderPasteTests(unittest.TestCase):
    def test_nozzle_settings_drive_motor_and_dwell(self):
        job = make_job()
        for name in ('Paste_1', 'Paste_GRBL', 'Paste_Marlin'):
            with self.subTest(name=name):
                data = nozzle_data(job, [LineString([(1, 1), (2, 1)])])
                data['data']['tools_solderpaste_pp'] = name
                job.spindlespeed = None
                job.dwelltime = 0
                code = job.generate_gcode_from_solder_paste_geo(is_first=True, **data)
                self.assertIn('G90', code)
                self.assertRegex(code, r'M0?3 S300(?:\.0)?\b')
                self.assertIn('G4 S1.0' if name == 'Paste_Marlin' else 'G4 P1.0', code)
                self.assertRegex(code, r'M0?4 S200(?:\.0)?\b')


    def test_absolute_paste_coordinates_ignore_global_incremental_option(self):
        job = make_job()
        job.app.options['cncjob_coords_type'] = 'G91'
        code = job.generate_gcode_from_solder_paste_geo(is_first=True, **nozzle_data(
            job, [LineString([(10, 10), (12, 10)]), LineString([(20, 10), (22, 10)])]
        ))
        self.assertIn('G90', code)
        self.assertIn('X20.0000 Y10.0000', code)


    def test_marlin_uses_comments_and_manual_nozzle_change(self):
        job = make_job()
        data = nozzle_data(job, [LineString([(1, 1), (2, 1)])])
        data['data']['tools_solderpaste_pp'] = 'Paste_Marlin'
        code = job.generate_gcode_from_solder_paste_geo(is_first=True, **data)
        self.assertNotIn('M6', code)
        self.assertFalse(any(line.startswith('T') for line in code.splitlines()))
        self.assertFalse(any(line.startswith('(') for line in code.splitlines()))

    def test_header_keeps_a_name_with_parentheses_inside_its_comment(self):
        from appPlugins.ToolSolderPaste.SolderPasteGen import gcode_header
        # a ')' in the name ended the '(Name: ...)' comment early; the rest of the line was read as G-code
        header = gcode_header('1', '1', 'board (1)-F_Cu.gbr_paste', 'Geometry', 'MM', 'today', 'Paste_GRBL')
        self.assertIn('(Name: board [1]-F_Cu.gbr_paste)\n', header)

    def test_split_pad_paths_generate_separate_dispensing_moves(self):
        pad = Polygon(box(0, 0, 4, 2).exterior.coords, [box(1.5, 0.5, 2.5, 1.5).exterior.coords])
        tool = make_tool([pad])
        SolderPaste.on_create_geo(tool, 'pads', tool.source, use_thread=False)
        job = make_job(tool.app)
        data = tool.objects[0].tools[1]
        job.tools = {1: data}
        code = job.generate_gcode_from_solder_paste_geo(is_first=True, **data)
        self.assertEqual(code.count('M03 S'), 2)
        self.assertEqual(code.count('M04 S'), 2)


    def test_impossible_nozzle_rejects_incomplete_board(self):
        tool = make_tool([box(0, 0, 0.1, 0.1)])
        result = SolderPaste.on_create_geo(tool, 'pads', tool.source, use_thread=False)
        self.assertEqual(result, 'fail')
        self.assertFalse(tool.objects)

    def test_pad_holes_are_not_filled(self):
        pad = Polygon(box(0, 0, 4, 2).exterior.coords, [box(1.5, 0.5, 2.5, 1.5).exterior.coords])
        tool = make_tool([pad])
        SolderPaste.on_create_geo(tool, 'pads', tool.source, use_thread=False)
        self.assertTrue(tool.objects)
        for path in tool.objects[0].tools[1]['solid_geometry']:
            self.assertTrue(pad.buffer(-0.15).covers(path))

if __name__ == '__main__':
    unittest.main()


# ==============================================================================================================
# test_solderpaste_calibrated: Calibrated dispensing: real geometry, volume arithmetic, controller output and UI.
# ==============================================================================================================

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
