"""Retained-byte PNG preview with bounded decoding, fit, physical scale, zoom and pan."""

import math
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
from appGUI.GUIElements import FCFileSaveDialog

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def physical_zoom(image_dpi, screen_pixel_dpi, device_pixel_ratio):
    if not all(math.isfinite(value) and value > 0 for value in
               (image_dpi, screen_pixel_dpi, device_pixel_ratio)):
        raise ValueError(_('Invalid image or screen DPI.'))
    return screen_pixel_dpi / image_dpi / device_pixel_ratio


class ImageView(QtWidgets.QGraphicsView):
    def __init__(self, parent):
        super().__init__(parent)
        self.fitting = True
        self.setDragMode(self.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)

    def fit(self):
        self.fitting = True
        self.fitInView(self.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitting:
            self.fit()

    def wheelEvent(self, event):
        self.fitting = False
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if 0.005 <= self.transform().m11() * factor <= 100:
            self.scale(factor, factor)
        event.accept()


class ImagePreview(QtWidgets.QWidget):
    def __init__(self, data, display_name, dpi, app=None):
        super().__init__(None)
        self.app = app
        self.setObjectName('film_png_preview')
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        layout.addLayout(controls)
        fit = QtWidgets.QPushButton(_('Fit'), self)
        actual = QtWidgets.QPushButton(_('Physical 100%'), self)
        zoom_in = QtWidgets.QPushButton('+', self)
        zoom_out = QtWidgets.QPushButton('−', self)
        zoom_in.setAccessibleName(_('Zoom in'))
        zoom_out.setAccessibleName(_('Zoom out'))
        self.info = QtWidgets.QLabel(self)
        for widget in (fit, actual, zoom_out, zoom_in, self.info):
            controls.addWidget(widget)
        controls.addStretch()
        self.view = ImageView(self)
        self.scene = QtWidgets.QGraphicsScene(self)
        self.view.setScene(self.scene)
        layout.addWidget(self.view)
        fit.clicked.connect(self.view.fit)
        actual.clicked.connect(self.actual_size)
        zoom_in.clicked.connect(lambda: self.zoom(1.2))
        zoom_out.clicked.connect(lambda: self.zoom(1 / 1.2))
        self.view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)
        self.set_image(data, display_name, dpi)

    def set_image(self, data, display_name, dpi):
        if not math.isfinite(dpi) or dpi <= 0:
            raise ValueError(_('Invalid image DPI.'))
        buffer = QtCore.QBuffer()
        buffer.setData(data)
        buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
        reader = QtGui.QImageReader(buffer, b'PNG')
        size = reader.size()
        # 24 bytes/pixel plus the encoded storage fits Film's conservative 32-byte estimate.
        estimate = size.width() * size.height() * 24 + 32 * 1024 * 1024 + 2 * len(data)
        if not size.isValid() or max(size.width(), size.height()) > 32767 or estimate > 512 * 1024 * 1024:
            raise ValueError(_('PNG exceeds the preview working-memory limit.'))
        image = reader.read()
        if image.isNull():
            raise ValueError(_('Cannot decode PNG preview.'))
        pixmap = QtGui.QPixmap.fromImage(image)
        if pixmap.isNull():
            raise ValueError(_('Cannot allocate PNG preview.'))
        self.scene.clear()
        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(QtCore.QRectF(pixmap.rect()))
        self.data, self.display_name, self.dpi = bytes(data), display_name, dpi
        self.pixel_size = image.size()
        self.info.setText(_('%d × %d pixels - %g DPI') % (size.width(), size.height(), dpi))
        self.view.fit()

    def zoom(self, factor):
        self.view.fitting = False
        if 0.005 <= self.view.transform().m11() * factor <= 100:
            self.view.scale(factor, factor)

    def actual_size(self):
        screen = self.screen()
        dpr = self.devicePixelRatioF()
        # QScreen physicalDotsPerInch is in device-independent dots; convert to hardware pixels first.
        factor = physical_zoom(self.dpi, screen.physicalDotsPerInch() * dpr, dpr)
        self.view.fitting = False
        self.view.resetTransform()
        self.view.scale(factor, factor)

    def context_menu(self, position):
        menu = QtWidgets.QMenu(self)
        menu.addAction(_('Save...'), self.save)
        menu.exec(self.view.mapToGlobal(position))

    def save(self):
        if self.app is None:
            return False
        filename, _filter = FCFileSaveDialog.get_saved_filename(
            parent=self, caption=_('Save PNG'),
            directory=str(Path(self.app.get_last_save_folder()) / Path(self.display_name).name),
            ext_filter=_('PNG Files (*.png)'))
        if not filename:
            return False
        chosen = filename
        if not filename.lower().endswith('.png'):
            filename += '.png'
        # the dialog already confirmed the name it returned; only a name changed by the extension needs a question
        if filename != chosen and Path(filename).exists() and QtWidgets.QMessageBox.question(
                self, _('Overwrite file?'), _('The file already exists. Replace it?')) != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return False
        output = QtCore.QSaveFile(filename)
        if not output.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or output.write(self.data) != len(self.data):
            output.cancelWriting()
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Cannot save PNG'), output.errorString()))
            return False
        if not output.commit():
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('Cannot save PNG'), output.errorString()))
            return False
        self.app.file_saved.emit('PNG', filename)
        self.app.inform.emit('[success] %s: %s' % (_('PNG saved'), filename))
        return True
