"""Tests for the Project combobox above the Project tab tree."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6 import QtCore, QtWidgets


class FakeManager(QtCore.QObject):
    projects_changed = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.contexts = [SimpleNamespace(name='Board_A'), SimpleNamespace(name='Board_B')]
        self.active = self.contexts[0]
        self.app = SimpleNamespace(inform=Mock(), f_handlers=SimpleNamespace(on_file_open_project=Mock(),
                                                                                on_file_save_project=Mock()),
                                   resource_location='assets/resources')
        self.busy = False
        self.switch = Mock(side_effect=self._switch)
        self.new = Mock()
        self.rename = Mock(return_value=True)
        self.request_close = Mock()
        self.request_close_all = Mock()

    def _switch(self, ctx):
        self.active = ctx
        return True

    def names(self):
        return [ctx.name for ctx in self.contexts]

    def is_busy(self):
        return self.busy

    def inform_busy(self):
        self.app.inform.emit('busy')


class TestProjectSelector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make(self):
        from appGUI.ProjectSelector import ProjectSelector
        manager = FakeManager()
        return manager, ProjectSelector(manager)


    def test_import_dialog_lists_the_board_imports_and_runs_the_choice(self):
        from appGUI.ImportDialog import ImportDialog
        handlers = SimpleNamespace(on_file_import_kicad_pcb=Mock())
        app = SimpleNamespace(f_handlers=handlers, resource_location='assets/resources', pdf_tool=None,
                              image_tool=None)
        dialog = ImportDialog(app)
        keys = dialog.keys()
        for key in ('svg_geometry', 'dxf_gerber', 'hpgl2', 'pcbdoc', 'proteus', 'kicad_pcb', 'sprint_layout',
                    'eagle_brd', 'allegro_brd'):
            self.assertIn(key, keys)
        # a plugin that is not installed has no entry
        self.assertNotIn('pdf', keys)
        self.assertNotIn('image', keys)

        dialog.select('kicad_pcb')
        self.assertEqual(dialog.ext_label.text(), '.kicad_pcb')
        dialog.accept()
        dialog.run_selected()
        handlers.on_file_import_kicad_pcb.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
