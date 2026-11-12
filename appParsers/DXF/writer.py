"""
DXF writer: Shapely geometries -> ezdxf document.

Public API: build_dxf(), layer_name().
"""
import logging
import math

import ezdxf
import numpy as np
from shapely import get_coordinates
from ezdxf import colors as ezcolors
from ezdxf.lldxf.const import VALID_DXF_LINEWEIGHTS
from shapely.geometry import (Point, MultiPoint, LineString, LinearRing, Polygon, MultiLineString,
                              MultiPolygon, GeometryCollection)

log = logging.getLogger('base')

_BAD_NAME_CHARS = set('<>/\\":;?*|=`\'')
MAX_ARC_STEP = math.radians(45.0)   # largest angle between two neighbour points of one arc
MIN_ARC_SWEEP = math.radians(5.0)   # shorter runs are treated as straight
MAX_ARC_SWEEP = math.radians(270.0)  # longer arcs are split (bulge = tan(sweep / 4) blows up at 360)
MIN_ARC_POINTS = 4
# A cached arc fit is used for a path only if the shape of the path differs from the cached shape by not more than
# this part of the fit tolerance.
CACHE_SHAPE_PART = 1e-3
MIN_CIRCLE_POINTS = 8


def layer_name(text):
    """Return a valid DXF layer name: forbidden and control characters removed, empty -> '0'."""
    cleaned = ''.join(c for c in str(text) if c not in _BAD_NAME_CHARS and ord(c) >= 32 and ord(c) != 127)
    cleaned = cleaned.strip()[:255]
    return cleaned or '0'


def _aci_from_rgb(rgb):
    best, best_d = 7, None
    for aci in range(1, 256):
        r, g, b = ezcolors.aci2rgb(aci)
        d = (r - rgb[0]) ** 2 + (g - rgb[1]) ** 2 + (b - rgb[2]) ** 2
        if best_d is None or d < best_d:
            best, best_d = aci, d
    return best


def _parse_color(color):
    if not color:
        return None
    text = str(color).strip().lstrip('#')
    if len(text) not in (6, 8):
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def _lineweight(mm):
    """Nearest valid DXF lineweight (1/100 mm) for a width in mm, capped at the maximum."""
    try:
        target = float(mm) * 100.0
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    return min((v for v in VALID_DXF_LINEWEIGHTS if v > 0),
               key=lambda v: abs(v - target))


# ---------------------------------------------------------------- arc fitting

def _circumcircle(p, q, r):
    ax, ay = p
    bx, by = q
    cx, cy = r
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _steps(pts, cx, cy):
    """Signed angle steps between consecutive points around (cx, cy), or None when not one monotone arc."""
    steps = []
    prev = math.atan2(pts[0][1] - cy, pts[0][0] - cx)
    for x, y in pts[1:]:
        cur = math.atan2(y - cy, x - cx)
        d = (cur - prev + math.pi) % (2 * math.pi) - math.pi
        steps.append(d)
        prev = cur
    if not steps:
        return None
    sign = 1 if steps[0] > 0 else -1
    for d in steps:
        if d * sign <= 0 or abs(d) > MAX_ARC_STEP:
            return None
    return steps


def _on_circle(pts, circle, tol):
    cx, cy, r = circle
    return all(abs(math.hypot(x - cx, y - cy) - r) <= tol for x, y in pts)


