"""CSS handling: style sheets, declarations, cascade, inheritance and paint helpers."""
import logging
import re

from .document import parse_length

log = logging.getLogger('base')

# properties kept in the (inherited) style dictionary
STYLE_DEFAULTS = {
    'fill': 'black', 'stroke': 'none', 'stroke-width': '1', 'stroke-linecap': 'butt',
    'stroke-linejoin': 'miter', 'stroke-miterlimit': '4', 'fill-rule': 'nonzero',
    'fill-opacity': '1', 'stroke-opacity': '1', 'opacity': '1', 'display': 'inline',
    'visibility': 'visible', 'font-family': 'sans-serif', 'font-size': 16.0,
    'font-weight': 'normal', 'font-style': 'normal', 'text-anchor': 'start', 'color': 'black',
    'white-space': 'normal', 'dominant-baseline': 'auto', 'alignment-baseline': 'auto',
}
NON_INHERITED = frozenset(('opacity', 'display', 'alignment-baseline'))
# specified on the element only
OWN_ONLY = frozenset(('vector-effect', 'paint-order', 'transform-origin', 'transform',
                      'x', 'y', 'width', 'height', 'rx', 'ry', 'cx', 'cy', 'r'))
ATTR_KEYS = frozenset(STYLE_DEFAULTS) | {'vector-effect', 'paint-order', 'transform-origin'}
CSS_KEYS = frozenset(STYLE_DEFAULTS) | OWN_ONLY
_FONT_SIZES = {'xx-small': 9.0, 'x-small': 10.0, 'small': 13.0, 'medium': 16.0, 'large': 18.0,
               'x-large': 24.0, 'xx-large': 32.0}
_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_RULE = re.compile(r'([^{}]+)\{([^{}]*)\}')
_COMPOUND = re.compile(r'^(\*|[a-zA-Z][\w-]*)?((?:[#.][\w-]+)*)$')
_PARTS = re.compile(r'([#.])([\w-]+)')
_FUNC_COLOR = re.compile(r'^(rgba?|hsla?)\((.*)\)$')
_ARG_SPLIT = re.compile(r'[\s,/]+')
_DECL_CACHE = {}


def parse_declarations(text):
    """
    :param text: declaration block ``a: b; c: d !important``
    :return: (normal dict, important dict) restricted to known properties
    """
    hit = _DECL_CACHE.get(text)
    if hit is not None:
        return hit
    normal, important = {}, {}
    for part in _COMMENT.sub('', text).split(';'):
        key, sep, value = part.partition(':')
        if not sep:
            continue
        key = key.strip().lower()
        if key not in CSS_KEYS:
            continue
        value = value.strip()
        target = normal
        if value.lower().endswith('!important'):
            value = value[:-10].strip()
            target = important
        target[key] = value if key == 'font-family' else value.lower()
    if len(_DECL_CACHE) < 4096:
        _DECL_CACHE[text] = (normal, important)
    return normal, important


class _Rule:
    """One compiled CSS rule (single selector)."""
    __slots__ = ('spec', 'order', 'compounds', 'normal', 'important')

    def __init__(self, spec, order, compounds, normal, important):
        self.spec, self.order, self.compounds = spec, order, compounds
        self.normal, self.important = normal, important

    def matches(self, tag, el_id, classes, frame):
        if not _compound_ok(self.compounds[-1], tag, el_id, classes):
            return False
        idx = len(self.compounds) - 2
        while idx >= 0:
            while frame is not None and not _compound_ok(self.compounds[idx], frame[0], frame[1], frame[2]):
                frame = frame[3]
            if frame is None:
                return False
            frame = frame[3]
            idx -= 1
        return True


def _compound_ok(comp, tag, el_id, classes):
    ctag, cid, cclasses = comp
    if ctag and ctag != tag:
        return False
    if cid and cid != el_id:
        return False
    return all(c in classes for c in cclasses)


def _parse_selector(sel):
    """
    :return: (compounds, specificity, index key) or None when unsupported
    """
    comps = []
    spec = [0, 0, 0]
    for chunk in sel.replace('>', ' ').replace('+', ' ').replace('~', ' ').split():
        m = _COMPOUND.match(chunk)
        if not m:
            return None
        tag = m.group(1) if m.group(1) not in (None, '*') else None
        el_id, classes = None, []
        for kind, name in _PARTS.findall(m.group(2)):
            if kind == '#':
                el_id = name
            else:
                classes.append(name)
        spec[0] += 1 if el_id else 0
        spec[1] += len(classes)
        spec[2] += 1 if tag else 0
        comps.append((tag, el_id, tuple(classes)))
    if not comps:
        return None
    tag, el_id, classes = comps[-1]
    key = ('id', el_id) if el_id else (('class', classes[0]) if classes else (('tag', tag) if tag else ('*', '')))
    return comps, tuple(spec), key


