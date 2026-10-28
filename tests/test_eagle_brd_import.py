import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtWidgets
from shapely.geometry import LineString, Point, Polygon

from appHandlers.appIO import appIO
from appParsers.ParseEagleBrd import EagleBrdParser


BOARD = os.path.join('D:' + os.sep, 'boards', 'Pcb39.brd')
GOOD_BOARD = '''<?xml version="1.0" encoding="utf-8"?>
<eagle version="8.3.2"><drawing>
<layers><layer number="1" name="Top"/><layer number="16" name="Bottom"/><layer number="51" name="tDocu"/></layers>
<board><plain><wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1"/>
<wire x1="0" y1="2" x2="10" y2="2" width="0.5" layer="51"/><hole x="5" y="5" drill="3.2"/></plain>
<signals><signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" diameter="0.6" shape="round"/></signal></signals>
</board></drawing></eagle>
'''


class TestEagleBrdImport(unittest.TestCase):
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
        polygon = Polygon([(0, 0), (10, 0), (10, 5), (0, 5)])
        self.parsed = SimpleNamespace(
            layers={
                'Top': dict(name='layer 1', solid_geometry=[polygon],
                            follow_geometry=[LineString([(0, 0), (10, 0)])],
                            tools={0: dict(type='REG', size=0.0, geometry=[])}),
                'Bottom': dict(name='layer 16', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'tStop': dict(name='layer 29', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'Empty': dict(name='layer 9', solid_geometry=[], follow_geometry=[], tools={}),
            },
            drill_groups={
                'PTH': {1: dict(tooldia=0.8, drills=[Point(1, 1)], slots=[])},
                'NPTH': {1: dict(tooldia=3.2, drills=[Point(2, 2)], slots=[])},
            },
            default_layers=['Top', 'Bottom', 'tStop'],
            warnings=['Text is drawn with an approximate stroke font'])
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

    def test_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['Top', 'tStop'], ['PTH', 'NPTH'])
        self.assertEqual([obj.obj_options['name'] for kind, obj in self.objects],
                         ['Pcb39_Top', 'Pcb39_tStop', 'Pcb39_PTH', 'Pcb39_NPTH'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'gerber', 'excellon', 'excellon'])
        for kind, obj in self.objects:
            with self.subTest(name=obj.obj_options['name']):
                self.assertTrue(obj.obj_options['pcbdoc_import'])
                self.assertEqual(obj.units, 'MM')

    def test_inch_project_converts_objects_to_inch(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['Top'], ['PTH'])
        kind, obj = self.objects[0]
        self.assertEqual(obj.units, 'IN')

    def test_dialog_accept_queues_only_selected_layers_and_drill_groups(self):
        def accepted(dialog):
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text() == 'Bottom (layer 16)':
                    box.setChecked(False)
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accepted):
            self.io.on_pcbdoc_parsed(BOARD, self.parsed)
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['fcn'], self.io.import_pcbdoc_objects)
        self.assertEqual(job['params'][0], BOARD)
        self.assertEqual(job['params'][2:], [['Top', 'tStop'], ['PTH', 'NPTH']])

    def test_parse_eagle_brd_error_reports_and_returns_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = os.path.join(directory, 'bad.brd')
            with open(bad, 'w', encoding='utf-8') as handle:
                handle.write('<eagle version="9.0"><drawing><schematic/></drawing></eagle>')
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertEqual(self.io.parse_eagle_brd(bad), 'fail')
        parsed_signal.emit.assert_not_called()
        self.app.inform.emit.assert_called_once()
        message = self.app.inform.emit.call_args.args[0]
        self.assertIn('[ERROR_NOTCL]', message)
        self.app.log.error.assert_called_once()

    def test_parse_eagle_brd_success_emits_parsed_board(self):
        with tempfile.TemporaryDirectory() as directory:
            good = os.path.join(directory, 'good.brd')
            with open(good, 'w', encoding='utf-8') as handle:
                handle.write(GOOD_BOARD)
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertIsNone(self.io.parse_eagle_brd(good))
        parsed_signal.emit.assert_called_once()
        filename, parsed = parsed_signal.emit.call_args.args
        self.assertEqual(filename, good)
        self.assertIsInstance(parsed, EagleBrdParser)
        self.assertIn('Top', parsed.layers)
        self.app.inform.emit.assert_not_called()

    def test_on_file_import_eagle_brd_queues_parse_for_given_name(self):
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName') as chooser:
            self.io.on_file_import_eagle_brd(BOARD)
        chooser.assert_not_called()
        self.app.worker_task.emit.assert_called_once_with({'fcn': self.io.parse_eagle_brd,
                                                           'params': [BOARD]})

if __name__ == '__main__':
    unittest.main()
