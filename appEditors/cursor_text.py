# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/24/2026                                         #
# MIT Licence                                              #
# ##########################################################

"""
The X, Y and length text near the cursor, shared by the drawing tools of the Geometry, Gerber and Excellon Editors.

The text goes on the side of the cursor away from the reference point (the last clicked point), so it does not cover
the shape that is drawn. If shapes of the editor are under that side, the next free side is used. The text box is
tested against the pick index of the editor (an STRtree that the editor makes again only when its shapes change).
"""

import math

from PyQt6 import QtCore
from shapely.geometry import box

# pixels between the cursor and the text
GAP_PX = 30
# the cursor must move this many pixels before the free side is searched again
RECHECK_PX = 8
# size of the text box in em: average width of a character, height of a line (vispy line_height)
CHAR_EM = 0.6
LINE_EM = 1.2

_state = {'font_size': None, 'last': None}


def _font_size():
    if _state['font_size'] is None:
        q_settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        _state['font_size'] = q_settings.value('hud_font_size', type=int, defaultValue=8)
    return _state['font_size']


def _sides(cursor_px, ref_px):
    """
    :return: the 4 sides (sx, sy) in the order of preference; sx = 1 is right of the cursor, sy = 1 is below it
             (the pixel y axis points down)
    """
    sx, sy = 1, -1
    if ref_px is not None:
        sx = -1 if ref_px[0] > cursor_px[0] else 1
        sy = 1 if ref_px[1] < cursor_px[1] else -1
    return [(sx, sy), (sx, -sy), (-sx, sy), (-sx, -sy)]


def _side_is_free(canvas, tree, cursor_px, side, size_px):
    if tree is None:
        return True
    sx, sy = side
    x0 = cursor_px[0] + sx * GAP_PX
    x1 = x0 + sx * size_px[0]
    y1 = cursor_px[1] + sy * size_px[1]
    ax, ay = canvas.translate_coords((x0, cursor_px[1]))[:2]
    bx, by = canvas.translate_coords((x1, y1))[:2]
    return len(tree.query(box(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)), predicate='intersects')) == 0


def show_cursor_text(draw_app, pos, length=None, ref=None):
    """
    Show the X, Y and length text near the cursor.

    :param draw_app:    the editor; it has app and get_pick_index()
    :param pos:         (x, y) of the cursor
    :param length:      the length to show; None is the distance from ref to the cursor (0 without ref)
    :param ref:         (x, y) of the reference point (the last clicked point) or None
    :return:            None
    """
    app = draw_app.app
    canvas = app.plotcanvas
    text_cursor = canvas.text_cursor
    x, y = pos[0], pos[1]
    if length is None:
        length = math.hypot(x - ref[0], y - ref[1]) if ref is not None else 0.0

    units = app.app_units.lower()
    lines = ['X:   %s [%s]' % (str(app.dec_format(x, app.decimals)) if x else '0.0', units),
             'Y:   %s [%s]' % (str(app.dec_format(y, app.decimals)) if y else '0.0', units),
             '',
             'L:   %s [%s]' % (str(app.dec_format(length, app.decimals)) if length else '0.0', units)]

    fsize = _font_size()
    cursor_px = canvas.translate_coords_2((x, y))[:2]
    ref_px = canvas.translate_coords_2(ref)[:2] if ref is not None else None
    sides = _sides(cursor_px, ref_px)

    tree, __ = draw_app.get_pick_index()
    last = _state['last']
    if last is not None and last[0] == (sides[0], id(tree)) and \
            abs(last[1][0] - cursor_px[0]) < RECHECK_PX and abs(last[1][1] - cursor_px[1]) < RECHECK_PX:
        side = last[2]
    else:
        em = fsize / 72.0 * getattr(canvas, 'dpi', 96)
        size_px = (max(len(line) for line in lines) * CHAR_EM * em, len(lines) * LINE_EM * em)
        side = next((s for s in sides if _side_is_free(canvas, tree, cursor_px, s, size_px)), sides[0])
        _state['last'] = ((sides[0], id(tree)), tuple(cursor_px), side)

    tx, ty = canvas.translate_coords((cursor_px[0] + side[0] * GAP_PX, cursor_px[1]))[:2]
    text_cursor.font_size = fsize
    text_cursor.text = '\n'.join(lines)
    text_cursor.pos = tx, ty
    # vispy anchors are in the y up space of the plot: 'top' puts the text above the point
    text_cursor.anchors = 'left' if side[0] > 0 else 'right', 'top' if side[1] < 0 else 'bottom'

    if text_cursor.parent is None:
        text_cursor.parent = canvas.view.scene


def hide_cursor_text(app):
    """
    Remove the cursor text from the canvas. The next text reads the font size again.
    """
    app.plotcanvas.text_cursor.parent = None
    app.plotcanvas.view.camera.zoom_callback = lambda *args: None
    _state['font_size'] = None
    _state['last'] = None
