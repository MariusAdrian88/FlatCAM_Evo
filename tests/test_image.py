"""Merged from: test_image_tool: Image loading, embedded DPI, and the separate object import action.; test_image_trace: Tests for appCommon/imagetrace.py: the tracing mode of the Image import plugin (vtracer).
"""

import os
import sys
import tempfile
import unittest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image, ImageDraw
from PyQt6 import QtWidgets
from shapely.geometry import Point
from appPlugins.ToolImage import ImageGen, ToolImage
from shapely import unary_union
from appCommon.imagetrace import trace
from appParsers.SVG import svg_to_shapes


# ==============================================================================================================
# test_image_tool: Image loading, embedded DPI, and the separate object import action.
# ==============================================================================================================

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

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

    def test_preselected_image_loads_options_without_another_picker_or_import(self):
        filename = Path(self.folder.name) / 'welcome.png'
        Image.new('RGB', (8, 8)).save(filename, dpi=(300, 300))
        with patch('appPlugins.ToolImage.QtWidgets.QFileDialog.getOpenFileName',
                   side_effect=AssertionError('image already selected')):
            self.tool.on_load_image(filename=str(filename))
        self.assertEqual(self.tool.filename, str(filename))
        self.assertEqual(self.tool.ui.dpi_entry.get_value(), 300)
        self.assertTrue(self.tool.ui.import_button.isEnabled())
        self.tool.app.app_obj.new_object.assert_not_called()


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


# ==============================================================================================================
# test_image_trace: Tests for appCommon/imagetrace.py: the tracing mode of the Image import plugin (vtracer).
# ==============================================================================================================

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)

def svg_to_shape(svg_text):
    """The geometry that the plugin imports (in pixels, not flipped)."""
    geos = svg_to_shapes(svg_text, 'gerber', units='MM', resolution=16, flip=False, user_units=True)
    return unary_union([g for g in geos if g is not None and not g.is_empty])

class ImageTraceTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def save(self, img, name='img.png'):
        path = os.path.join(self.tmp.name, name)
        img.save(path)
        return path

    def test_square_outline_is_exact(self):
        img = Image.new('RGB', (64, 64), 'white')
        ImageDraw.Draw(img).rectangle((20, 20, 43, 43), fill='black')
        shape = svg_to_shape(trace(self.save(img)))
        self.assertEqual(shape.bounds, (20.0, 20.0, 44.0, 44.0))
        self.assertAlmostEqual(shape.area, 24 * 24)

    def test_enclosed_white_area_stays_a_hole(self):
        # imagetracer.js (the old svgtrace engine) filled white areas enclosed by traces: a short on a PCB
        img = Image.new('RGB', (100, 100), 'white')
        ImageDraw.Draw(img).rectangle((10, 10, 89, 89), outline='black', width=6)
        shape = svg_to_shape(trace(self.save(img)))
        self.assertEqual(len(shape.interiors), 1)
        self.assertAlmostEqual(shape.area, 80 * 80 - 68 * 68)

    def test_close_traces_are_not_bridged(self):
        img = Image.new('RGB', (120, 60), 'white')
        draw = ImageDraw.Draw(img)
        draw.rectangle((10, 20, 55, 39), fill='black')
        draw.rectangle((59, 20, 110, 39), fill='black')
        shape = svg_to_shape(trace(self.save(img)))
        self.assertEqual(shape.geom_type, 'MultiPolygon')
        self.assertEqual(len(shape.geoms), 2)

    def test_red_artwork_is_traced_in_bw(self):
        # the binary mode of vtracer looks only at the red channel: without the grayscale step this is empty
        img = Image.new('RGB', (64, 64), 'white')
        ImageDraw.Draw(img).rectangle((20, 20, 43, 43), fill=(220, 20, 60))
        self.assertAlmostEqual(svg_to_shape(trace(self.save(img))).area, 24 * 24)

    def test_transparent_background_is_white(self):
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle((20, 20, 43, 43), fill=(0, 0, 0, 255))
        self.assertAlmostEqual(svg_to_shape(trace(self.save(img))).area, 24 * 24)

    def test_threshold(self):
        img = Image.new('RGB', (64, 64), 'white')
        ImageDraw.Draw(img).rectangle((20, 20, 43, 43), fill=(150, 150, 150))
        path = self.save(img)
        self.assertEqual(trace(path, threshold=128).count('<path'), 0)
        self.assertAlmostEqual(svg_to_shape(trace(path, threshold=200)).area, 24 * 24)

    def test_native_tracer_panic_is_a_reportable_conversion_failure(self):
        class PanicException(BaseException):
            pass

        path = self.save(Image.new('RGB', (8, 8), 'black'))
        with patch('appCommon.imagetrace.vtracer.convert_raw_image_to_svg', side_effect=PanicException('overflow')):
            with self.assertRaisesRegex(ValueError, 'overflow'):
                trace(path)

if __name__ == '__main__':
    unittest.main()
