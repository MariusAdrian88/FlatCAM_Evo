import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, Point, Polygon

from appHandlers.appIO import appIO
from appParsers.ParseKiCadPcb import KiCadPcbParser


BOARD = os.path.join('D:' + os.sep, 'boards', 'Easyduino_RP2040.kicad_pcb')
MINIMAL_BOARD = '''(kicad_pcb (version 20240108) (generator "test")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen") (25 "Edge.Cuts" user))
 (segment (start 0 0) (end 5 0) (width 0.3) (layer "F.Cu") (net 1)))
'''


class TestKiCadPcbImport(unittest.TestCase):
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
        polygon = Polygon([(-25.4, 0), (25.4, 0), (25.4, 25.4), (-25.4, 25.4)],
                          [[(-5, 5), (5, 5), (5, 10), (-5, 10)]])
        self.parsed = SimpleNamespace(
            layers={
                'F.Cu': dict(name='F.Cu', solid_geometry=[polygon],
                             follow_geometry=[LineString([(0, 0), (25.4, 0)])],
                             tools={10: dict(type='C', size=2.54, geometry=[])}),
                'In1.Cu': dict(name='In1.Cu', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'F.Fab': dict(name='F.Fab', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'F.SilkS': dict(name='F.Silkscreen', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'Edge.Cuts': dict(name='Edge.Cuts', solid_geometry=[polygon], follow_geometry=[], tools={}),
            },
            drill_groups={'PTH_F.Cu-B.Cu': {1: dict(tooldia=2.54, drills=[Point(25.4, 0)],
                                                    slots=[(Point(0, 0), Point(25.4, 0))])}},
            default_layers=['F.Cu', 'In1.Cu', 'Edge.Cuts', 'F.SilkS'],
            warnings=[])
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.io.export_gerber = Mock(return_value='G04*\nM02*\n')
        self.io.export_excellon = Mock(return_value='M48\nM30\n')

    def tearDown(self):
        self.qapp.processEvents()

    def test_kicad_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['F.Cu'], ['PTH_F.Cu-B.Cu'])
        self.assertEqual([obj.obj_options['name'] for kind, obj in self.objects],
                         ['Easyduino_RP2040_F_Cu', 'Easyduino_RP2040_PTH_F_Cu-B_Cu'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'excellon'])
        for kind, obj in self.objects:
            with self.subTest(name=obj.obj_options['name']):
                self.assertTrue(obj.obj_options['pcbdoc_import'])
                self.assertEqual(obj.units, 'MM')

    def test_dialog_accept_queues_only_selected_layers_and_drill_groups(self):
        self.app.ui = QtWidgets.QWidget()

        def accepted(dialog):
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text() == 'In1.Cu':
                    box.setChecked(False)
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accepted):
            self.io.on_pcbdoc_parsed(BOARD, self.parsed)
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['fcn'], self.io.import_pcbdoc_objects)
        self.assertEqual(job['params'][0], BOARD)
        self.assertEqual(job['params'][2:], [['F.Cu', 'F.SilkS', 'Edge.Cuts'], ['PTH_F.Cu-B.Cu']])

    def test_parse_kicad_pcb_error_reports_and_returns_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = os.path.join(directory, 'bad.kicad_pcb')
            with open(bad, 'w', encoding='utf-8') as handle:
                handle.write('(foo)')
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertEqual(self.io.parse_kicad_pcb(bad), 'fail')
        parsed_signal.emit.assert_not_called()
        self.app.inform.emit.assert_called_once()
        message = self.app.inform.emit.call_args.args[0]
        self.assertIn('[ERROR_NOTCL]', message)
        self.app.log.error.assert_called_once()

    def test_parse_kicad_pcb_success_emits_parsed_board(self):
        with tempfile.TemporaryDirectory() as directory:
            good = os.path.join(directory, 'good.kicad_pcb')
            with open(good, 'w', encoding='utf-8') as handle:
                handle.write(MINIMAL_BOARD)
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertIsNone(self.io.parse_kicad_pcb(good))
        parsed_signal.emit.assert_called_once()
        filename, parsed = parsed_signal.emit.call_args.args
        self.assertEqual(filename, good)
        self.assertIsInstance(parsed, KiCadPcbParser)
        self.assertIn('F.Cu', parsed.layers)
        self.app.inform.emit.assert_not_called()

    def test_on_file_import_kicad_pcb_queues_parse_for_given_name(self):
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName') as chooser:
            self.io.on_file_import_kicad_pcb(BOARD)
        chooser.assert_not_called()
        self.app.worker_task.emit.assert_called_once_with({'fcn': self.io.parse_kicad_pcb,
                                                           'params': [BOARD]})

if __name__ == '__main__':
    unittest.main()
