"""Bounds used by the transformation reference selection."""

import math

import numpy as np

from appCommon.SafeEval import safe_eval

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def bounds_center(bounds):
    xmin, ymin, xmax, ymax = bounds
    return (xmax + xmin) * 0.5, (ymax + ymin) * 0.5


MOVETO_REFS = ('tl', 'tr', 'bl', 'br', 'c')


def moveto_choices():
    """The choices of the Move to reference combo: the code is the value, the translated name is shown."""
    names = {'tl': _("Top Left"), 'tr': _("Top Right"), 'bl': _("Bottom Left"), 'br': _("Bottom Right"),
             'c': _("Center")}
    return [{'label': names[ref], 'value': ref} for ref in MOVETO_REFS]


def bounds_reference_point(bounds, ref):
    """The corner ('tl', 'tr', 'bl', 'br') or the center ('c') of the bounds (xmin, ymin, xmax, ymax)."""
    xmin, ymin, xmax, ymax = bounds
    if ref == 'c':
        return bounds_center(bounds)
    if ref not in MOVETO_REFS:
        raise ValueError("Unknown reference: %s" % str(ref))
    return (xmax if ref[1] == 'r' else xmin), (ymax if ref[0] == 't' else ymin)


def parse_point(text):
    """The finite (x, y) of a text like '6, -9'; None when the text is not a pair of finite numbers."""
    try:
        px, py = safe_eval('{}'.format(text))
        px, py = float(px), float(py)
    except (TypeError, ValueError, OverflowError, SyntaxError):
        return None
    return (px, py) if math.isfinite(px) and math.isfinite(py) else None


SUPPORTED_KINDS = ('gerber', 'excellon', 'geometry')
# the kinds that scale and offset also move: the G-code of a CNCJob is rewritten with the geometry
AFFINE_KINDS = SUPPORTED_KINDS + ('cncjob',)


def alt_bounds(obj_list, kinds=SUPPORTED_KINDS):
    """Return the bounds of the objects of the given kinds, skipping deleted objects, other kinds and objects
    without finite geometry."""
    def bounds_rec(lst):
        minx = np.inf
        miny = np.inf
        maxx = -np.inf
        maxy = -np.inf
        for obj in lst:
            if isinstance(obj, (list, tuple)):
                minx_, miny_, maxx_, maxy_ = bounds_rec(obj)
            elif getattr(obj, 'deleted', False):
                continue
            elif str(getattr(obj, 'kind', '')).lower() in kinds:
                try:
                    minx_, miny_, maxx_, maxy_ = obj.bounds()
                except Exception:
                    continue
            else:
                continue
            try:
                if not all(np.isfinite((minx_, miny_, maxx_, maxy_))):
                    continue
            except TypeError:
                continue
            minx = min(minx, minx_)
            miny = min(miny, miny_)
            maxx = max(maxx, maxx_)
            maxy = max(maxy, maxy_)
        return minx, miny, maxx, maxy

    return bounds_rec(obj_list)
