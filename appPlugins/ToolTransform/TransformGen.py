"""Bounds used by the transformation reference selection."""

import numpy as np


def bounds_center(bounds):
    xmin, ymin, xmax, ymax = bounds
    return (xmax + xmin) * 0.5, (ymax + ymin) * 0.5


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