def strip_at_rules(css):
    """
    :param css: stylesheet text without comments
    :return: text without @media/@import/@font-face/... statements and blocks
    """
    out, pos, size = [], 0, len(css)
    while pos < size:
        at = css.find('@', pos)
        if at < 0:
            out.append(css[pos:])
            break
        out.append(css[pos:at])
        semi, brace = css.find(';', at), css.find('{', at)
        if brace < 0 or (0 <= semi < brace):
            pos = size if semi < 0 else semi + 1
            continue
        depth, pos = 0, brace
        while pos < size:
            depth += {'{': 1, '}': -1}.get(css[pos], 0)
            pos += 1
            if depth == 0:
                break
    return ''.join(out)


class StyleSheet:
    """All ``<style>`` blocks of a document compiled once."""

    def __init__(self, css_texts=()):
        self.index = {}
        self.needs_frames = False
        self.empty = True
        order = 0
        for css in css_texts:
            for selectors, body in _RULE.findall(strip_at_rules(_COMMENT.sub('', css))):
                selectors = selectors.strip()
                if '@' in selectors or not selectors:
                    continue
                normal, important = parse_declarations(body)
                if not normal and not important:
                    continue
                for sel in selectors.split(','):
                    parsed = _parse_selector(sel.strip())
                    if parsed is None:
                        continue
                    comps, spec, key = parsed
                    order += 1
                    self.needs_frames = self.needs_frames or len(comps) > 1
                    self.index.setdefault(key, []).append(_Rule(spec, order, comps, normal, important))
                    self.empty = False

    def matching(self, tag, el_id, classes, frame):
        """
        :return: matching rules sorted by specificity and order
        """
        idx = self.index
        cands = []
        if el_id:
            cands += idx.get(('id', el_id), ())
        for c in classes:
            cands += idx.get(('class', c), ())
        cands += idx.get(('tag', tag), ())
        cands += idx.get(('*', ''), ())
        hits = [r for r in cands if r.matches(tag, el_id, classes, frame)]
        hits.sort(key=lambda r: (r.spec, r.order))
        return hits


class StyleEngine:
    """Computes the specified (own) and computed styles of elements."""

    def __init__(self, sheet):
        self.sheet = sheet

    def resolve(self, el, tag, parent, parent_frame):
        """
        :param el: element
        :param tag: local tag name
        :param parent: computed style dictionary of the parent
        :param parent_frame: ancestor chain for selectors
        :return: (computed style, own specified properties, frame)
        """
        own = None
        for k, v in el.attrib.items():
            if k in ATTR_KEYS:
                if own is None:
                    own = {}
                own[k] = v.strip() if k == 'font-family' else v.strip().lower()
        sp = el.get('{http://www.w3.org/XML/1998/namespace}space')
        if sp:
            own = own or {}
            own['white-space'] = 'pre' if sp == 'preserve' else 'normal'
        frame = None
        sheet = self.sheet
        important = None
        if not sheet.empty or sheet.needs_frames:
            classes = el.get('class', '').split()
            el_id = el.get('id')
            if sheet.needs_frames:
                frame = (tag, el_id, classes, parent_frame)
            if not sheet.empty:
                for rule in sheet.matching(tag, el_id, classes, parent_frame):
                    if rule.normal:
                        own = own or {}
                        own.update(rule.normal)
                    if rule.important:
                        important = important or {}
                        important.update(rule.important)
        inline = el.get('style')
        if inline:
            normal, imp = parse_declarations(inline)
            if normal:
                own = own or {}
                own.update(normal)
            if imp:
                important = important or {}
                important.update(imp)
        if important:
            own = own or {}
            own.update(important)
        if not own:
            return parent, {}, frame
        return self._compute(own, parent), own, frame

    @staticmethod
    def _compute(own, parent):
        new = dict(parent)
        new['opacity'] = '1'
        new['display'] = 'inline'
        new['alignment-baseline'] = 'auto'
        for k, v in own.items():
            if k not in STYLE_DEFAULTS:
                continue
            if v == 'inherit':
                new[k] = parent[k]
            elif v == 'initial' or (v == 'unset' and k in NON_INHERITED):
                new[k] = STYLE_DEFAULTS[k]
            elif v == 'unset':
                new[k] = parent[k]
            elif k == 'font-size':
                new[k] = _font_size(v, parent['font-size'])
            else:
                new[k] = v
        return new


