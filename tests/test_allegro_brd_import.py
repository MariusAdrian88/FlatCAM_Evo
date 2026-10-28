import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtWidgets

from appHandlers.appIO import appIO
from appParsers.ParseAllegroBrd import AllegroBrdParser


BOARD = str(Path(__file__).parent / 'fixtures' / 'orcad_17.2' / 'proiectCAD.brd')
EAGLE = '''<?xml version="1.0" encoding="utf-8"?>
<eagle version="8.3.2"><drawing><layers><layer number="1" name="Top"/><layer number="16" name="Bottom"/></layers>
<board><plain><wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1"/></plain></board></drawing></eagle>
'''


class TestAllegroBrdImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.parsed = AllegroBrdParser().parse_file(BOARD)

    def setUp(self):
        self.app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
                                   options={}, app_units='MM', new_project_signal=Mock(),
                                   abort_flag=False, app_obj=Mock(), plot_all=Mock(), file_opened=Mock(),
                                   ui=QtWidgets.QWidget(), get_last_folder=Mock(return_value=''))
        self.app.projects = SimpleNamespace(is_busy=lambda: False,
                                            active=SimpleNamespace(collection=SimpleNamespace(get_list=lambda: [])))
        self.app.project_filename, self.app.should_we_save = None, False
        self.io = appIO(self.app)
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

    # ------------------------------------------------------------------ .brd content sniffing

    def _file(self, directory, name, data):
        path = os.path.join(directory, name)
        with open(path, 'wb') as handle:
            handle.write(data)
        return path

    def test_source_kind_tells_allegro_from_eagle_by_content(self):
        with tempfile.TemporaryDirectory() as directory:
            allegro = self._file(directory, 'board.brd', b'\x00\x04\x14\x00' + bytes(100))
            eagle = self._file(directory, 'eagle.brd', EAGLE.encode('utf-8'))
            eagle_bom = self._file(directory, 'bom.brd', b'\xef\xbb\xbf  \r\n\t' + EAGLE.encode('utf-8'))
            eagle_upper = self._file(directory, 'UPPER.BRD', EAGLE.encode('utf-8'))
            allegro_upper = self._file(directory, 'ALLEGRO.BRD', b'\x00\x02\x15\x00' + bytes(100))
            empty = self._file(directory, 'empty.brd', b'')
            blanks = self._file(directory, 'blanks.brd', b'  \r\n')
            self.assertEqual(appIO._pcb_source_kind(allegro), 'allegro_brd')
            self.assertEqual(appIO._pcb_source_kind(allegro_upper), 'allegro_brd')
            self.assertEqual(appIO._pcb_source_kind(BOARD), 'allegro_brd')
            self.assertEqual(appIO._pcb_source_kind(eagle), 'eagle_brd')
            self.assertEqual(appIO._pcb_source_kind(eagle_bom), 'eagle_brd')
            self.assertEqual(appIO._pcb_source_kind(eagle_upper), 'eagle_brd')
            self.assertEqual(appIO._pcb_source_kind(empty), 'eagle_brd')
            self.assertEqual(appIO._pcb_source_kind(blanks), 'eagle_brd')

    # ------------------------------------------------------------------ objects

    def test_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['TOP', 'SOLDERMASK_TOP'], ['PTH', 'NPTH'])
        self.assertEqual([obj.obj_options['name'] for kind, obj in self.objects],
                         ['proiectCAD_TOP', 'proiectCAD_SOLDERMASK_TOP', 'proiectCAD_PTH', 'proiectCAD_NPTH'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'gerber', 'excellon', 'excellon'])
        for kind, obj in self.objects:
            with self.subTest(name=obj.obj_options['name']):
                self.assertTrue(obj.obj_options['pcbdoc_import'])
                self.assertEqual(obj.units, 'MM')
        top = self.objects[0][1]
        self.assertTrue(top.solid_geometry)
        pth = self.objects[2][1]
        self.assertEqual(sum(len(tool['drills']) for tool in pth.tools.values()), 26)

    def test_inch_project_converts_objects_to_inch(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['OUTLINE'], ['NPTH'])
        kind, obj = self.objects[0]
        self.assertEqual(obj.units, 'IN')
        self.assertAlmostEqual(max(polygon.bounds[2] for polygon in obj.solid_geometry), 70.005 / 25.4, places=3)

    # ------------------------------------------------------------------ dialog

    def test_dialog_accept_queues_only_selected_layers_and_drill_groups(self):
        def accepted(dialog):
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text() == 'BOTTOM (ETCH/BOTTOM)':
                    box.setChecked(False)
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accepted):
            self.io.on_pcbdoc_parsed(BOARD, self.parsed)
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['fcn'], self.io.import_pcbdoc_objects)
        self.assertEqual(job['params'][0], BOARD)
        self.assertEqual(job['params'][2:], [['TOP', 'SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM', 'SILKSCREEN_TOP',
                                              'OUTLINE'], ['PTH', 'NPTH']])

    # ------------------------------------------------------------------ parse entry points

    def test_parse_allegro_brd_error_reports_and_returns_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = self._file(directory, 'bad.brd', b'\x00\x04\x14\x00' + bytes(40))
            with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
                self.assertEqual(self.io.parse_allegro_brd(bad), 'fail')
        parsed_signal.emit.assert_not_called()
        self.app.inform.emit.assert_called_once()
        self.assertIn('[ERROR_NOTCL]', self.app.inform.emit.call_args.args[0])
        self.app.log.error.assert_called_once()
        self.assertEqual(self.app.log.error.call_args.args[1], 'Allegro Board')

    def test_parse_allegro_brd_success_emits_parsed_board(self):
        with patch.object(self.io, 'pcbdoc_parsed') as parsed_signal:
            self.assertIsNone(self.io.parse_allegro_brd(BOARD))
        parsed_signal.emit.assert_called_once()
        filename, parsed = parsed_signal.emit.call_args.args
        self.assertEqual(filename, BOARD)
        self.assertIsInstance(parsed, AllegroBrdParser)
        self.assertIn('TOP', parsed.layers)
        self.assertIs(parsed.app, self.app)
        self.app.inform.emit.assert_not_called()

    def test_on_file_import_allegro_brd_queues_parse_for_given_name(self):
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName') as chooser:
            self.io.on_file_import_allegro_brd(BOARD)
        chooser.assert_not_called()
        self.app.worker_task.emit.assert_called_once_with({'fcn': self.io.parse_allegro_brd, 'params': [BOARD]})

if __name__ == '__main__':
    unittest.main()
