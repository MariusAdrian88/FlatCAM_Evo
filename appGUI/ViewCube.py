"""Clickable and draggable camera orientation overlay for the 3D plot."""

import builtins
import gettext
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ViewCube(QtWidgets.QWidget):
    fit_requested = QtCore.pyqtSignal()
    rotation_requested = QtCore.pyqtSignal(float, float)

    vertices = np.array([
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ], dtype=float)
    faces = {
        'top': (4, 5, 6, 7), 'bottom': (0, 1, 2, 3),
        'left': (0, 3, 7, 4), 'right': (1, 2, 6, 5),
        'front': (0, 1, 5, 4), 'back': (3, 2, 6, 7),
    }

    def __init__(self, camera, parent):
        super().__init__(parent)
        self.camera = camera
        self.labels = {
            'top': _('Top'), 'bottom': _('Bottom'),
            'left': _('Left'), 'right': _('Right'),
            'front': _('Front'), 'back': _('Back'),
        }
        dark_mode = np.mean(camera._viewbox.bgcolor.rgb) < 0.5
        self._icon_suffix = '_dark' if dark_mode else ''
        colors = ('#925f5f', '#5f805f', '#5f5f92') if dark_mode else ('#e58a8a', '#8ac58a', '#8a8ae5')
        self.face_colors = {
            'left': QtGui.QColor(colors[0]), 'right': QtGui.QColor(colors[0]),
            'front': QtGui.QColor(colors[1]), 'back': QtGui.QColor(colors[1]),
            'top': QtGui.QColor(colors[2]), 'bottom': QtGui.QColor(colors[2]),
        }
        self.text_color = QtGui.QColor('white' if dark_mode else 'black')
        self._hovered = None
        self._press_pos = None
        self._last_pos = None
        self._dragging = False
        self.setFixedSize(120, 120)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(_('View orientation'))
        self.setToolTip(_('Click a face to select a view. Drag to rotate around the grid center. '
                          'Right-click for all six views.'))
        self.menu = QtWidgets.QMenu(self)
        for name, label in self.labels.items():
            action = self.menu.addAction(self._view_icon(name), label)
            action.setData(name)
            action.triggered.connect(lambda checked=False, view=name: self.camera.set_view(view))
        self.menu.addSeparator()
        action = self.menu.addAction(self._view_icon('iso'), _('Iso View'))
        action.setData('iso')
        action.triggered.connect(lambda checked=False: self.camera.set_view('iso'))
        action = self.menu.addAction(self._view_icon('fit'), _('Fit View'))
        action.setData('fit')
        action.triggered.connect(lambda checked=False: self.fit_requested.emit())
        camera.transform.changed.connect(self._on_camera_change)
        parent.installEventFilter(self)
        self._place()
        self.show()

    def _view_icon(self, name):
        path = Path(__file__).resolve().parent.parent / 'assets' / 'view_cube'
        return QtGui.QIcon(str(path / ('%s%s.png' % (name, self._icon_suffix))))

    def _place(self):
        self.move(max(0, self.parentWidget().width() - self.width() - 12), 12)
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QtCore.QEvent.Type.Resize:
            self._place()
        return super().eventFilter(watched, event)

    def _on_camera_change(self, event):
        self.update()

    def _project_faces(self):
        center = np.asarray(self.camera.center)
        points = np.vstack((center, center + np.eye(3) * self.camera.scale_factor))
        projected = self.camera._scene_transform.map(points)
        projected = projected[:, :2] / projected[:, 3:]
        basis = projected[1:] - projected[0]
        scale = np.linalg.norm(basis) / np.sqrt(2)
        if not np.isfinite(scale) or scale == 0:
            return []
        basis /= scale
        toward_viewer = -np.cross(basis[:, 0], basis[:, 1])
        screen = self.vertices @ basis * 30 + (self.width() / 2, self.height() / 2)
        faces = []
        for name, indices in self.faces.items():
            normal = self.vertices[list(indices)].mean(axis=0)
            if np.dot(normal, toward_viewer) <= 1e-6:
                continue
            polygon = QtGui.QPolygonF([QtCore.QPointF(*screen[i]) for i in indices])
            faces.append((name, polygon))
        return faces

    def _face_at(self, pos):
        for name, polygon in self._project_faces():
            if polygon.containsPoint(pos, QtCore.Qt.FillRule.OddEvenFill):
                return name
        return None

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        font = painter.font()
        font.setPointSizeF(8)
        painter.setFont(font)
        for name, polygon in self._project_faces():
            hovered = name == self._hovered
            color = self.face_colors[name]
            painter.setBrush(color.lighter(125) if hovered else color)
            painter.setPen(palette.mid().color())
            painter.drawPolygon(polygon)
            painter.setPen(self.text_color)
            rect = polygon.boundingRect()
            if rect.width() >= 25 and rect.height() >= 16:
                painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, self.labels[name])

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._press_pos is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            if not self._dragging and (pos - self._press_pos).manhattanLength() >= \
                    QtWidgets.QApplication.startDragDistance():
                self._dragging = True
                self._hovered = None
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                delta = (pos - self._last_pos) * (np.pi / min(self.width(), self.height()))
                self.rotation_requested.emit(delta.x(), delta.y())
                self._last_pos = pos
                event.accept()
                return
        hovered = self._face_at(event.position())
        if hovered != self._hovered:
            self._hovered = hovered
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if hovered else QtCore.Qt.CursorShape.ArrowCursor)
            self.update()
        event.accept()

    def leaveEvent(self, event):
        self._hovered = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._press_pos = self._last_pos = event.position()
            self._dragging = False
            event.accept()
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self._press_pos is not None and not self._dragging:
                name = self._face_at(self._press_pos)
                if name is not None and name == self._face_at(event.position()):
                    self.camera.set_view(name)
            self._press_pos = self._last_pos = None
            self._dragging = False
            self._hovered = self._face_at(event.position())
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if self._hovered
                           else QtCore.Qt.CursorShape.ArrowCursor)
            self.update()
            event.accept()
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.menu.exec(event.globalPos())
        event.accept()

    def wheelEvent(self, event):
        event.accept()