def _font_size(value, parent_size):
    if value in _FONT_SIZES:
        return _FONT_SIZES[value]
    if value == 'larger':
        return parent_size * 1.2
    if value == 'smaller':
        return parent_size / 1.2
    size = parse_length(value, ref=parent_size, font_size=parent_size)
    return size if size and size > 0 else parent_size


def is_zero(value):
    """
    :param value: opacity-like string (``0``, ``0%``)
    :return: True when numerically zero
    """
    try:
        return float(value.rstrip('%')) == 0.0
    except (ValueError, AttributeError):
        return False


def _alpha_is_zero(value):
    m = _FUNC_COLOR.match(value)
    if m:
        args = [a for a in _ARG_SPLIT.split(m.group(2).strip()) if a]
        return len(args) >= 4 and is_zero(args[3])
    if value.startswith('#'):
        if len(value) == 5:
            return value[4] == '0'
        if len(value) == 9:
            return value[7:9] == '00'
    return False


def paint_is_none(value, color='black', depth=0):
    """
    :param value: fill/stroke value
    :param color: computed ``color`` for currentColor
    :return: True when the value paints nothing (none, transparent, zero alpha)
    """
    v = value.strip().lower()
    if v in ('none', 'transparent'):
        return True
    if v == 'currentcolor':
        return depth == 0 and paint_is_none(color, 'black', 1)
    return _alpha_is_zero(v)


def fill_active(style):
    """
    :param style: computed style
    :return: True when the fill paints
    """
    return not paint_is_none(style['fill'], style['color']) and not is_zero(style['fill-opacity'])


def stroke_paint_active(style):
    """
    :param style: computed style
    :return: True when the stroke paint is set (ignoring width)
    """
    return not paint_is_none(style['stroke'], style['color']) and not is_zero(style['stroke-opacity'])


_NAMED_RGB = {'white': (255, 255, 255), 'black': (0, 0, 0), 'gray': (128, 128, 128), 'grey': (128, 128, 128),
              'darkgray': (169, 169, 169), 'darkgrey': (169, 169, 169), 'dimgray': (105, 105, 105),
              'dimgrey': (105, 105, 105), 'navy': (0, 0, 128), 'darkblue': (0, 0, 139), 'maroon': (128, 0, 0),
              'darkgreen': (0, 100, 0), 'red': (255, 0, 0), 'green': (0, 128, 0), 'blue': (0, 0, 255)}


def paint_rgb(value, color='black', depth=0):
    """
    :param value: fill/stroke value
    :param color: computed ``color`` for currentColor
    :return: (r, g, b) 0..255 of a hex, rgb() or basic named color, else None (gradients, hsl(), other names)
    """
    v = value.strip().lower()
    if v == 'currentcolor':
        return paint_rgb(color, 'black', 1) if depth == 0 else None
    if v.startswith('#'):
        h = v[1:]
        if len(h) in (3, 4):
            h = ''.join(c * 2 for c in h)
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return None
    m = _FUNC_COLOR.match(v)
    if m and m.group(1).startswith('rgb'):
        args = [a for a in _ARG_SPLIT.split(m.group(2).strip()) if a][:3]
        try:
            vals = [float(a[:-1]) * 2.55 if a.endswith('%') else float(a) for a in args]
        except ValueError:
            return None
        return tuple(min(max(int(round(x)), 0), 255) for x in vals) if len(vals) == 3 else None
    return _NAMED_RGB.get(v)


def is_white_paint(value, color='black'):
    """:return: True when the paint is white (a small tolerance for 254, 250 ...)"""
    rgb = paint_rgb(value, color)
    return rgb is not None and min(rgb) >= 245


def is_dark_paint(value, color='black'):
    """:return: True when the paint is dark (luminance up to 40 %)"""
    rgb = paint_rgb(value, color)
    return rgb is not None and 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] <= 102.0


def opacity_value(value):
    """:return: opacity-like string (``0.5``, ``50%``) as a float, 1.0 when unparsable"""
    try:
        v = value.strip()
        return float(v[:-1]) / 100.0 if v.endswith('%') else float(v)
    except (ValueError, AttributeError):
        return 1.0
