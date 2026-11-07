# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

"""Loading, settings and conversion shared by the Image Import tool and its Geometry Editor subtool."""

from PyQt6 import QtCore, QtGui, QtWidgets

from appPlugins.ToolImage.ImageUI import ImageUI
from appGUI.GUIElements import FCSliderWithSpinner
from appPlugins.ToolImage import ImageGen
import appPlugins.ToolImage as image_plugin

from PIL import Image
from PIL.ImageQt import ImageQt
import numpy as np
import shapely

from appCommon.imagetrace import PRESETS, PRESET_NAMES, CURVE_MODES
from appParsers.SVG import SvgImportError
from camlib import object_circle_steps

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ImagePreviewSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(int, object)


class ImageImportMixin:
    """The common part of a tool that turns an image into geometry with the ImageUI settings."""

    def build_image_import_ui(self):
        if getattr(self, '_preview_timer', None) is not None:
            self._preview_timer.stop()
        else:
            self._preview_timer = QtCore.QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(300)
            self._preview_timer.timeout.connect(self.prepare_image_preview)
        self._preview_image = None
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        if not hasattr(self, '_preview_signals'):
            self._preview_signals = ImagePreviewSignals(self)
            self._preview_signals.finished.connect(self.on_image_preview_finished)
            self._preview_running = False
        self.ui = ImageUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_image_import_signals()
        self.set_image_import_defaults()
        for widget in vars(self.ui).values():
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox, FCSliderWithSpinner)):
                widget.valueChanged.connect(self.on_image_settings_changed)
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.toggled.connect(self.on_image_settings_changed)
            elif isinstance(widget, QtWidgets.QComboBox):
                widget.currentIndexChanged.connect(self.on_image_settings_changed)
        for widget in (self.ui.image_type, self.ui.import_mode_radio, self.ui.control_radio):
            widget.activated_custom.connect(self.on_image_settings_changed)

    def on_image_settings_changed(self, *args):
        if self.filename:
            self._preview_generation += 1
            self.on_image_loaded(self.filename)
            self._preview_timer.start()

    def update_image_preview(self):
        self._preview_timer.stop()
        if self._preview_image is None:
            return
        params = self.image_import_params()
        image = self.image_preview_source(params)
        pixels = np.array(image)
        if params['import_mode'] == 'raster':
            total, mask = ImageGen.raster_values(*pixels.transpose(2, 0, 1), params['mode'], params['mask'])
            pixels[~mask] = 255
            if params['mode'] == 'black':
                pixels[:] = pixels[:, :, :1]
        elif params['mode'] == 'black':
            gray = np.array(image.convert('L'))
            pixels[:] = np.where(gray[:, :, None] < params['threshold'], 0, 255)
        pixmap = QtGui.QPixmap.fromImage(ImageQt(Image.fromarray(pixels).convert('RGBA')))
        self.ui.preview_label.setPixmap(pixmap.scaled(320, 200, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                                     QtCore.Qt.TransformationMode.FastTransformation))

    def image_preview_source(self, params):
        image = ImageGen.adjust_image(self._preview_image, **self.image_adjustments(params))
        flat = Image.new('RGBA', image.size, 'white')
        flat.alpha_composite(image.convert('RGBA'))
        return flat.convert('RGB')

    def prepare_image_preview(self):
        """One bounded worker at a time; never use this sampled geometry for import."""
        if self._preview_image is None:
            return
        self.update_image_preview()
        if self._preview_running:
            return
        self._preview_running = True
        token = self._preview_generation
        params = self.image_import_params()
        image = self.image_preview_source(params)
        dpi = params['dpi'] * image.width / self._image_size[0]
        units = self.app.app_units
        factor = (25.4 if units.lower() == 'mm' else 1) / dpi
        signal = self._preview_signals.finished

        def job():
            try:
                tones = image
                if params['import_mode'] == 'raster':
                    pixels = np.array(image.convert('RGB'))
                    total, mask = ImageGen.raster_values(*pixels.transpose(2, 0, 1), params['mode'], params['mask'])
                    geos = ImageGen.raster_geometries(total, mask, factor)
                    if params['mode'] == 'black':
                        tones = image.getchannel('R').convert('RGB')
                else:
                    from appCommon.imagetrace import trace_image
                    svg = trace_image(image, params['mode'] == 'black', params['threshold'], params['trace_options'])
                    geos = self.import_image_as_trace_handler(svg, obj_type='gerber', units=units, dpi=dpi)
                    if geos == 'fail':
                        raise ValueError(_("Failed."))
                    if params['mode'] == 'black':
                        tones = image.convert('L').point(lambda value: 0 if value < params['threshold'] else 255)
                geos = self.image_path_geometries(list(self.flatten_list(geos)), params['min_area'])
                result = QtGui.QImage(image.width, image.height, QtGui.QImage.Format.Format_ARGB32)
                result.fill(QtCore.Qt.GlobalColor.white)
                painter = QtGui.QPainter(result)
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                # Show retained image tones, not the union of every color region as a black silhouette.
                painter.setBrush(QtGui.QBrush(ImageQt(tones.convert('RGBA'))))
                try:
                    for geometry in geos:
                        path = QtGui.QPainterPath()
                        rings = [geometry.exterior, *geometry.interiors] if geometry.geom_type == 'Polygon' else [geometry]
                        for ring in rings:
                            coords = list(ring.coords)
                            if not coords:
                                continue
                            path.moveTo(coords[0][0] / factor, -coords[0][1] / factor)
                            for x, y, *rest in coords[1:]:
                                path.lineTo(x / factor, -y / factor)
                        painter.drawPath(path)
                finally:
                    painter.end()
                signal.emit(token, result)
            except Exception as err:
                signal.emit(token, str(err))

        self.app.worker_task.emit({'fcn': job, 'params': []})

    def on_image_preview_finished(self, token, result):
        self._preview_running = False
        if token != self._preview_generation:
            if self._preview_image is not None:
                self._preview_timer.start()
            return
        if isinstance(result, str):
            self.ui.preview_label.clear()
            self.ui.preview_label.setToolTip(result)
            return
        pixmap = QtGui.QPixmap.fromImage(result)
        self.ui.preview_label.setPixmap(pixmap.scaled(320, 200, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                                     QtCore.Qt.TransformationMode.FastTransformation))

    def stop_image_preview(self):
        self._preview_timer.stop()
        self._preview_generation += 1
        self._preview_image = None

    @staticmethod
    def image_adjustments(params):
        return {key: params.get(key, False) for key in ('invert', 'mirror_x', 'mirror_y')}

    def start_image_processing(self):
        ImageImportMixin.cancel_image_processing(self)
        token = self._image_process_token
        parent = getattr(self.app, 'ui', None)
        parent = parent if isinstance(parent, QtWidgets.QWidget) else self
        progress = QtWidgets.QProgressDialog(_("Converting the image..."), _("Cancel"), 0, 0, parent)
        progress.setWindowTitle(_("Image Import"))
        progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self.cancel_image_processing)
        self._image_progress = progress
        self.ui.import_button.setEnabled(False)
        progress.show()
        return token

    def finish_image_processing(self, token, error=''):
        if token != getattr(self, '_image_process_token', 0):
            return
        self.cancel_image_processing()
        if error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)

    def cancel_image_processing(self):
        self._image_process_token = getattr(self, '_image_process_token', 0) + 1
        progress = getattr(self, '_image_progress', None)
        self._image_progress = None
        if progress is not None:
            progress.canceled.disconnect()
            progress.close()
            progress.deleteLater()
        self.ui.import_button.setEnabled(bool(self.filename))

    def connect_image_import_signals(self):
        self.ui.load_button.clicked.connect(self.on_load_image)
        self.ui.import_button.clicked.connect(self.on_image_import_action)
        self.ui.image_type.activated_custom.connect(self.ui.on_image_type)

    def set_image_import_defaults(self):
        self.ui.dpi_entry.set_value(96)
        self.ui.image_type.set_value('black')
        self.ui.on_image_type(val=self.ui.image_type.get_value())
        self.ui.min_area_entry.set_value(0.3)
        self.ui.import_mode_radio.set_value('raster')
        self.ui.on_import_image_mode(val=self.ui.import_mode_radio.get_value())
        self.ui.control_radio.set_value('presets')
        self.ui.on_tracing_control_radio(val=self.ui.control_radio.get_value())
        self.ui.mask_bw_entry.set_value(128)
        self.ui.mask_r_entry.set_value(250)
        self.ui.mask_g_entry.set_value(250)
        self.ui.mask_b_entry.set_value(250)
        self.ui.threshold_entry.set_value(128)
        self.ui.presets_combo.set_value(0)
        self.ui.on_preset_changed(0)

    def on_load_image(self, filename=None):
        _filter = "Image Files(*.BMP *.PNG *.JPG *.JPEG);;" \
                  "Bitmap File (*.BMP);;" \
                  "PNG File (*.PNG);;" \
                  "Jpeg File (*.JPG);;" \
                  "All Files (*.*)"
        if filename is None or isinstance(filename, bool):
            # clicked(bool) still opens the picker; the welcome screen supplies an already selected path.
            try:
                filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load Image"),
                                                                     directory=self.app.get_last_folder(), filter=_filter)
            except TypeError:
                filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Load Image"), filter=_filter)

        filename = str(filename)
        if not filename:
            self.app.inform.emit(_("Cancelled."))
            return

        previous_filename = self.filename
        previous_enabled = self.ui.import_button.isEnabled()
        self.ui.import_button.setEnabled(False)
        try:
            with Image.open(filename) as image:
                image.load()
                image_dpi = image.info.get('dpi', 0)
                image_size = image.size
                preview = image.copy()
                preview.thumbnail((320, 200))
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as err:
            self.ui.load_status.color = 'red'
            self.ui.load_status.setToolTip('%s\n%s' % (filename, str(err)))
            self.app.log.error("ImageImportMixin.on_load_image() -> %s" % str(err))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to load image"), str(err)))
            if previous_filename:
                self.filename = previous_filename
                self.ui.load_status.color = 'green'
                self.ui.load_status.setToolTip(previous_filename)
                self.ui.import_button.setEnabled(previous_enabled)
            return

        accepted_dpi = 96
        try:
            dpi = float(image_dpi[0] if isinstance(image_dpi, (tuple, list)) else image_dpi)
            if np.isfinite(dpi) and 1 <= round(dpi) <= self.ui.dpi_entry.maximum():
                accepted_dpi = round(dpi)
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
        self.ui.dpi_entry.set_value(accepted_dpi)

        self.filename = filename
        self.app.register_folder(filename)
        self.ui.load_status.color = 'green'
        self.ui.load_status.setToolTip(filename)
        self.ui.import_button.setEnabled(True)
        self.on_image_loaded(filename)
        if hasattr(self.ui, 'preview_label'):
            self._preview_image = preview
            self._image_size = image_size
            self._preview_generation += 1
            self.update_image_preview()
            self._preview_timer.start()

    def on_image_loaded(self, filename):
        """Hook: a new image was accepted and replaced the previous one."""
        pass

    def image_import_params(self):
        return {
            'filename': self.filename,
            'import_mode': self.ui.import_mode_radio.get_value(),
            'trace_options': PRESETS[PRESET_NAMES[self.ui.presets_combo.get_value()]]
            if self.ui.control_radio.get_value() == 'presets' else self.get_tracing_options(),
            'dpi': self.ui.dpi_entry.get_value(),
            'mode': self.ui.image_type.get_value(),
            'min_area': self.ui.min_area_entry.get_value(),
            'mask': [self.ui.mask_bw_entry.get_value(), self.ui.mask_r_entry.get_value(),
                     self.ui.mask_g_entry.get_value(), self.ui.mask_b_entry.get_value()],
            'threshold': self.ui.threshold_entry.get_value(),
            'invert': self.ui.invert_entry.isChecked(),
            'mirror_x': self.ui.mirror_x_entry.isChecked(),
            'mirror_y': self.ui.mirror_y_entry.isChecked()
        }

    def trace_image(self, filename, mode, threshold, trace_options, **adjustments):
        if any(adjustments.values()):
            from appCommon.imagetrace import trace_image
            with Image.open(filename) as image:
                return trace_image(ImageGen.adjust_image(image, **adjustments), mode == 'black', threshold,
                                   trace_options)
        # Preserve the legacy package-level monkeypatch target.
        return image_plugin.trace(filename, black_white=(mode == 'black'), threshold=threshold,
                                  options=trace_options)

    def image_geometry(self, filename, import_mode, dpi, mode, mask, svg_text, min_area, units,
                       obj_type='geometry', keep_paths=False, **adjustments):
        if import_mode == 'raster':
            image_geo = self.import_image_handler(filename, units=units, dpi=dpi, mode=mode, mask=mask, **adjustments)
        else:
            image_geo = self.import_image_as_trace_handler(svg_text=svg_text, obj_type=obj_type, units=units, dpi=dpi)
        if not image_geo:
            return []
        if image_geo == 'fail':
            return 'fail'
        geos = list(self.flatten_list(image_geo))
        if keep_paths:
            return self.image_path_geometries(geos, min_area)
        geos = np.asarray(geos, dtype=object)
        return list(geos[shapely.is_valid(geos) & ~shapely.is_empty(geos) & (shapely.area(geos) >= min_area)])

    def image_path_geometries(self, geos, min_area):
        """Editor geometry: keep paths and apply the minimum area to each polygon leaf."""
        polygons = np.asarray(geos, dtype=object)
        if np.all(shapely.get_type_id(polygons) == 3):
            return list(polygons[shapely.is_valid(polygons) & ~shapely.is_empty(polygons) &
                                 (shapely.area(polygons) >= min_area)])
        result = []

        def collect(geometry):
            if geometry is None or geometry.is_empty or not geometry.is_valid or geometry.geom_type == 'Point':
                return
            if geometry.geom_type == 'Polygon':
                if geometry.area >= min_area:
                    result.append(geometry)
            elif geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
                for part in geometry.geoms:
                    collect(part)
            else:
                result.append(geometry)

        for geometry in geos:
            collect(geometry)
        return result

    def import_image_handler(self, filename, flip=True, units='MM', dpi=96, mode='black', mask=None, **adjustments):
        """Import masked raster shapes in application units."""
        geos = ImageGen.import_raster(filename, flip=flip, units=units, dpi=dpi, mode=mode, mask=mask,
                                      raster_opener=image_plugin.rasterio_open, **adjustments)
        if mode == 'black':
            self.app.log.debug("Image import as monochrome.")
        else:
            thresholds = mask if mask is not None else [128, 128, 128, 128]
            self.app.log.debug("Image import as colored. Thresholds are: R = %s , G = %s, B = %s" %
                               (str(thresholds[1]), str(thresholds[2]), str(thresholds[3])))
        return geos

    def import_image_as_trace_handler(self, svg_text, obj_type='geometry', flip=True, units='MM', dpi=96):
        """Parse traced SVG pixels, then scale to application units."""
        # every circle gets the steps of its own radius (in the output units)
        options = self.app.options
        res = lambda radius: object_circle_steps(options, 'geometry', radius, units)
        try:
            geos = image_plugin.svg_to_shapes(svg_text, obj_type, units=units, resolution=res, flip=flip,
                                              app=self.app, user_units=True)
        except SvgImportError as e:
            self.app.log.error("ImageImportMixin.import_image_as_trace_handler(). %s" % str(e))
            return 'fail'
        self.app.log.debug("ImageImportMixin.import_image_as_trace_handler(). Finished parsing the SVG geometry.")
        return ImageGen.scale_geometry(geos, units=units, dpi=dpi)

    def get_tracing_options(self):
        return {
            'mode': CURVE_MODES[self.ui.curve_combo.get_value()],
            'filter_speckle': self.ui.speckle_entry.get_value(),
            'color_precision': self.ui.color_precision_entry.get_value(),
            'layer_difference': self.ui.layer_diff_entry.get_value(),
            'corner_threshold': self.ui.corner_entry.get_value(),
            'length_threshold': self.ui.length_entry.get_value(),
            'splice_threshold': self.ui.splice_entry.get_value()
        }

    def flatten_list(self, obj_list):
        yield from ImageGen.flatten_list(obj_list)
