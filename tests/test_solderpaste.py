"""Solder-paste generation regressions. Run with python -m unittest tests.test_solderpaste."""
import copy
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch, mock_open

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

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

    def test_reverse_dwell_precedes_motor_stop(self):
        job = make_job()
        code = job.generate_gcode_from_solder_paste_geo(**nozzle_data(
            job, [LineString([(1, 1), (2, 1)])]
        ))
        reverse = code.index('M04')
        self.assertLess(code.index('G4 P1.0', reverse), code.index('M05', reverse))

    def test_absolute_paste_coordinates_ignore_global_incremental_option(self):
        job = make_job()
        job.app.options['cncjob_coords_type'] = 'G91'
        code = job.generate_gcode_from_solder_paste_geo(is_first=True, **nozzle_data(
            job, [LineString([(10, 10), (12, 10)]), LineString([(20, 10), (22, 10)])]
        ))
        self.assertIn('G90', code)
        self.assertIn('X20.0000 Y10.0000', code)

    def test_empty_tool_returns_failure(self):
        job = make_job()
        self.assertEqual(job.generate_gcode_from_solder_paste_geo(**nozzle_data(job, [])), 'fail')

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

    def test_marlin_export_uses_comments_and_job_units(self):
        job = make_job()
        job.special_group = 'solder_paste_tool'
        job.pp_solderpaste_name = 'Paste_Marlin'
        job.tools = {1: {'gcode': 'G21\nG90\n'}}
        app = make_app()
        app.collection = SimpleNamespace(get_by_name=lambda name: job)
        app.version = 'test'
        app.version_date = 'test'
        app.get_last_save_folder = lambda: ''
        app.file_opened = Mock()
        app.file_saved = Mock()
        tool = SimpleNamespace(app=app, units='IN', ui=SimpleNamespace(
            cnc_obj_combo=SimpleNamespace(currentText=lambda: 'paste')
        ))
        opened = mock_open()
        with patch('appPlugins.ToolSolderPaste.FCFileSaveDialog.get_saved_filename',
                   return_value=('paste.nc', '')), patch('builtins.open', opened):
            SolderPaste.on_save_gcode(tool)
        code = ''.join(call.args[0] for call in opened().write.call_args_list)
        self.assertIn(';Units: MM', code)
        self.assertFalse(any(line.startswith('(') for line in code.splitlines()))

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
