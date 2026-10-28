"""Lengths, viewBox and the root viewport."""
import logging
import re

import numpy as np

from .transform import scaling, translation

log = logging.getLogger('base')

_LEN = re.compile(r'^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)\s*$')
_NUM = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
# user units (px) per one unit
_UNIT_PX = {'': 1.0, 'px': 1.0, 'pt': 96.0 / 72.0, 'pc': 16.0, 'mm': 96.0 / 25.4, 'cm': 96.0 / 2.54,
            'in': 96.0, 'q': 96.0 / 101.6}
_ALIGN = {'min': 0.0, 'mid': 0.5, 'max': 1.0}


def parse_length(text, ref=0.0, font_size=16.0):
    """
    :param text: CSS/SVG length such as ``2mm``, ``50%``, ``1.5em``; unknown units count as user units
    :param ref: reference size for percentages
    :param font_size: size used for em/ex
    :return: length in user units (px) or None when unparsable (calc(), garbage)
    """
    if text is None:
        return None
    if not isinstance(text, str):
        return float(text)
    m = _LEN.match(text)
    if not m:
        if 'calc(' in text:
            log.debug('SVG: calc() lengths are not supported: %s', text)
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit == '%':
        return value * ref / 100.0
    if unit == 'em':
        return value * font_size
    if unit == 'ex':
        return value * font_size / 2.0
    return value * _UNIT_PX.get(unit, 1.0)


# namespace and attribute that the SVG export of FlatCAM writes on the root element
FLATCAM_NS = 'https://github.com/MariusAdrian88/FlatCAM_Evo/svg'
FLATCAM_ORIGIN = '{%s}origin' % FLATCAM_NS


def parse_viewbox(text):
    """
    :param text: viewBox attribute
    :return: (x, y, w, h) or None
    """
    if not text:
        return None
    v = [float(x) for x in _NUM.findall(text)]
    if len(v) != 4 or v[2] <= 0 or v[3] <= 0:
        return None
    return tuple(v)


def viewbox_matrix(vb, par, width, height):
    """
    :param vb: (x, y, w, h)
    :param par: preserveAspectRatio text or None
    :param width: viewport width
    :param height: viewport height
    :return: 3x3 matrix mapping viewBox coordinates to the viewport
    """
    sx, sy = width / vb[2], height / vb[3]
    parts = (par or 'xMidYMid meet').split()
    if parts and parts[0] == 'defer':
        parts = parts[1:]
    align = parts[0] if parts else 'xMidYMid'
    if align == 'none' or len(align) != 8:
        return translation(-vb[0] * sx, -vb[1] * sy) @ scaling(sx, sy)
    slice_ = len(parts) > 1 and parts[1] == 'slice'
    s = max(sx, sy) if slice_ else min(sx, sy)
    ax = _ALIGN.get(align[1:4].lower(), 0.5)
    ay = _ALIGN.get(align[5:8].lower(), 0.5)
    tx = -vb[0] * s + (width - vb[2] * s) * ax
    ty = -vb[1] * s + (height - vb[3] * s) * ay
    return translation(tx, ty) @ scaling(s, s)


def _absolute(text):
    if text is None or '%' in text:
        return None
    v = parse_length(text)
    return v if v and v > 0 else None


def root_setup(root, units, user_units, flip):
    """
    :param root: root svg element
    :param units: 'MM' or 'IN' (ignored with user_units)
    :param user_units: skip the viewport conversion
    :param flip: Y points up
    :return: (matrix, (vp_w, vp_h) in user units, out_scale px->output units)
    """
    vb = parse_viewbox(root.get('viewBox'))
    width, height = _absolute(root.get('width')), _absolute(root.get('height'))
    if vb:
        if width is None and height is None:
            width, height = vb[2], vb[3]
        elif width is None:
            width = height * vb[2] / vb[3]
        elif height is None:
            height = width * vb[3] / vb[2]
    vp = (vb[2], vb[3]) if vb else ((width or 100.0), (height or 100.0))
    if user_units:
        return (scaling(1, -1) if flip else np.eye(3)), vp, 1.0
    out = 25.4 / 96.0 if str(units).upper() == 'MM' else 1.0 / 96.0
    base = viewbox_matrix(vb, root.get('preserveAspectRatio'), width, height) if vb else np.eye(3)
    # a FlatCAM export: the viewBox coordinates are the real coordinates, the viewBox corner is not moved to 0,0
    keep_origin = bool(vb) and root.get(FLATCAM_ORIGIN) == 'viewbox'
    if keep_origin:
        base[0, 2] = base[1, 2] = 0.0
    m = scaling(out, out) @ base
    if flip:
        m = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0 if keep_origin else (height or 0.0) * out],
                      [0.0, 0.0, 1.0]]) @ m
    return m, vp, out
