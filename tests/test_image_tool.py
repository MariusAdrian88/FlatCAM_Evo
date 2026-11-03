"""Image loading, embedded DPI, and the separate object import action.

Run: python tests/test_image_tool.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PyQt6 import QtWidgets

from appPlugins.ToolImage import ToolImage


_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestImageLoading(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        app = MagicMock()
        app.decimals = 4
        app.app_units = 'MM'
        app.resource_location = 'assets/resources'
        app.get_last_folder.return_value = self.folder.name
        with patch.object(ToolImage, 'add_object_creation_controls'):
            self.tool = ToolImage(app)
            self.tool.set_tool_ui()
        self.addCleanup(self.tool.deleteLater)

    def load(self, filename):
        with patch('appPlugins.ToolImage.QtWidgets.QFileDialog.getOpenFileName',
                   return_value=(str(filename), '')):
            self.tool.ui.load_button.click()

    def test_embedded_dpi_for_supported_formats_loads_without_importing(self):
        for extension in ('png', 'jpg', 'bmp'):
            with self.subTest(extension=extension):
                filename = Path(self.folder.name) / ('image.' + extension)
                Image.new('RGB', (8, 8)).save(filename, dpi=(300, 150))
                self.load(filename)
                self.assertEqual(self.tool.ui.dpi_entry.get_value(), 300)
                self.assertEqual(self.tool.filename, str(filename))
                self.assertEqual(self.tool.ui.load_status.color, 'green')
                self.assertTrue(self.tool.ui.import_button.isEnabled())
                self.tool.app.worker_task.emit.assert_not_called()

    def test_import_uses_loaded_file_and_current_parameters_without_dialog(self):
        filename = Path(self.folder.name) / 'image.png'
        Image.new('RGB', (8, 8)).save(filename, dpi=(300, 300))
        self.load(filename)
        self.tool.ui.dpi_entry.set_value(600)
        for obj_type in ('Gerber', 'Geometry'):
            with self.subTest(obj_type=obj_type):
                self.tool.ui.tf_type_obj_combo.set_value(obj_type)
                with patch('appPlugins.ToolImage.QtWidgets.QFileDialog.getOpenFileName') as dialog:
                    self.tool.ui.import_button.click()
                dialog.assert_not_called()
                task = self.tool.app.worker_task.emit.call_args.args[0]
                self.assertEqual(task['fcn'], self.tool.import_image)
                self.assertEqual(task['params'][:4], [str(filename), 'raster', obj_type, 600])

if __name__ == '__main__':
    unittest.main()
