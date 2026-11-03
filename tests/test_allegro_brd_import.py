import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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

    # ------------------------------------------------------------------ parse entry points

if __name__ == '__main__':
    unittest.main()
