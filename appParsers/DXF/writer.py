"""
DXF writer: Shapely geometries -> ezdxf document.

Public API: build_dxf(), layer_name().
"""
import logging
import math

import ezdxf
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


def _arc_circle(pts, tol):
    """Circle through a short point window if all points lie on it as one arc, else None."""
    n = len(pts)
    c = _circumcircle(pts[0], pts[n // 2], pts[-1])
    if c is None or not _on_circle(pts, c, tol):
        return None
    if _steps(pts, c[0], c[1]) is None:
        return None
    return c


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


def _is_arc_window(pts, i, n, closed, tol):
    """True when MIN_ARC_POINTS points starting at index i (wrapping for rings) form an arc."""
    if closed:
        win = [pts[(i + k) % n] for k in range(MIN_ARC_POINTS)]
    else:
        if i < 0 or i + MIN_ARC_POINTS > n:
            return False
        win = pts[i:i + MIN_ARC_POINTS]
    c = _arc_circle(win, tol)
    return c is not None and abs(sum(_steps(win, c[0], c[1]))) >= MIN_ARC_SWEEP


def _vertices(pts, closed, tol, fit):
    """
    Build [(x, y, bulge), ...] from a point list. For rings pts has no repeated closing point.
    The bulge belongs to the segment from this vertex to the next one.
    """
    if not fit or len(pts) < MIN_ARC_POINTS:
        return [(x, y, 0.0) for x, y in pts]
    n = len(pts)
    seq = list(pts)
    if closed:
        # start at a point that is not strictly inside an arc, so no arc is split by the ring start
        start = 0
        for s in range(n):
            if not (_is_arc_window(pts, s - 2, n, True, tol) or _is_arc_window(pts, s - 1, n, True, tol)):
                start = s
                break
        seq = pts[start:] + pts[:start] + [pts[start]]
    m = len(seq)
    out = []
    i = 0
    while i < m - 1:
        end = None
        if i + MIN_ARC_POINTS <= m and _is_arc_window(seq, i, m, False, tol):
            circle = _arc_circle(seq[i:i + MIN_ARC_POINTS], tol)
            j = i + MIN_ARC_POINTS - 1
            total = abs(sum(_steps(seq[i:j + 1], circle[0], circle[1])))
            while j + 1 < m:
                cand = seq[j + 1]
                if abs(math.hypot(cand[0] - circle[0], cand[1] - circle[1]) - circle[2]) > tol:
                    break
                st = _steps([seq[j], cand], circle[0], circle[1])
                if st is None or abs(total + abs(st[0])) > MAX_ARC_SWEEP:
                    break
                same = _steps(seq[i:j + 1] + [cand], circle[0], circle[1])
                if same is None:
                    break
                total += abs(st[0])
                j += 1
            end = j
            sweep = sum(_steps(seq[i:end + 1], circle[0], circle[1]))
        if end is None:
            out.append((seq[i][0], seq[i][1], 0.0))
            i += 1
        else:
            out.append((seq[i][0], seq[i][1], math.tan(sweep / 4.0)))
            i = end
    if not closed:
        out.append((seq[-1][0], seq[-1][1], 0.0))
    return out


# ------------------------------------------------------------------- writing

class _Writer:
    def __init__(self, doc, fit_arcs, tol, width):
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
                self._path(list(geo.exterior.coords), True, layer)
                for interior in geo.interiors:
                    self._path(list(interior.coords), True, layer)
            elif isinstance(geo, Point):
                self.msp.add_point((geo.x, geo.y), dxfattribs={'layer': layer})
            else:
                self._path(list(geo.coords), bool(geo.is_closed), layer)
            self.bounds.append(geo.bounds)

    def _path(self, coords, closed, layer):
        pts = [(c[0], c[1]) for c in coords]          # always 2D: the 3rd value would be read as a width
        if closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 2:
            return
        attribs = {'layer': layer}
        if closed and self.fit:
            circle = _fit_circle_ring(pts, self.tol)
            if circle is not None:
                self.msp.add_circle((circle[0], circle[1]), circle[2], dxfattribs=attribs)
                return
        verts = _vertices(pts, closed, self.tol, self.fit)
        if closed and not any(v[2] for v in verts):
            # no arc found: keep the plain ring as before, including its repeated closing vertex
            verts = [(x, y, 0.0) for x, y in [(c[0], c[1]) for c in coords]]
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
            writer = _Writer(doc, fit_arcs, tol, item.get('width'))
            writer.add(item.get('geometry'), name)
            all_bounds.extend(writer.bounds)

        if all_bounds:
            doc.header['$EXTMIN'] = (min(b[0] for b in all_bounds), min(b[1] for b in all_bounds), 0.0)
            doc.header['$EXTMAX'] = (max(b[2] for b in all_bounds), max(b[3] for b in all_bounds), 0.0)
    except Exception as e:
        log.error('build_dxf() --> %s' % str(e))
        return None
    return doc
