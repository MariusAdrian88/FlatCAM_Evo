"""Image loading, embedded DPI, and the separate object import action.

Run: python tests/test_image_tool.py
"""
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PyQt6 import QtCore, QtWidgets
import numpy as np
from shapely.geometry import Point
from appPlugins.ToolImage import ImageGen

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
                self.tool.ui.dpi_entry.set_value(600)
                self.load(filename)
                self.assertEqual(self.tool.ui.dpi_entry.get_value(), 300)
                self.assertEqual(self.tool.filename, str(filename))
                self.assertEqual(self.tool.ui.load_status.color, 'green')
                self.assertTrue(self.tool.ui.import_button.isEnabled())
                self.tool.app.worker_task.emit.assert_not_called()

    def test_missing_or_invalid_dpi_resets_to_default_on_accepted_image(self):
        filename = Path(self.folder.name) / 'metadata.png'
        Image.new('RGB', (8, 8)).save(filename, dpi=(300, 300))
        self.load(filename)
        plain = filename.with_name('plain.png')
        Image.new('RGB', (8, 8)).save(plain)
        self.load(plain)
        self.assertEqual(self.tool.ui.dpi_entry.get_value(), 96)
        for metadata in (None, (), 'invalid', float('nan'), float('inf'), 0, -1, 1000000):
            with self.subTest(metadata=metadata):
                self.tool.ui.dpi_entry.set_value(600)
                image = Image.new('RGB', (8, 8))
                image.info['dpi'] = metadata
                with patch('appPlugins.ToolImage.ImageImportMixin.Image.open') as opener:
                    opener.return_value.__enter__.return_value = image
                    self.load(plain)
                self.assertEqual(self.tool.ui.dpi_entry.get_value(), 96)

    def test_exif_jpeg_dpi_overrides_current_value(self):
        filename = Path(self.folder.name) / 'exif.jpg'
        exif = Image.Exif()
        exif[282], exif[283], exif[296] = 240, 240, 2
        Image.new('RGB', (8, 8)).save(filename, exif=exif)
        self.tool.ui.dpi_entry.set_value(600)
        self.load(filename)
        self.assertEqual(self.tool.ui.dpi_entry.get_value(), 240)

    def test_both_object_types_are_offered(self):
        items = [self.tool.ui.tf_type_obj_combo.itemText(index)
                 for index in range(self.tool.ui.tf_type_obj_combo.count())]
        self.assertEqual(items, ['Gerber', 'Geometry'])

    def test_raster_mask_controls_start_synced_and_follow_mode_changes(self):
        ui = self.tool.ui
        self.assertEqual(self.tool.image_import_params()['mask'], [128, 250, 250, 250])
        for mode in ('color', 'black'):
            ui.image_type.set_value(mode)
            ui.on_image_type(mode)
            for channel in ('bw', 'r', 'g', 'b'):
                entry = getattr(ui, 'mask_%s_entry' % channel)
                self.assertEqual(entry.slider.value(), entry.get_value())
                self.assertEqual(entry.slider.isEnabled(), mode == ('black' if channel == 'bw' else 'color'))
                entry.set_value(73)
                self.assertEqual(entry.slider.value(), 73)
                entry.slider.setValue(154)
                self.assertEqual(entry.get_value(), 154)

    def test_mask_slider_updates_preview_and_coalesces_pending_work(self):
        filename = Path(self.folder.name) / 'slider.png'
        Image.new('RGB', (20, 20), (100, 100, 100)).save(filename)
        self.load(filename)
        self.tool.ui.min_area_entry.set_value(0)
        self.tool.prepare_image_preview()
        pending = self.tool.app.worker_task.emit.call_args.args[0]
        self.tool.app.worker_task.emit.reset_mock()
        for value in range(255, 49, -1):
            self.tool.ui.mask_bw_entry.slider.setValue(value)
        # A running sampled conversion can't enqueue more workers during a drag.
        self.tool.prepare_image_preview()
        self.tool.app.worker_task.emit.assert_not_called()
        pending['fcn'](*pending['params'])
        self.tool.prepare_image_preview()
        task = self.tool.app.worker_task.emit.call_args.args[0]
        task['fcn'](*task['params'])
        preview = self.tool.ui.preview_label.pixmap().toImage()
        self.assertEqual(preview.pixelColor(100, 100).red(), 255)
        self.assertEqual(self.tool.image_import_params()['mask'][0], 50)

    def test_trace_threshold_slider_sync_mode_and_preview(self):
        ui = self.tool.ui
        self.assertEqual(ui.threshold_entry.get_value(), 128)
        for mode in ('color', 'black'):
            ui.image_type.set_value(mode)
            ui.on_image_type(mode)
            self.assertEqual(ui.threshold_entry.slider.isEnabled(), mode == 'black')
        filename = Path(self.folder.name) / 'threshold.png'
        Image.new('RGB', (20, 20), (100, 100, 100)).save(filename)
        ui.import_mode_radio.set_value('trace')
        ui.min_area_entry.set_value(0)
        self.load(filename)
        for value, expected in ((50, 255), (150, 0)):
            ui.threshold_entry.set_value(128)
            self.assertEqual(ui.threshold_entry.slider.value(), 128)
            ui.threshold_entry.slider.setValue(value)
            self.assertEqual(self.tool.image_import_params()['threshold'], value)
            self.assertTrue(self.tool._preview_timer.isActive())
            self.tool.prepare_image_preview()
            task = self.tool.app.worker_task.emit.call_args.args[0]
            task['fcn'](*task['params'])
            preview = ui.preview_label.pixmap().toImage()
            self.assertEqual(preview.pixelColor(100, 100).red(), expected)

    def test_successful_load_remembers_folder_but_cancel_and_invalid_do_not(self):
        from appMain import App
        app = self.tool.app
        app.options = {'global_last_folder': 'previous'}
        app.register_folder.side_effect = lambda filename: App.register_folder(app, filename)
        app.get_last_folder.side_effect = lambda: App.get_last_folder(app)
        filename = Path(self.folder.name) / 'loaded.png'
        Image.new('RGB', (8, 8), 'white').save(filename, dpi=(300, 300))
        self.load(filename)
        for selection in ('', str(filename.with_name('invalid.png'))):
            with patch('appPlugins.ToolImage.QtWidgets.QFileDialog.getOpenFileName',
                       return_value=(selection, '')) as dialog:
                self.tool.on_load_image()
            self.assertEqual(dialog.call_args.kwargs['directory'], str(filename.parent))
            self.assertEqual(app.options['global_last_folder'], str(filename.parent))
            self.assertEqual(self.tool.filename, str(filename))
            self.assertEqual(self.tool.ui.dpi_entry.get_value(), 300)

    def test_preview_preserves_visible_tones_and_colors_after_worker(self):
        for import_mode, image_mode in product(('raster', 'trace'), ('L', 'RGB', 'P', 'RGBA')):
            with self.subTest(import_mode=import_mode, image_mode=image_mode):
                pixels = np.empty((16, 24, 3), dtype=np.uint8)
                pixels[:, :8] = (60, 100, 140)
                pixels[:, 8:16] = (180, 160, 120)
                pixels[:, :16] += np.arange(16, dtype=np.uint8)[:, None, None]
                pixels[:, 16:] = 255
                image = Image.fromarray(pixels).convert(image_mode)
                if image_mode == 'RGBA':
                    image.paste((0, 0, 0, 0), (16, 0, 24, 16))
                filename = Path(self.folder.name) / (image_mode + '.png')
                image.save(filename)
                self.tool.ui.image_type.set_value('color')
                self.tool.ui.import_mode_radio.set_value(import_mode)
                self.tool.ui.min_area_entry.set_value(0)
                self.load(filename)
                for worker in (False, True):
                    if worker:
                        self.tool.prepare_image_preview()
                        task = self.tool.app.worker_task.emit.call_args.args[0]
                        self.tool._preview_timer.stop()
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            pool.submit(task['fcn'], *task['params']).result()
                        _qapp.processEvents()
                    preview = self.tool.ui.preview_label.pixmap().toImage()
                    for index in range(3):
                        actual = preview.pixelColor(int((index + 0.5) * preview.width() / 3),
                                                    preview.height() // 2).getRgb()[:3]
                        expected = image.convert('RGB').getpixel((index * 8 + 4, 8))
                        if image_mode == 'RGBA' and index == 2:
                            expected = (255, 255, 255)
                        self.assertEqual(actual, expected, (image_mode, worker, index))

    def test_tonal_preview_masks_trace_threshold_and_orientation(self):
        filename = Path(self.folder.name) / 'tones.png'
        pixels = np.full((24, 48), 180, dtype=np.uint8)
        pixels[:, :24] = 60
        Image.fromarray(pixels).save(filename)
        self.load(filename)
        self.tool.ui.min_area_entry.set_value(0)
        for mode, mask, invert, mirror, expected in (
                ('raster', 250, False, False, (60, 180)),
                ('raster', 100, False, False, (60, 255)),
                ('raster', 250, True, True, (75, 195)),
                ('trace', 250, False, False, (0, 255))):
            with self.subTest(mode=mode, mask=mask, invert=invert):
                self.tool.ui.import_mode_radio.set_value(mode)
                self.tool.ui.mask_bw_entry.set_value(mask)
                self.tool.ui.invert_entry.setChecked(invert)
                self.tool.ui.mirror_x_entry.setChecked(mirror)
                self.tool.prepare_image_preview()
                task = self.tool.app.worker_task.emit.call_args.args[0]
                task['fcn'](*task['params'])
                preview = self.tool.ui.preview_label.pixmap().toImage()
                self.assertEqual(tuple(preview.pixelColor(preview.width() * x // 4,
                                                          preview.height() // 2).red() for x in (1, 3)), expected)

    def test_preview_and_import_share_inversion_and_mirror_orientation(self):
        filename = Path(self.folder.name) / 'asymmetric.png'
        pixels = np.array([[0, 255, 255], [255, 255, 255]], dtype=np.uint8)
        Image.fromarray(pixels).save(filename)
        self.load(filename)
        self.assertFalse(self.tool.ui.preview_label.pixmap().isNull())
        self.tool.ui.invert_entry.setChecked(True)
        self.tool.ui.mirror_x_entry.setChecked(True)
        self.tool.ui.mirror_y_entry.setChecked(True)
        self.tool.update_image_preview()
        preview = self.tool.ui.preview_label.pixmap().toImage()
        self.assertEqual(preview.pixelColor(preview.width()-1, preview.height()-1).red(), 255)
        geos = ImageGen.import_raster(str(filename), dpi=25.4, invert=True, mirror_x=True, mirror_y=True)
        self.assertAlmostEqual(sum(g.area for g in geos), 5.0)
        self.assertFalse(any(g.contains(Point(2.5, -1.5)) for g in geos))
        self.assertTrue(any(g.contains(Point(0.5, -0.5)) for g in geos))

    def test_processing_dialog_blocks_and_closes_on_completion_failure_and_cancel(self):
        for error in ('', 'failed'):
            token = self.tool.start_image_processing()
            progress = self.tool._image_progress
            self.assertTrue(progress.isVisible())
            self.assertEqual(progress.windowModality(), QtCore.Qt.WindowModality.ApplicationModal)
            self.assertEqual((progress.minimum(), progress.maximum()), (0, 0))
            self.tool.finish_image_processing(token, error)
            self.assertFalse(progress.isVisible())
        token = self.tool.start_image_processing()
        progress = self.tool._image_progress
        self.tool.cancel_image_processing()
        self.assertFalse(progress.isVisible())
        current = self.tool.start_image_processing()
        self.tool.finish_image_processing(token, '')
        self.assertTrue(self.tool._image_progress.isVisible())
        self.tool.finish_image_processing(current, '')

    def test_preview_responds_to_area_and_trace_settings(self):
        filename = Path(self.folder.name) / 'preview.png'
        Image.new('RGB', (12, 12), 'black').save(filename)
        self.load(filename)
        self.tool.ui.dpi_entry.set_value(25)
        for mode, area, expected_black in (('raster', 0, True), ('raster', 200, False),
                                           ('trace', 0, True)):
            self.tool.ui.import_mode_radio.set_value(mode)
            self.tool.ui.min_area_entry.set_value(area)
            self.tool.prepare_image_preview()
            task = self.tool.app.worker_task.emit.call_args.args[0]
            task['fcn'](*task['params'])
            pixel = self.tool.ui.preview_label.pixmap().toImage().pixelColor(100, 100)
            self.assertEqual(pixel.red() == 0, expected_black)

    def test_cancelled_main_worker_cannot_create_an_object(self):
        filename = Path(self.folder.name) / 'cancel.png'
        Image.new('RGB', (8, 8), 'black').save(filename)
        self.load(filename)
        self.tool.new_object = MagicMock()
        self.tool.on_file_importimage()
        task = self.tool.app.worker_task.emit.call_args.args[0]
        self.tool.cancel_image_processing()
        task['fcn'](*task['params'])
        self.tool.new_object.assert_not_called()
        self.assertIsNone(self.tool._image_progress)

    def test_trace_adjustments_apply_before_threshold_and_geometry(self):
        filename = Path(self.folder.name) / 'trace_adjust.png'
        pixels = np.full((8, 12), 200, dtype=np.uint8)
        pixels[:4, :4] = 10
        Image.fromarray(pixels).save(filename)
        svg = self.tool.trace_image(str(filename), 'black', 128, {}, invert=True, mirror_x=True, mirror_y=True)
        geos = self.tool.image_geometry(str(filename), 'trace', 25.4, 'black', None, svg, 0, 'MM', 'gerber')
        self.assertAlmostEqual(sum(g.area for g in geos), 80)
        self.assertFalse(any(g.contains(Point(10, -6)) for g in geos))
        self.assertTrue(any(g.contains(Point(2, -2)) for g in geos))

    def test_raster_masks_color_regions_holes_and_units_match_original_conversion(self):
        from rasterio.features import shapes
        from shapely.geometry import shape
        from shapely.affinity import scale
        pixels = np.random.default_rng(7).integers(0, 256, (80, 90, 3), dtype=np.uint8)
        pixels[2:10, 2:12] = 0
        pixels[4:8, 4:10] = 255
        filename = Path(self.folder.name) / 'regions.png'
        Image.fromarray(pixels).save(filename)
        for mode, units in (('black', 'MM'), ('color', 'IN')):
            red, green, blue = pixels.transpose(2, 0, 1)
            mask = [130, 70, 110, 160]
            included = red <= mask[0] if mode == 'black' else (
                (red <= mask[1]) | (green <= mask[2]) | (blue <= mask[3]))
            total = red if mode == 'black' else (red.astype(np.float32) + green + blue) / 3
            factor = (25.4 if units == 'MM' else 1) / 96
            expected = [scale(shape(g), factor, -factor, origin=(0, 0)) for g, val in shapes(total, mask=included)]
            actual = ImageGen.import_raster(str(filename), mode=mode, units=units, mask=mask)
            self.assertEqual(len(actual), len(expected))
            self.assertTrue(all(a.equals(b) for a, b in zip(actual, expected)))
            # Area filtering stays per tone region, not per union of adjacent colors.
            filtered = self.tool.image_path_geometries(actual, 2 * factor ** 2)
            retained = [g for g in expected if g.is_valid and g.area >= 2 * factor ** 2]
            self.assertEqual(len(filtered), len(retained))
            self.assertTrue(all(a.equals(b) for a, b in zip(filtered, retained)))
        self.assertEqual(ImageGen.raster_geometries(red, np.zeros(red.shape, dtype=bool)), [])

    def test_import_uses_loaded_file_and_current_parameters_without_dialog(self):
        filename = Path(self.folder.name) / 'image.png'
        Image.new('RGB', (8, 8)).save(filename, dpi=(300, 300))
        self.load(filename)
        self.tool.ui.dpi_entry.set_value(600)
        self.tool.ui.min_area_entry.set_value(0)
        imported = []
        def new_object(kind, name, initialize):
            obj = MagicMock()
            obj.tools = {}
            initialize(obj, self.tool.app)
            imported.append((kind, obj.solid_geometry))
        self.tool.new_object = new_object
        self.tool.app.options = {'tools_mill_tooldia': 0.2}
        for obj_type in ('Gerber', 'Geometry'):
            with self.subTest(obj_type=obj_type):
                self.tool.ui.tf_type_obj_combo.set_value(obj_type)
                with patch('appPlugins.ToolImage.QtWidgets.QFileDialog.getOpenFileName') as dialog:
                    self.tool.ui.import_button.click()
                dialog.assert_not_called()
                task = self.tool.app.worker_task.emit.call_args.args[0]
                task['fcn'](*task['params'])
                self.assertEqual(imported[-1][0], obj_type.lower())
                self.assertAlmostEqual(imported[-1][1][0].bounds[2], 8 * 25.4 / 600)
                self.assertIsNone(self.tool._image_progress)

    def test_import_builds_geometry_for_both_destinations(self):
        filename = Path(self.folder.name) / 'image.png'
        Image.new('RGB', (4, 4), 'black').save(filename, dpi=(25.4, 25.4))
        self.tool.app.options = {'tools_mill_tooldia': 0.2}
        seen = []

        def new_object(kind, name, initialize):
            obj = MagicMock()
            obj.tools, obj.solid_geometry = {}, []
            result = initialize(obj, self.tool.app)
            seen.append((kind, result, list(obj.solid_geometry)))
            return obj

        self.tool.new_object = new_object
        for obj_type in ('Geometry', 'Gerber'):
            with self.subTest(obj_type=obj_type):
                self.tool.import_image(str(filename), 'raster', obj_type, 25.4, 'black',
                                       [250, 250, 250, 250], None, 0.0, silent=True)

        self.assertEqual([entry[0] for entry in seen], ['geometry', 'gerber'])
        for kind, result, geometry in seen:
            self.assertIsNone(result)
            self.assertEqual([shape.bounds for shape in geometry], [(0.0, -4.0, 4.0, 0.0)])

if __name__ == '__main__':
    unittest.main()
