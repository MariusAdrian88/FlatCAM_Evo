"""PNG export keeps the VisPy pixels without requiring a GPU or backend flag."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

from appHandlers.appIO import appIO


def make_handler(open_style=False):
    app = SimpleNamespace(
        options={'global_open_style': open_style}, defaults={},
        get_last_save_folder=lambda: 'saved', file_opened=Mock(), file_saved=Mock())
    return SimpleNamespace(app=app, log=Mock(), inform=Mock())


@pytest.mark.parametrize('channels,open_style', [(3, False), (4, True)])
def test_export_preserves_pixels_without_backend_flag(tmp_path, channels, open_style):
    handler = make_handler(open_style)
    pixels = np.array([[[255, 0, 31, 255], [0, 255, 63, 128]],
                       [[17, 32, 255, 0], [91, 73, 55, 64]]], dtype=np.uint8)[:, :, :channels]
    filename = str(tmp_path / 'capture.png')
    with patch('appHandlers.appIO._screenshot', return_value=pixels) as screenshot, \
            patch('appHandlers.appIO.FCFileSaveDialog.get_saved_filename',
                  return_value=(filename, '')) as dialog:
        appIO.on_file_export_png(handler)
    screenshot.assert_called_once_with(alpha=False)
    assert dialog.call_args.kwargs['directory'].startswith('saved/png_')
    with Image.open(filename) as image:
        np.testing.assert_array_equal(np.asarray(image), pixels)
    handler.app.file_saved.emit.assert_called_once_with('png', filename)
    if open_style:
        handler.app.file_opened.emit.assert_not_called()
    else:
        handler.app.file_opened.emit.assert_called_once_with('png', filename)

