import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets


def write_icon(path, colors):
    image = QtGui.QImage(len(colors), 1, QtGui.QImage.Format.Format_RGBA8888)
    for x, color in enumerate(colors):
        image.setPixelColor(x, 0, QtGui.QColor(*color))
    assert image.save(str(path))


class TestIconColors(unittest.TestCase):
    def test_only_single_color_icons_change_and_remain_recolorable(self):
        from appCommon.IconColors import recolor_icons

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mono = [(102, 173, 199, 255), (51, 87, 100, 128), (0, 0, 0, 0)]
            for name in ('tool.png', 'tool.ico', 'drill16_project.png', 'axis18.png',
                         'settings18.png', 'hud18.png', 'graylight12.png', 'active_static.png', 'red32.png',
                         'grid18_status.png'):
                write_icon(root / name, mono)
            write_icon(root / 'neutral.png', [(227, 233, 237, 255), (110, 114, 116, 64)])
            write_icon(root / 'white.png', [(255, 255, 255, 255)])
            write_icon(root / 'mixed.png', [(255, 0, 0, 255), (0, 255, 0, 255)])
            write_icon(root / 'logo.png', [(255, 0, 0, 255), (255, 255, 255, 255)])
            (root / 'dark_resources').mkdir()
            write_icon(root / 'dark_resources' / 'tool.png', mono)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}

            self.assertEqual(recolor_icons(root, '#aabbcc'), 4)
            for name in ('tool.png', 'tool.ico', 'neutral.png', 'white.png'):
                image = QtGui.QImage(str(root / name))
                self.assertEqual(image.pixelColor(0, 0).name(), '#aabbcc')
            self.assertEqual(QtGui.QImage(str(root / 'tool.png')).pixelColor(1, 0).alpha(), 128)
            self.assertEqual(QtGui.QImage(str(root / 'tool.png')).pixelColor(2, 0).alpha(), 0)
            for path, original in before.items():
                if path.name not in ('tool.png', 'tool.ico', 'neutral.png', 'white.png') or path.parent != root:
                    self.assertEqual(path.read_bytes(), original, path.name)
            self.assertEqual(recolor_icons(root, '#223344'), 4)
            self.assertEqual(QtGui.QImage(str(root / 'tool.png')).pixelColor(0, 0).name(), '#223344')
            self.assertEqual(recolor_icons(root, '#223344'), 0)
            with self.assertRaises(ValueError):
                recolor_icons(root, '#123')

    def test_invalid_image_does_not_change_any_files(self):
        from appCommon.IconColors import recolor_icons

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_icon(root / 'a.png', [(255, 255, 255, 255)])
            (root / 'z.png').write_bytes(b'invalid image')
            original = (root / 'a.png').read_bytes()
            with self.assertRaises(OSError):
                recolor_icons(root, '#66adc7')
            self.assertEqual((root / 'a.png').read_bytes(), original)

    def test_failed_write_restores_previously_changed_icons(self):
        from appCommon.IconColors import recolor_icons, _write_icon

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a.png', 'b.png'):
                write_icon(root / name, [(255, 255, 255, 255)])
            before = {p: p.read_bytes() for p in root.iterdir()}

            def fail_second(path, data):
                if path.name == 'b.png':
                    raise OSError('Test write failure')
                _write_icon(path, data)

            with patch('appCommon.IconColors._write_icon', side_effect=fail_second):
                with self.assertRaisesRegex(OSError, 'Test write failure'):
                    recolor_icons(root, '#66adc7')
            for path, original in before.items():
                self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
