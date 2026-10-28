import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6 import QtWidgets
from shapely.geometry import LineString, Point, Polygon

from appHandlers.appIO import appIO
from appParsers.ParseSprintLayout import SprintLayoutParser
from test_sprint_layout_parser import fu, board, pad_at, sprint_file, BOARD_MM


FILE = os.path.join('D:' + os.sep, 'boards', 'cata.lay6')


def _layer(name, polygon):
    return dict(name=name, solid_geometry=[polygon], follow_geometry=[LineString([(0, 0), (10, 0)])],
                tools={0: dict(type='REG', size=0.0, geometry=[{'solid': polygon}])})


class TestSprintLayoutImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
                                   options={}, app_units='MM', new_project_signal=Mock(),
                                   abort_flag=False, app_obj=Mock(), plot_all=Mock(), file_opened=Mock(),
                                   ui=QtWidgets.QWidget(), get_last_folder=Mock(return_value=''))
        self.app.projects = SimpleNamespace(is_busy=lambda: False,
                                            active=SimpleNamespace(collection=SimpleNamespace(get_list=lambda: [])))
        self.app.project_filename, self.app.should_we_save = None, False
        self.io = appIO(self.app)
        polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        # Same layer keys, names and default selection as the cata.lay6 fixture, plus one non-default layer.
        self.parsed = SimpleNamespace(
            layers={
                'C1': _layer('Copper Top', polygon),
                'C2': _layer('Copper Bottom', polygon),
                'M1': _layer('Solder Mask Top', polygon),
                'M2': _layer('Solder Mask Bottom', polygon),
                'S1': _layer('Silkscreen Top', polygon),
                'S2': _layer('Silkscreen Bottom', polygon),
                'O': _layer('Board Outline', polygon),
            },
            drill_groups={'PTH': {1: dict(tooldia=0.7, drills=[Point(2, 2)], slots=[])}},
            default_layers=['C1', 'C2', 'M1', 'M2', 'S1', 'O'],
            warnings=[])
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj_ = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj_, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj_))
            return obj_

        self.app.app_obj.new_object.side_effect = create
        self.io.export_gerber = Mock(return_value='G04*\nM02*\n')
        self.io.export_excellon = Mock(return_value='M48\nM30\n')

    def tearDown(self):
        self.qapp.processEvents()

    def test_sprint_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(FILE, self.parsed, ['C1', 'M1', 'O'], ['PTH'])
        self.assertEqual([obj_.obj_options['name'] for kind, obj_ in self.objects],
                         ['cata_C1', 'cata_M1', 'cata_O', 'cata_PTH'])
        self.assertEqual([kind for kind, obj_ in self.objects], ['gerber', 'gerber', 'gerber', 'excellon'])
        for kind, obj_ in self.objects:
            with self.subTest(name=obj_.obj_options['name']):
                self.assertTrue(obj_.obj_options['pcbdoc_import'])
                self.assertEqual(obj_.units, 'MM')

    def test_inch_import_scales_drill_tooldia_and_sets_units(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects(FILE, self.parsed, ['C1'], ['PTH'])
        excellon = [obj_ for kind, obj_ in self.objects if kind == 'excellon'][0]
        self.assertEqual(excellon.units, 'IN')
        self.assertAlmostEqual(excellon.tools[1]['tooldia'], 0.7 / 25.4, places=6)

    def test_dialog_accept_queues_only_selected_layers_and_drill_groups(self):
        self.app.ui = QtWidgets.QWidget()

        def accepted(dialog):
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text() == 'M1 (Solder Mask Top)':
                    box.setChecked(False)
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accepted):
            self.io.on_pcbdoc_parsed(FILE, self.parsed)
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['fcn'], self.io.import_pcbdoc_objects)
        self.assertEqual(job['params'][0], FILE)
        self.assertEqual(job['params'][2:], [['C1', 'C2', 'M2', 'S1', 'O'], ['PTH']])

    def test_parse_sprint_layout_error_reports_and_returns_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = os.path.join(directory, 'bad.lay6')
            with open(bad, 'wb') as handle:
                handle.write(b'JUNK' + bytes(50))
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertEqual(self.io.parse_sprint_layout(bad), 'fail')
        parsed_signal.emit.assert_not_called()
        self.app.inform.emit.assert_called_once()
        message = self.app.inform.emit.call_args.args[0]
        self.assertIn('[ERROR_NOTCL]', message)
        self.app.log.error.assert_called_once()

    def test_parse_sprint_layout_success_emits_parsed_board(self):
        data = sprint_file([board([(2, pad_at(10.0, 10.0))], name=b'Test', w=fu(BOARD_MM), h=fu(BOARD_MM))])
        with tempfile.TemporaryDirectory() as directory:
            good = os.path.join(directory, 'good.lay6')
            with open(good, 'wb') as handle:
                handle.write(data)
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertIsNone(self.io.parse_sprint_layout(good))
        parsed_signal.emit.assert_called_once()
        filename, parsed = parsed_signal.emit.call_args.args
        self.assertEqual(filename, good)
        self.assertIsInstance(parsed, SprintLayoutParser)
        self.assertIn('C1', parsed.layers)
        self.app.inform.emit.assert_not_called()

    def test_on_file_import_sprint_layout_queues_parse_for_given_name(self):
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName') as chooser:
            self.io.on_file_import_sprint_layout(FILE)
        chooser.assert_not_called()
        self.app.worker_task.emit.assert_called_once_with({'fcn': self.io.parse_sprint_layout,
                                                           'params': [FILE]})

if __name__ == '__main__':
    unittest.main()
