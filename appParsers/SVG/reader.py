"""Entry point: loads the document, walks the tree once and collects Shapely geometries."""
import builtins
import gettext
import io
import logging
import math
import os
import re

import numpy as np
from lxml import etree
from shapely.geometry import Polygon

from . import paint
from .batch import FillSlot, FillBatch
from .document import parse_length, parse_viewbox, root_setup, viewbox_matrix
from .errors import SvgImportError
from .pathdata import is_batchable, parse_path, polylines_of
from .shapes import Geo, shape_polylines
from .style import STYLE_DEFAULTS, StyleEngine, StyleSheet, fill_active, is_dark_paint, is_white_paint, is_zero, \
    opacity_value, stroke_paint_active
from .text import FontService, layout_text
from .transform import around, mean_scale, parse_css_transform, parse_transform, translation

if '_' not in builtins.__dict__:
    _ = gettext.gettext

SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'

_CONTAINERS = frozenset(('g', 'a'))
_SHAPES = frozenset(('path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'))
_RENDERABLE = _CONTAINERS | _SHAPES | {'switch', 'svg', 'use', 'text'}
_XML_DECL = re.compile(r'^\s*<\?xml[^>]*\?>')
_ORIGIN_KEYWORDS = {'left': 0.0, 'top': 0.0, 'center': 50.0, 'right': 100.0, 'bottom': 100.0}
# the diameters (mm) of a drill marker: a white circle with a dark stroke
DRILL_MIN_MM, DRILL_MAX_MM = 0.2, 6.0


def _parse_bytes(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    return etree.fromstring(data, parser)


def _from_text(text):
    return _parse_bytes(_XML_DECL.sub('', text.lstrip('﻿')).encode('utf-8'))


def load_root(source):
    """
    :param source: path, SVG text/bytes, stream, Element or ElementTree
    :return: root element
    :raises SvgImportError: unreadable or not an svg document
    """
    try:
        root = _load(source)
    except SvgImportError:
        raise
    except (etree.XMLSyntaxError, OSError, ValueError, TypeError) as exc:
        raise SvgImportError('Cannot read the SVG document: %s' % exc) from exc
    if root is None or not isinstance(root.tag, str) or root.tag.rpartition('}')[2] != 'svg':
        raise SvgImportError('The document has no <svg> root element')
    return root


def _load(source):
    if isinstance(source, (bytes, bytearray)):
        return _parse_bytes(bytes(source))
    if isinstance(source, str):
        stripped = source.lstrip('﻿ \t\r\n')
        if stripped.startswith('<') or ('<' in source and not os.path.exists(source)):
            return _from_text(source)
        return etree.parse(source, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)).getroot()
    if isinstance(source, os.PathLike):
        return etree.parse(os.fspath(source),
                           etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)).getroot()
    if hasattr(source, 'getroot'):
        return source.getroot()
    if hasattr(source, 'tag'):
        return source
    if hasattr(source, 'getvalue') or hasattr(source, 'read'):
        data = source.getvalue() if hasattr(source, 'getvalue') else source.read()
        return _load(data)
    raise SvgImportError('Unsupported SVG source: %r' % type(source))


class _Ctx:
    """Inherited state while walking the tree."""
    __slots__ = ('m', 'style', 'vp', 'frame', 'uses')

    def __init__(self, m, style, vp, frame, uses):
        self.m, self.style, self.vp, self.frame, self.uses = m, style, vp, frame, uses


def _split_tag(tag):
    if tag[0] == '{':
        ns, _, local = tag[1:].partition('}')
        return ns, local
    return '', tag


def _passes_conditions(el):
    lang = el.get('systemLanguage')
    if lang is not None:
        langs = [p.strip().lower() for p in lang.split(',') if p.strip()]
        if not any(p == 'en' or p.startswith('en-') for p in langs):
            return False
    return el.get('requiredExtensions') is None


