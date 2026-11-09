# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 10/21/2026                                         #
# MIT Licence                                              #
# ##########################################################

"""
Corner (bend) modes shared by the polyline drawing tools of the Gerber Editor (Track, Region) and of the
Geometry Editor (Path, Polygon).

Modes:  1 = 45 degrees, 2 = reverse 45 degrees, 3 = 90 degrees, 4 = reverse 90 degrees, 5 = free angle
"""

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

BEND_MODE_FREE = 5

_NEXT_MODE = {1: 2, 2: 3, 3: 4, 4: 5, 5: 1}
_PREV_MODE = {1: 5, 2: 1, 3: 2, 4: 3, 5: 4}


def corner_point(mode, old, new, tol=1e-9):
    """
    Compute the corner between two points from their real coordinates (the points need not be on the grid).

    :param mode:    corner mode, 1 to 5
    :param old:     (x, y) of the previous point
    :param new:     (x, y) of the new point
    :param tol:     coordinates closer than this are considered equal
    :return:        (x, y) of the corner, or None when no corner is needed: free mode, an aligned segment, an exact
                    diagonal in modes 1 and 2, or a corner that would coincide with one of the points
    """
    if mode not in (1, 2, 3, 4):
        return None

    old_x, old_y = old[0], old[1]
    x, y = new[0], new[1]
    dx = x - old_x
    dy = y - old_y
    adx = abs(dx)
    ady = abs(dy)
    sx = 1.0 if dx >= 0 else -1.0
    sy = 1.0 if dy >= 0 else -1.0

    if mode == 1:
        # the straight part first, the 45 degrees part last
        if adx > ady:
            corner = (old_x + sx * (adx - ady), old_y)
        else:
            corner = (old_x, old_y + sy * (ady - adx))
    elif mode == 2:
        # the 45 degrees part first, the straight part last
        diag = min(adx, ady)
        corner = (old_x + sx * diag, old_y + sy * diag)
    elif mode == 3:
        corner = (x, old_y)
    else:
        corner = (old_x, y)

    for pt in ((old_x, old_y), (x, y)):
        if abs(corner[0] - pt[0]) <= tol and abs(corner[1] - pt[1]) <= tol:
            return None
    return corner


def next_bend_mode(mode, forward=True):
    """
    Return the mode that follows (T key, forward=True) or precedes (R key, forward=False) the given one.
    An unknown mode restarts the cycle at mode 1.
    """
    return (_NEXT_MODE if forward else _PREV_MODE).get(mode, 1)


def bend_mode_message(mode):
    """
    Return the status bar message for a corner mode.
    """
    if mode == 1:
        return _('Corner Mode 1: 45 degrees ...')
    if mode == 2:
        return _('Corner Mode 2: Reverse 45 degrees ...')
    if mode == 3:
        return _('Corner Mode 3: 90 degrees ...')
    if mode == 4:
        return _('Corner Mode 4: Reverse 90 degrees ...')
    return _('Corner Mode 5: Free angle ...')
