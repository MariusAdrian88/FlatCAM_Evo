"""Numeric material drawing using the Geometry editor's controls and font outlines."""

import builtins
import gettext
import math

from PyQt6 import QtCore, QtGui, QtWidgets
from shapely.affinity import rotate, translate
from shapely.geometry import box, Polygon, MultiPolygon

from appEditors.geo_plugins.GeoRectanglePlugin import RectangleEditorUI
from appEditors.geo_plugins.GeoTextPlugin import TextEditorUI
from appEditors.grb_plugins.GrbCommon import DrawToolUtilityShape, gerber_quad
from appGUI.GUIElements import FCButton, FCDoubleSpinner
from appParsers.ParseFont import ParseFont
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def rectangle_region(x, y, length, width, anchor, corner, radius, angle, quad_segs):
    values = (x, y, length, width, radius, angle)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values) or \
            length <= 0 or width <= 0 or radius < 0 or anchor not in ('c', 'tl', 'tr', 'bl', 'br') or \
            corner not in ('s', 'r', 'b') or (corner != 's' and radius * 2 >= min(length, width)):
        raise ValueError(_("Enter finite coordinates, positive dimensions and a corner radius smaller than half the size."))
    cx = x + (length / 2 if anchor in ('tl', 'bl') else -length / 2 if anchor in ('tr', 'br') else 0)
    cy = y + (width / 2 if anchor in ('bl', 'br') else -width / 2 if anchor in ('tl', 'tr') else 0)
    inset = radius if corner in ('r', 'b') else 0
    polygon = box(cx - length / 2 + inset, cy - width / 2 + inset,
                  cx + length / 2 - inset, cy + width / 2 - inset)
    if inset:
        polygon = polygon.buffer(inset, quad_segs=quad_segs, join_style='round' if corner == 'r' else 'bevel')
    return rotate(polygon, angle, origin=(x, y))


def text_region(parser, text, family, style, size, units, x, y, angle, bitmap_smoothing=0.5):
    try:
        size = float(size)
        bitmap_smoothing = float(bitmap_smoothing)
        valid = all(math.isfinite(value) for value in (size, x, y, angle)) and size > 0
        smoothing_valid = math.isfinite(bitmap_smoothing) and bitmap_smoothing >= 0
    except (TypeError, ValueError, OverflowError):
        valid = False
        smoothing_valid = False
    if not valid or not text.strip() or units.upper() not in ('MM', 'IN'):
        raise ValueError(_("Enter text, a positive font size and finite coordinates and rotation."))
    if not smoothing_valid:
        raise ValueError(_("Bitmap smoothing must be a finite nonnegative number."))
    polygon = parser.font_to_geometry(char_string=text, font_name=family, font_type=style,
                                      font_size=size, units=units.upper(), bitmap_smoothing=bitmap_smoothing)
    if isinstance(polygon, str) or polygon is None or polygon.is_empty:
        raise ValueError(_("No geometry generated from text input."))
    return translate(rotate(polygon, angle, origin=(0, 0)), xoff=x, yoff=y)


def copper_pour(editor, outline, clearance, quad_segs):
    """Subtract source material sequentially, buffering only spatial candidates."""
    if not math.isfinite(clearance) or clearance < 0 or not isinstance(outline, Polygon) or \
            outline.is_empty or not outline.is_valid or not all(math.isfinite(v) for v in outline.bounds):
        raise ValueError(_("Enter a valid polygon and finite nonnegative clearance."))
    tree, refs = editor.get_pick_index()
    if tree is None:
        return outline
    xmin, ymin, xmax, ymax = outline.bounds
    bounds = (xmin - clearance, ymin - clearance, xmax + clearance, ymax + clearance)
    if not all(math.isfinite(v) for v in bounds):
        raise ValueError(_("Clearance exceeds the coordinate range."))
    result = outline
    for index in sorted(tree.query(box(*bounds))):
        solid = refs[index][2].geo.get('solid')
        if solid is None or solid.is_empty:
            # a clear object (Native load) is no copper
            continue
        parts = solid.geoms if isinstance(solid, MultiPolygon) else [solid]
        for polygon in parts:
            if not isinstance(polygon, Polygon):
                continue
            if not polygon.is_valid:
                raise ValueError(_("Source contains invalid polygon material."))
            obstacle = polygon.buffer(clearance, quad_segs=quad_segs) if clearance else polygon
            if result.intersects(obstacle):
                result = result.difference(obstacle)
                if result.is_empty:
                    return result
    return result