class _Importer:
    """One import run."""

    def __init__(self, root, object_type, units, resolution, flip, include_text, app, user_units, drills=None):
        self.root = root
        # drills: None = no detection, else a list that gets (x, y, diameter) in the output units of each drill marker
        self.drills = drills
        self.warned = set()
        self.gerber = object_type == 'gerber'
        self.units = units
        # circle steps count the whole circle, the internal code works per quarter circle: `resolution` is a fixed
        # number of steps or a function radius -> steps (radius in the output units of the import)
        if callable(resolution):
            self._steps = resolution
        else:
            fixed = int(resolution)
            self._steps = lambda radius: fixed
        # the curve tolerance follows the segments of a typical feature of 5 mm
        self.resolution = self.quad(5.0 if str(units).upper() == 'MM' else 5.0 / 25.4)
        self.flip = flip
        self.include_text = include_text
        self.app = app
        self.user_units = user_units
        self.log = getattr(app, 'log', None) or logging.getLogger('base')
        self.out = []
        self.batch = FillBatch(self.gerber)
        self._flags = {}
        self._ids = None
        self.fonts = FontService(app)
        self.engine = None
        self.base_scale = 1.0
        self.tol_out = 0.01

    def quad(self, radius):
        """
        :param radius:  radius in output units
        :return:        segments per quarter circle for that radius (at least 2)
        """
        return max(math.ceil(int(self._steps(abs(radius))) / 4), 2)

    def warn(self, kind):
        """One shell warning of each kind for an import."""
        if kind in self.warned:
            return
        self.warned.add(kind)
        msg = _("The SVG has no physical size (width/height with a unit or a page size), the scale can be wrong.")
        self.log.warning('SVG import: %s' % msg)
        inform = getattr(self.app, 'inform', None)
        if inform is not None:
            inform.emit('[WARNING_NOTCL] %s' % msg)

    def run(self):
        sheet = StyleSheet(self._css_texts())
        self.engine = StyleEngine(sheet)
        m, vp, out_scale = root_setup(self.root, self.units, self.user_units, self.flip, self.warn)
        self.base_scale = out_scale
        if not self.user_units:
            tol_mm = min(max(0.005 * 16.0 / self.resolution, 0.001), 0.02)
            self.tol_out = tol_mm if str(self.units).upper() == 'MM' else tol_mm / 25.4
        style, own, frame = self.engine.resolve(self.root, 'svg', STYLE_DEFAULTS, None)
        if style['display'] == 'none' or is_zero(style['opacity']):
            return []
        local = self._local_matrix(self.root, own, vp, style['font-size'])
        if local is not None:
            m = m @ local
        stack = []
        self._push_children(stack, self.root, _Ctx(m, style, vp, frame, ()))
        while stack:
            el, ctx = stack.pop()
            try:
                self._visit(el, ctx, stack)
            except Exception as exc:
                self.log.debug('SVG import: element skipped (%s): %s', getattr(el, 'tag', '?'), exc)
        return self._collect()

    def _collect(self):
        """Build the batched fills and return the output list in document order."""
        self.batch.resolve()
        out = []
        for item in self.out:
            if type(item) is FillSlot:
                out.extend(item.geoms)
            else:
                out.append(item)
        return out

    def _style_flags(self, style, vp, fs):
        """Paint flags of a computed style, cached because most elements share their parent's style object."""
        key = (id(style), vp)
        hit = self._flags.get(key)
        if hit is None:
            width = paint.stroke_width(style, vp, fs) if self.gerber else 0.0
            rule = 'evenodd' if style['fill-rule'] == 'evenodd' else 'nonzero'
            hit = (style, fill_active(style), stroke_paint_active(style), width, rule)
            self._flags[key] = hit
        return hit

    def _css_texts(self):
        texts = []
        for tag in ('{%s}style' % SVG_NS, 'style'):
            for el in self.root.iter(tag):
                if el.get('type') in (None, '', 'text/css'):
                    texts.append(''.join(el.itertext()))
        return texts

    def ids(self):
        if self._ids is None:
            self._ids = {}
            for el in self.root.iter():
                if isinstance(el.tag, str):
                    eid = el.get('id')
                    if eid and eid not in self._ids:
                        self._ids[eid] = el
        return self._ids

    @staticmethod
    def _push_children(stack, el, ctx):
        for child in reversed(list(el)):
            stack.append((child, ctx))

    def _origin(self, text, vp, fs):
        toks = text.replace(',', ' ').split()[:2]
        if len(toks) == 1:
            toks.append('center')
        if toks[0] in ('top', 'bottom') or toks[1] in ('left', 'right'):
            toks = toks[::-1]
        vals = []
        for tok, ref in zip(toks, vp):
            if tok in _ORIGIN_KEYWORDS:
                vals.append(_ORIGIN_KEYWORDS[tok] * ref / 100.0)
            else:
                v = parse_length(tok, ref, fs)
                vals.append(0.0 if v is None else v)
        return vals[0], vals[1]

    def _local_matrix(self, el, own, vp, fs):
        css = own.get('transform')
        if css is not None:
            m = None if css == 'none' else parse_css_transform(css, lambda s: parse_length(s, vp[0], fs))
        else:
            m = parse_transform(el.get('transform'))
        if m is None:
            return None
        origin = own.get('transform-origin') or el.get('transform-origin')
        if origin:
            m = around(m, *self._origin(origin, vp, fs))
        return m

    def _visit(self, el, ctx, stack):
        tag = el.tag
        if not isinstance(tag, str):
            return
        ns, local = _split_tag(tag)
        if (ns and ns != SVG_NS) or local not in _RENDERABLE:
            return
        style, own, frame = self.engine.resolve(el, local, ctx.style, ctx.frame)
        if style['display'] == 'none' or style['opacity'] != '1' and is_zero(style['opacity']):
            return
        m = ctx.m
        local_m = self._local_matrix(el, own, ctx.vp, style['font-size'])
        if local_m is not None:
            m = m @ local_m
        if local in _SHAPES:
            self._shape(local, el, own, ctx, style, m)
        elif local in _CONTAINERS:
            self._push_children(stack, el, _Ctx(m, style, ctx.vp, frame, ctx.uses))
        elif local == 'switch':
            for child in el:
                if isinstance(child.tag, str) and _split_tag(child.tag)[1] in _RENDERABLE \
                        and _passes_conditions(child):
                    stack.append((child, _Ctx(m, style, ctx.vp, frame, ctx.uses)))
                    break
        elif local == 'svg':
            nm, nvp = self._viewport(el, own, m, ctx.vp, style['font-size'], None, None, True)
            self._push_children(stack, el, _Ctx(nm, style, nvp, frame, ctx.uses))
        elif local == 'use':
            self._use(el, own, ctx, style, frame, m, stack)
        elif local == 'text' and self.include_text:
            self._text(el, own, ctx, style, frame, m)

    def _viewport(self, el, own, m, vp, fs, w_ov, h_ov, use_xy):
        geo = Geo(el, own, vp, fs)
        x = geo.length('x', 'x') if use_xy else 0.0
        y = geo.length('y', 'y') if use_xy else 0.0
        w = w_ov if w_ov is not None else geo.length('width', 'x', vp[0])
        h = h_ov if h_ov is not None else geo.length('height', 'y', vp[1])
        m = m @ translation(x, y)
        vb = parse_viewbox(el.get('viewBox'))
        if vb and w > 0 and h > 0:
            return m @ viewbox_matrix(vb, el.get('preserveAspectRatio'), w, h), (vb[2], vb[3])
        return m, (w, h)

    def _use(self, el, own, ctx, style, frame, m, stack):
        href = el.get(XLINK_HREF) or el.get('href')
        if not href or not href.startswith('#'):
            return
        tid = href[1:]
        if tid in ctx.uses or len(ctx.uses) > 24:
            return
        target = self.ids().get(tid)
        if target is None or not isinstance(target.tag, str):
            return
        fs = style['font-size']
        geo = Geo(el, own, ctx.vp, fs)
        m2 = m @ translation(geo.length('x', 'x'), geo.length('y', 'y'))
        uses = ctx.uses + (tid,)
        tlocal = _split_tag(target.tag)[1]
        if tlocal in ('symbol', 'svg'):
            t_style, t_own, t_frame = self.engine.resolve(target, tlocal, style, frame)
            if t_style['display'] == 'none':
                return
            w = geo.length('width', 'x', None) if geo.raw('width') is not None else None
            h = geo.length('height', 'y', None) if geo.raw('height') is not None else None
            nm, nvp = self._viewport(target, t_own, m2, ctx.vp, fs, w, h, tlocal == 'svg')
            self._push_children(stack, target, _Ctx(nm, t_style, nvp, t_frame, uses))
        else:
            stack.append((target, _Ctx(m2, style, ctx.vp, frame, uses)))

    def _shape(self, local, el, own, ctx, style, m):
        if style['visibility'] in ('hidden', 'collapse'):
            return
        scale = mean_scale(m)
        if scale <= 0:
            return
        fs = style['font-size']
        flags = self._style_flags(style, ctx.vp, fs)
        tol = self.tol_out / scale
        if self.drills is not None and self._drill(local, el, own, ctx, style, m, scale, tol):
            return
        # radii inside the element are in user units, the steps are chosen for the radius in output units
        segs = lambda radius: self.quad(radius * scale)
        if local == 'path':
            d = el.get('d')
            subs = parse_path(d) if d else []
            if is_batchable(subs) and paint.batchable(self.gerber, subs[0][2], flags[1], flags[2], flags[3]):
                self.out.append(self.batch.add_path(subs[0], tol, m, flags[4]))
                return
            polylines = polylines_of(subs, tol, segs)
        else:
            polylines = shape_polylines(local, Geo(el, own, ctx.vp, fs), segs, tol)
        if not polylines:
            return
        ring = paint.single_ring(polylines, self.gerber, flags[1], flags[2], flags[3])
        if ring is not None:
            self.out.append(self.batch.add(ring, m, flags[4]))
        else:
            self.out.extend(paint.element_geometries(polylines, style, own, m, self.gerber, ctx.vp, fs,
                                                     self.quad, self.base_scale))

    def _drill(self, local, el, own, ctx, style, m, scale, tol):
        """
        Take a drill marker out of the output: a circle (a <circle> or a closed path that passes the circle test) with
        a white fill and a visible dark stroke; the circle itself (without the stroke width) is the drill.

        :return: True when the element is a drill marker (it is added to self.drills and not drawn)
        """
        if local not in ('circle', 'path') or self.user_units:
            return False
        if not (is_white_paint(style['fill'], style['color']) and opacity_value(style['fill-opacity']) >= 0.9 and
                is_dark_paint(style['stroke'], style['color']) and opacity_value(style['stroke-opacity']) >= 0.5):
            return False
        fs = style['font-size']
        if paint.stroke_width(style, ctx.vp, fs) <= 0:
            return False
        a, t = m[:2, :2], m[:2, 2]
        sv = np.linalg.svd(a, compute_uv=False)
        if sv[1] <= 0 or sv[0] / sv[1] > 1.02:
            # a skewed or non-uniformly scaled circle is an ellipse
            return False
        if local == 'circle':
            geo = Geo(el, own, ctx.vp, fs)
            r = geo.length('r', 'r')
            if r <= 0:
                return False
            x, y = a @ np.array([geo.length('cx', 'x'), geo.length('cy', 'y')]) + t
            dia = 2.0 * r * scale
        else:
            d = el.get('d')
            polylines = polylines_of(parse_path(d), tol, lambda radius: self.quad(radius * scale)) if d else []
            if len(polylines) != 1 or not polylines[0][1] or len(polylines[0][0]) < 8:
                return False
            # lazy: appJoinConvert imports camlib, which imports this package
            from appHandlers.appJoinConvert import classify_hole
            hole = classify_hole(Polygon(polylines[0][0] @ a.T + t))
            if hole is None or hole[0] != 'drill':
                return False
            x, y, dia = hole[1].x, hole[1].y, hole[2]
        mm = dia if str(self.units).upper() == 'MM' else dia * 25.4
        if not DRILL_MIN_MM <= mm <= DRILL_MAX_MM:
            return False
        self.drills.append((float(x), float(y), float(dia)))
        return True

    def _text(self, el, own, ctx, style, frame, m):
        if style['visibility'] in ('hidden', 'collapse') or mean_scale(m) <= 0:
            return

        def resolve(child, local, parent_style, parent_frame):
            return self.engine.resolve(child, local, parent_style, parent_frame)

        for geom, st in layout_text(el, style, own, resolve, ctx.vp, self.fonts, frame):
            if st['visibility'] in ('hidden', 'collapse'):
                continue
            self.out.extend(paint.area_geometries(geom, st, own, m, self.gerber, ctx.vp, st['font-size'],
                                                  self.quad, self.base_scale))