def _fit_circle_ring(pts, tol):
    """pts: unique points of a closed ring. Return (cx, cy, r) when they form one full circle."""
    n = len(pts)
    if n < MIN_CIRCLE_POINTS:
        return None
    c = _circumcircle(pts[0], pts[n // 3], pts[(2 * n) // 3])
    if c is None or not _on_circle(pts, c, tol):
        return None
    steps = _steps(pts + [pts[0]], c[0], c[1])
    if steps is None or abs(abs(sum(steps)) - 2 * math.pi) > 1e-6:
        return None
    return c


def _arc_window(pts, i, n, closed, tol):
    """
    (circle, steps) when MIN_ARC_POINTS points starting at index i (wrapping for rings) form an arc
    of at least MIN_ARC_SWEEP, else None.
    """
    if closed:
        win = [pts[(i + k) % n] for k in range(MIN_ARC_POINTS)]
    else:
        if i < 0 or i + MIN_ARC_POINTS > n:
            return None
        win = pts[i:i + MIN_ARC_POINTS]
    c = _circumcircle(win[0], win[len(win) // 2], win[-1])
    if c is None or not _on_circle(win, c, tol):
        return None
    steps = _steps(win, c[0], c[1])
    if steps is None or abs(sum(steps)) < MIN_ARC_SWEEP:
        return None
    return c, steps


def _vertices(pts, closed, tol, fit):
    """
    Build [(index, bulge), ...] from a point list: index is the position of the vertex in pts.
    For rings pts has no repeated closing point.
    The bulge belongs to the segment from this vertex to the next one.
    """
    if not fit or len(pts) < MIN_ARC_POINTS:
        return [(k, 0.0) for k in range(len(pts))]
    n = len(pts)
    seq = pts
    start = 0
    if closed:
        # start at a point that is not strictly inside an arc, so no arc is split by the ring start
        for s in range(n):
            if not (_arc_window(pts, s - 2, n, True, tol) or _arc_window(pts, s - 1, n, True, tol)):
                start = s
                break
        seq = pts[start:] + pts[:start] + [pts[start]]
    m = len(seq)
    out = []
    i = 0
    hypot = math.hypot
    while i < m - 1:
        w = _arc_window(seq, i, m, False, tol) if i + MIN_ARC_POINTS <= m else None
        if w is None:
            out.append(((i + start) % n, 0.0))
            i += 1
            continue
        (cx, cy, r), steps = w
        sign = 1 if steps[0] > 0 else -1
        j = i + MIN_ARC_POINTS - 1
        total = abs(sum(steps))
        while j + 1 < m:
            cand = seq[j + 1]
            if abs(hypot(cand[0] - cx, cand[1] - cy) - r) > tol:
                break
            st = _steps([seq[j], cand], cx, cy)
            if st is None or abs(total + abs(st[0])) > MAX_ARC_SWEEP:
                break
            if st[0] * sign <= 0:              # the new step must turn the same way as the arc
                break
            steps.append(st[0])
            total += abs(st[0])
            j += 1
        out.append(((i + start) % n, math.tan(sum(steps) / 4.0)))
        i = j
    if not closed:
        out.append((n - 1, 0.0))
    return out


# ------------------------------------------------------------------- writing

class _Writer:
    def __init__(self, doc, fit_arcs, tol, width, cache=None):
        # cache: arc fits of this document by shape, shared by all layers (one tolerance per document)
        self.cache = {} if cache is None else cache
        self.doc = doc
        self.msp = doc.modelspace()
        self.fit = fit_arcs
        self.tol = tol
        self.width = width
        self.r12 = doc.dxfversion == 'AC1009'
        self.bounds = []

    def add(self, geo, layer):
        if geo is None:
            return
        if isinstance(geo, (list, tuple)):
            for g in geo:
                self.add(g, layer)
        elif isinstance(geo, (MultiPolygon, MultiLineString, MultiPoint, GeometryCollection)):
            for g in geo.geoms:
                self.add(g, layer)
        elif isinstance(geo, (Polygon, LineString, LinearRing, Point)):
            if geo.is_empty:
                return
            if isinstance(geo, Polygon):
                self._path(get_coordinates(geo.exterior), True, layer)
                for interior in geo.interiors:
                    self._path(get_coordinates(interior), True, layer)
            elif isinstance(geo, Point):
                self.msp.add_point((geo.x, geo.y), dxfattribs={'layer': layer})
            else:
                self._path(get_coordinates(geo), bool(geo.is_closed), layer)
            self.bounds.append(geo.bounds)

    def _fit(self, pts, closed):
        """Return ('C', (cx, cy, r)) for a circle, or ('P', [(index in pts, bulge), ...]) for a polyline."""
        if closed:
            circle = _fit_circle_ring(pts, self.tol)
            if circle is not None:
                return 'C', circle
        return 'P', _vertices(pts, closed, self.tol, True)

    def _path(self, arr, closed, layer):
        # arr: (N, 2) array, always 2D: a 3rd value would be read as a width
        coords = arr.tolist()
        pts = list(map(tuple, coords))
        if closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 2:
            return
        attribs = {'layer': layer}
        kind = None
        if self.fit and len(pts) >= MIN_ARC_POINTS:
            # the same shape at another place fits the same way: key = shape relative to its first point
            shape = arr - arr[0]
            rel = shape * 1e7
            np.rint(rel, out=rel)
            rel += 0.0                                    # -0.0 -> 0.0, so equal shapes give equal bytes
            key = (closed, rel.tobytes())
            hit = self.cache.get(key)
            # The rounded key only finds a candidate: 2 different shapes can have the same key. The fit of the
            # candidate is used only if its shape is the shape of this path, to a small part of the tolerance
            # (a moved copy differs by the rounding of the move, about 1e-13). Else this path is fitted itself.
            if hit is not None and (hit[2].shape != shape.shape or
                                    float(np.abs(hit[2] - shape).max()) > self.tol * CACHE_SHAPE_PART):
                kind, fit = self._fit(pts, closed)
            elif hit is None:
                kind, fit = self._fit(pts, closed)
                x0, y0 = pts[0]
                if kind == 'C':
                    self.cache[key] = ('C', (fit[0] - x0, fit[1] - y0, fit[2]), shape)
                else:
                    self.cache[key] = ('P', fit, shape)
            elif hit[0] == 'C':
                kind, fit = 'C', (pts[0][0] + hit[1][0], pts[0][1] + hit[1][1], hit[1][2])
            else:
                kind, fit = 'P', hit[1]
        if kind == 'C':
            self.msp.add_circle((fit[0], fit[1]), fit[2], dxfattribs=attribs)
            return
        if kind is None and not closed:
            verts = [(x, y, 0.0) for x, y in pts]
        elif closed and (kind is None or not any(v[1] for v in fit)):
            # no arc found: keep the plain ring as before, including its repeated closing vertex
            verts = [(c[0], c[1], 0.0) for c in coords]
        else:
            verts = [(pts[k][0], pts[k][1], b) for k, b in fit]
        if self.r12:
            if self.width:
                attribs['default_start_width'] = self.width
                attribs['default_end_width'] = self.width
            self.msp.add_polyline2d(verts, format='xyb', close=closed, dxfattribs=attribs)
        else:
            if self.width:
                attribs['const_width'] = self.width
            self.msp.add_lwpolyline(verts, format='xyb', close=closed, dxfattribs=attribs)


def _extent(layers):
    xs, ys = [], []

    def walk(g):
        if isinstance(g, (list, tuple)):
            for x in g:
                walk(x)
        elif hasattr(g, 'bounds') and not getattr(g, 'is_empty', True):
            b = g.bounds
            xs.extend((b[0], b[2]))
            ys.extend((b[1], b[3]))
    for layer in layers:
        walk(layer.get('geometry'))
    if not xs:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys))


def build_dxf(layers, units, dxf_format, fit_arcs=True, tolerance=None):
    """
    Build an ezdxf drawing from layers of Shapely geometry.

    :param layers:      list of dicts {'name', 'geometry', optional 'color' ('#RRGGBB'/'#RRGGBBAA'),
                        'lineweight' (mm), 'width' (constant polyline width)}
    :param units:       'MM' or 'IN' (case insensitive)
    :param dxf_format:  ezdxf version name, e.g. 'R2010' or 'R12'
    :param fit_arcs:    circles become CIRCLE, arc runs (>= 4 points on one circle) become polyline bulges
    :param tolerance:   max distance of a point from the fitted circle. Default: 1e-4 * max(1, extent of
                        all geometry), in drawing units (0.0001 for small parts, relative for big ones).
    :return:            the ezdxf drawing, or None on failure (never a partial drawing)
    """
    try:
        doc = ezdxf.new(dxf_format)
        r12 = doc.dxfversion == 'AC1009'
        is_mm = str(units).lower() == 'mm'
        if r12:
            doc.header['$MEASUREMENT'] = 1 if is_mm else 0
        else:
            doc.units = ezdxf.InsertUnits(4) if is_mm else ezdxf.InsertUnits(1)
            doc.header['$MEASUREMENT'] = 1 if is_mm else 0
        tol = tolerance if tolerance is not None else 1e-4 * max(1.0, _extent(layers))

        used = set()
        all_bounds = []
        fit_cache = {}
        for item in layers:
            base = layer_name(item.get('name', '0'))
            name, k = base, 2
            while name.lower() in used:
                name = '%s_%d' % (base, k)
                k += 1
            used.add(name.lower())
            if name != '0':
                layer = doc.layers.add(name)
            else:
                layer = doc.layers.get('0')
            rgb = _parse_color(item.get('color'))
            if rgb is not None:
                if r12:
                    layer.dxf.color = _aci_from_rgb(rgb)
                else:
                    layer.dxf.true_color = ezcolors.rgb2int(rgb)
            lw = _lineweight(item.get('lineweight')) if not r12 else None
            if lw is not None:
                layer.dxf.lineweight = lw
            writer = _Writer(doc, fit_arcs, tol, item.get('width'), fit_cache)
            writer.add(item.get('geometry'), name)
            all_bounds.extend(writer.bounds)

        if all_bounds:
            doc.header['$EXTMIN'] = (min(b[0] for b in all_bounds), min(b[1] for b in all_bounds), 0.0)
            doc.header['$EXTMAX'] = (max(b[2] for b in all_bounds), max(b[3] for b in all_bounds), 0.0)
    except Exception as e:
        log.error('build_dxf() --> %s' % str(e))
        return None
    return doc