class RegionDrawingPanel(QtWidgets.QWidget):
    """Numeric drawing controls hosted in the editor Properties tab."""

    def __init__(self, editor, kind):
        super().__init__(editor.ui.grb_edit_widget)
        self.editor = editor
        self.app = editor.app
        self.kind = kind
        self.session = editor.history.session
        self.units = self.app.app_units
        self.parser = None
        self._text_cache = None
        self._preview_error_key = None
        # the canvas event dispatcher reads these of the active tool
        self.name = 'drawing'
        self.complete = True
        self.clear_utility_on_none = True
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel(_("Explicit material regions. Dimensions use %s; font size uses points.") %
                                          self.app.app_units))
        if kind == 'rectangle':
            self.ui = RectangleEditorUI(layout, self)
            self.ui.anchor_radio.set_value('c')
            self.ui.corner_radio.set_value('s')
            self.ui.radius_entry.set_value(0)
            self.ui.length_entry.set_value(0)
            self.ui.width_entry.set_value(0)
            self.ui.note_description_lbl.setText(_("Creates new material; existing features aren't replaced."))
            button = self.ui.add_button
        else:
            self.ui = TextEditorUI(layout, self)
            # the output is one outline for the whole text, so mixed rich-text styles would not match the preview
            self.ui.text_input_entry.setAcceptRichText(False)
            position = QtWidgets.QFormLayout()
            layout.addLayout(position)
            self.ui.x_entry = self.coordinate_entry()
            self.ui.y_entry = self.coordinate_entry()
            position.addRow(_("Baseline X"), self.ui.x_entry)
            position.addRow(_("Baseline Y"), self.ui.y_entry)
            button = self.ui.apply_button
        for entry, value in ((self.ui.x_entry, editor.snap_x), (self.ui.y_entry, editor.snap_y)):
            entry.set_value(value if isinstance(value, (int, float)) and math.isfinite(value) else 0)
        self.angle_entry = self.coordinate_entry()
        angle_row = QtWidgets.QFormLayout()
        angle_row.addRow(_("Rotation (degrees, about anchor)"), self.angle_entry)
        layout.addLayout(angle_row)
        cancel = FCButton(_("Close"))
        cancel.clicked.connect(self.cancel)
        layout.addWidget(cancel)
        button.clicked.connect(self.on_apply)
        if kind == 'text':
            self.ui.font_type_cb.currentFontChanged.connect(self.sync_text_format)
            self.ui.font_size_cb.currentTextChanged.connect(self.sync_text_format)
            self.ui.font_bold_tb.toggled.connect(self.sync_text_format)
            self.ui.font_italic_tb.toggled.connect(self.sync_text_format)
            self.sync_text_format()

    def coordinate_entry(self):
        entry = FCDoubleSpinner()
        entry.set_precision(self.app.decimals)
        entry.set_range(-10000, 10000)
        return entry

    def _style(self):
        bold, italic = self.ui.font_bold_tb.isChecked(), self.ui.font_italic_tb.isChecked()
        return 'bi' if bold and italic else 'bold' if bold else 'italic' if italic else 'regular'

    def sync_text_format(self, *args):
        """Show the whole-text output style in the text input; mixed styles are not part of the output."""
        if self.kind != 'text':
            return
        font = self.ui.font_type_cb.currentFont()
        try:
            size = float(self.ui.font_size_cb.get_value())
            if math.isfinite(size) and size > 0:
                font.setPointSizeF(size)
        except (TypeError, ValueError):
            pass
        font.setBold(self.ui.font_bold_tb.isChecked())
        font.setItalic(self.ui.font_italic_tb.isChecked())
        self.ui.text_input_entry.setFont(font)
        # setFont only changes the base font: restyle the characters already in the document too
        char_format = QtGui.QTextCharFormat()
        char_format.setFont(font)
        cursor = self.ui.text_input_entry.textCursor()
        position = cursor.position()
        cursor.beginEditBlock()
        cursor.select(QtGui.QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(char_format)
        cursor.clearSelection()
        cursor.setPosition(position)
        cursor.endEditBlock()
        self.ui.text_input_entry.setTextCursor(cursor)
        self.ui.text_input_entry.setCurrentCharFormat(char_format)
        self._text_cache = None

    def _polygon_key(self, angle):
        if self.kind == 'rectangle':
            return (self.kind, angle, self.ui.length_entry.get_value(),
                    self.ui.width_entry.get_value(), self.ui.anchor_radio.get_value(),
                    self.ui.corner_radio.get_value(), self.ui.radius_entry.get_value())
        return (self.kind, self.ui.text_input_entry.get_value(),
                self.ui.font_type_cb.currentFont().family(), self._style(),
                self.ui.font_size_cb.get_value(), self.app.app_units,
                self.ui.bitmap_smoothing_entry.get_value())

    def _text_polygon(self, x, y, angle):
        if self.parser is None:
            self.parser = ParseFont(self.app)
            self.parser.get_fonts_by_types()
        key = (self.ui.text_input_entry.get_value(),
               self.ui.font_type_cb.currentFont().family(), self._style(),
               self.ui.font_size_cb.get_value(), self.app.app_units,
               self.ui.bitmap_smoothing_entry.get_value())
        if self._text_cache is None or self._text_cache[0] != key:
            text, family, style, size, units, smoothing = key
            polygon = text_region(self.parser, text, family, style, size, units, 0, 0, 0,
                                  bitmap_smoothing=smoothing)
            self._text_cache = (key, polygon)
        return translate(rotate(self._text_cache[1], angle, origin=(0, 0)), xoff=x, yoff=y)

    def build_polygon(self, x, y, angle):
        if self.kind == 'rectangle':
            return rectangle_region(x, y, self.ui.length_entry.get_value(), self.ui.width_entry.get_value(),
                                    self.ui.anchor_radio.get_value(), self.ui.corner_radio.get_value(),
                                    self.ui.radius_entry.get_value(), angle,
                                    gerber_quad(self.app, self.ui.radius_entry.get_value()))
        return self._text_polygon(x, y, angle)

    def on_key(self, key):
        if key in ('Escape', QtCore.Qt.Key.Key_Escape):
            self.editor.close_region_drawing()
        elif key in (QtCore.Qt.Key.Key_J, 'J'):
            self.app.on_jump_to()
        return None

    def click(self, pos):
        x, y = pos
        if all(math.isfinite(value) for value in (x, y)):
            self.ui.x_entry.set_value(x)
            self.ui.y_entry.set_value(y)
            self.editor.update_utility_geometry(data=(x, y))

    def utility_geometry(self, data=None):
        if self.session != self.editor.history.session or self.units != self.app.app_units:
            self.editor.close_region_drawing()
            return None
        if not data:
            return None
        try:
            key = self._polygon_key(self.angle_entry.get_value())
        except (ValueError, TypeError, OverflowError):
            return None
        if key == self._preview_error_key:
            return None
        try:
            polygon = self.build_polygon(data[0], data[1], self.angle_entry.get_value())
        except Exception:
            # a font or input failure at mouse-move must not spam the status bar; keep this preview empty
            self._preview_error_key = key
            return None
        self._preview_error_key = None
        if polygon is None or polygon.is_empty:
            return None
        return DrawToolUtilityShape({'solid': polygon})

    def on_apply(self):
        if self.session != self.editor.history.session or self.units != self.app.app_units:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Editor session or units changed. Reopen the drawing tool."))
            self.editor.close_region_drawing()
            return
        if not self.editor.history.ready():
            return
        try:
            x, y = self.ui.x_entry.get_value(), self.ui.y_entry.get_value()
            angle = self.angle_entry.get_value()
            polygon = self.build_polygon(x, y, angle)
            if self.editor.add_region_geometry(polygon) != 'fail':
                if self.editor.active_tool is self:
                    self.editor.update_utility_geometry(data=(x, y))
        except (ValueError, TypeError, OverflowError) as error:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(error))
        except Exception as error:
            self.app.log.error('RegionDrawingPanel.on_apply() --> %s' % str(error))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Action was not executed"), str(error)))

    def cancel(self):
        self.editor.close_region_drawing()

    def clean_up(self):
        # MainGUI's Escape handler calls clean_up() before it selects the Select tool
        self.editor.close_region_drawing(return_to_select=False)