def svg_to_shapes(source, object_type='geometry', units='MM', resolution=64, flip=True,
                  include_text=True, app=None, user_units=False, drills=None):
    """
    Convert an SVG document into flat Shapely geometries.

    :param source: file path, SVG text or bytes, stream, lxml/ElementTree Element or ElementTree
    :param object_type: 'gerber' gives only Polygons; anything else gives Polygons and LineStrings
    :param units: output units 'MM' or 'IN' (ignored with user_units)
    :param resolution: circle steps: segments per full circle for every circle (an int), or a function
                       radius -> segments per full circle with the radius in output units (arcs, circles, ellipses,
                       rounded corners and strokes then get the steps of their own radius);
                       a 5 mm feature also sets the curve tolerance
    :param flip: Y points up with the origin at the bottom-left of the viewport
    :param include_text: render text elements
    :param app: optional application (ParseFont, log)
    :param user_units: output raw SVG user units (flip becomes y' = -y)
    :param drills: a list turns the drill detection on: each drill marker (a circle of 0.2 to 6 mm with a white fill and
                   a dark stroke) is not in the result, its (x, y, diameter) in the output units is added to the list
    :return: list of valid non-empty Polygons / LineStrings
    :raises SvgImportError: when the document cannot be read
    """
    root = load_root(source)
    importer = _Importer(root, object_type, units, resolution, flip, include_text, app, user_units, drills)
    return importer.run()
