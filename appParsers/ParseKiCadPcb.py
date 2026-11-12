# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native KiCad PCB (.kicad_pcb) geometry reader            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read saved .kicad_pcb manufacturing geometry without KiCad.

Written from the public file-format description, not from KiCad source.
Output is in millimetres with Y negated, the frame of KiCad's own Gerber
plots (Gerber Y = -KiCad Y). Saved zone fills are the authority for pours;
nothing is refilled. Anything that would change copper and cannot be
represented raises KiCadPcbParseError; skipped non-copper items are warned.
"""

import math
import re

from shapely import LineString, MultiLineString, Point, Polygon, box, set_precision
from shapely.affinity import rotate, scale, translate
from shapely.ops import polygonize, unary_union

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.ParsePcbDoc import PcbDocParser
from appParsers.board_primitives import BoardPrimitives


class KiCadPcbParseError(ValueError):
    """Malformed data or copper geometry that cannot be represented safely."""


_TOKEN = re.compile(r'\(|\)|"((?:\\.|[^"\\])*)"|([^\s()"]+)', re.S)
_ESCAPE = re.compile(r'\\(.)', re.S)
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r'}


def _unescape(value):
    return _ESCAPE.sub(lambda match: _ESCAPES.get(match.group(1), match.group(1)), value)


def _sexpr(text):
    """Parse S-expression text into nested lists of str; quoted and bare atoms are both str."""
    root = []
    stack = [root]
    position = 0
    length = len(text)
    while True:
        # Skip whitespace; anything the token pattern cannot match is malformed (stray quote).
        while position < length and text[position].isspace():
            position += 1
        if position >= length:
            break
        match = _TOKEN.match(text, position)
        if not match:
            raise KiCadPcbParseError('Malformed S-expression near byte %d' % position)
        position = match.end()
        token = match.group(0)
        if token == '(':
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            if len(stack) == 1:
                raise KiCadPcbParseError('Unbalanced ")" near byte %d' % position)
            stack.pop()
        elif match.group(1) is not None:
            stack[-1].append(_unescape(match.group(1)))
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise KiCadPcbParseError('Unbalanced "(": file is truncated')
    return root


class KiCadPcbParser:
    MIN_VERSION = 20211014          # KiCad 6 file format

    COPPER_INNER = re.compile(r'^In(\d+)\.Cu$')
    TECH_LAYERS = frozenset(
        ['F.Adhes', 'B.Adhes', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'F.Mask', 'B.Mask',
         'Dwgs.User', 'Cmts.User', 'Eco1.User', 'Eco2.User', 'Edge.Cuts', 'Margin',
         'F.CrtYd', 'B.CrtYd', 'F.Fab', 'B.Fab'] + ['User.%d' % i for i in range(1, 10)])
    DEFAULT_TECH = ('F.Mask', 'B.Mask', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'Edge.Cuts')

    # Top-level / footprint members that carry no manufacturing geometry.
    IGNORED_TOP = frozenset(
        ['version', 'generator', 'generator_version', 'general', 'paper', 'title_block', 'layers',
         'setup', 'net', 'nets', 'property', 'generated', 'embedded_fonts', 'embedded_files',
         'group', 'uuid', 'tstamp'])
    IGNORED_FOOTPRINT = frozenset(
        ['layer', 'uuid', 'tstamp', 'at', 'descr', 'tags', 'path', 'sheetname', 'sheetfile',
         'sheet_name', 'sheet_file', 'units', 'attr', 'duplicate_pad_numbers_are_jumpers', 'locked',
         'placed', 'tedit', 'version', 'generator', 'generator_version', 'model', 'embedded_fonts',
         'embedded_files', 'private_layers', 'net_tie_pad_groups', 'solder_mask_margin',
         'solder_paste_margin', 'solder_paste_margin_ratio', 'clearance', 'zone_connect',
         'thermal_width', 'thermal_gap', 'autoplace_cost90', 'autoplace_cost180', 'component_classes',
         'teardrops', 'group', 'unit', 'property', 'fp_text', 'pad'])

    # Lowercase and extra glyphs for the approximate stroke font (own design, 6-cell grid:
    # baseline 0, x-height 4, cap/ascender 6, descender -2). Uppercase and digits are reused
    # from the Altium reader's original reconstruction.
    _LOWER_GLYPHS = {
        'a': (((0, 3), (1, 4), (3, 4), (4, 3), (4, 0)), ((4, 2), (1, 2), (0, 1), (1, 0), (3, 0), (4, 1))),
        'b': (((0, 6), (0, 0)), ((0, 3), (1, 4), (3, 4), (4, 3), (4, 1), (3, 0), (1, 0), (0, 1))),
        'c': (((4, 3), (3, 4), (1, 4), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1)),),
        'd': (((4, 6), (4, 0)), ((4, 3), (3, 4), (1, 4), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1))),
        'e': (((0, 2), (4, 2), (4, 3), (3, 4), (1, 4), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1)),),
        'f': (((3, 6), (2, 6), (1, 5), (1, 0)), ((0, 4), (3, 4))),
        'g': (((4, 4), (4, -1), (3, -2), (1, -2), (0, -1)),
              ((4, 3), (3, 4), (1, 4), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1))),
        'h': (((0, 6), (0, 0)), ((0, 3), (1, 4), (3, 4), (4, 3), (4, 0))),
        'i': (((1, 4), (2, 4), (2, 0)), ((2, 5), (2, 6))),
        'j': (((3, 4), (3, -1), (2, -2), (1, -2), (0, -1)), ((3, 5), (3, 6))),
        'k': (((0, 6), (0, 0)), ((4, 4), (0, 1.5)), ((1.5, 2.5), (4, 0))),
        'l': (((1, 6), (2, 6), (2, 1), (3, 0)),),
        'm': (((0, 4), (0, 0)), ((0, 3), (1, 4), (2, 3), (2, 0)), ((2, 3), (3, 4), (4, 3), (4, 0))),
        'n': (((0, 4), (0, 0)), ((0, 3), (1, 4), (3, 4), (4, 3), (4, 0))),
        'o': (((1, 0), (0, 1), (0, 3), (1, 4), (3, 4), (4, 3), (4, 1), (3, 0), (1, 0)),),
        'p': (((0, 4), (0, -2)), ((0, 3), (1, 4), (3, 4), (4, 3), (4, 1), (3, 0), (1, 0), (0, 1))),
        'q': (((4, 4), (4, -2)), ((4, 3), (3, 4), (1, 4), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1))),
        'r': (((0, 4), (0, 0)), ((0, 2), (1, 4), (3, 4), (4, 3))),
        's': (((4, 3), (3, 4), (1, 4), (0, 3), (1, 2), (3, 2), (4, 1), (3, 0), (1, 0), (0, 1)),),
        't': (((1, 6), (1, 1), (2, 0), (3, 0)), ((0, 4), (3, 4))),
        'u': (((0, 4), (0, 1), (1, 0), (3, 0), (4, 1)), ((4, 4), (4, 0))),
        'v': (((0, 4), (2, 0), (4, 4)),),
        'w': (((0, 4), (1, 0), (2, 3), (3, 0), (4, 4)),),
        'x': (((0, 4), (4, 0)), ((0, 0), (4, 4))),
        'y': (((0, 4), (2, 0)), ((4, 4), (1, -2))),
        'z': (((0, 4), (4, 4), (0, 0), (4, 0)),),
        '!': (((2, 6), (2, 2)), ((2, 0), (2, 1))),
        '?': (((0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (2, 3), (2, 2)), ((2, 0), (2, 1))),
        '%': (((0, 0), (4, 6)), ((0, 5), (0, 6), (1, 6), (1, 5), (0, 5)),
              ((3, 0), (3, 1), (4, 1), (4, 0), (3, 0))),
        '#': (((1, 0), (1, 6)), ((3, 0), (3, 6)), ((0, 2), (4, 2)), ((0, 4), (4, 4))),
        '<': (((4, 6), (0, 3), (4, 0)),),
        '>': (((0, 6), (4, 3), (0, 0)),),
        "'": (((2, 6), (2, 4)),),
        '"': (((1, 6), (1, 4)), ((3, 6), (3, 4))),
        '\u00b0': (((1, 6), (0, 5), (1, 4), (2, 5), (1, 6)),),
        '|': (((2, -1), (2, 6)),),
        ';': (((1, 3), (1, 4)), ((1, 1), (1, 0), (0, -1))),
        '.': PcbDocParser.STROKE_GLYPHS['.'],
        '&': (((4, 0), (1, 4), (1, 5), (2, 6), (3, 5), (3, 4), (0, 1), (1, 0), (2, 0), (4, 2)),),
        '@': (((4, 1), (3, 0), (1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 2), (3, 2),
               (3, 4), (1, 4), (1, 2), (3, 2)),),
        '$': (((4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1), (3, 0),
               (1, 0), (0, 1)), ((2, 7), (2, -1))),
        '^': (((0, 4), (2, 6), (4, 4)),),
        '~': (((0, 2), (1, 3), (3, 2), (4, 3)),),
        '`': (((1, 6), (2, 5)),),
        '{': (((3, 6), (2, 6), (1, 5), (1, 3), (0, 3), (1, 3), (1, 1), (2, 0), (3, 0)),),
        '}': (((1, 6), (2, 6), (3, 5), (3, 3), (4, 3), (3, 3), (3, 1), (2, 0), (1, 0)),),
        '\u00b5': (((0, 4), (0, -2)), ((0, 1), (1, 0), (3, 0), (4, 1)), ((4, 4), (4, 0))),
        '\u03a9': (((0, 0), (1, 0), (1, 2), (0, 3), (0, 5), (1, 6), (3, 6), (4, 5), (4, 3), (3, 2),
                    (3, 0), (4, 0)),),
    }
    # Fraction of the KiCad text height that a capital letter occupies (calibrated on the fixture).
    CAP_RATIO = 0.7
    ADVANCE_PAD = 2                 # cells of side bearing added to the glyph width
    SPACE_CELLS = 4
    LINE_SPACING = 1.5

    def __init__(self, app=None, circle_steps=None):
        # circle_steps: a fixed number of segments for every circle; None: the Gerber "Circle Steps" preference
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        self._glyphs = dict(PcbDocParser.STROKE_GLYPHS)
        self._glyphs.update(self._LOWER_GLYPHS)
        self._reset()

    # ------------------------------------------------------------------ state

    def _reset(self):
        self._primitives = BoardPrimitives(self._options)
        self.layers = {}
        self.drill_groups = {}
        self.warnings = []
        self.board_outline = Polygon()
        self.default_layers = []
        self.version = 0
        self.copper_layers = []
        self._layer_names = {}
        self._layer_order = []
        self._solids = {}
        self._follows = {}
        self._tenting = {'front': False, 'back': False}
        self._mask_margin = 0.0
        self._paste_margin = 0.0
        self._paste_ratio = 0.0
        self._warned = set()
        self._unsupported_chars = set()
        self._conn = {}
        self._pending = []
        self._text_used = False
        self._footprint_count = 0

    def _warn(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(message)

    # ------------------------------------------------------------------ entry points

    def parse_file(self, filename):
        try:
            with open(str(filename), 'r', encoding='utf-8') as handle:
                text = handle.read()
        except (OSError, UnicodeError) as error:
            self._reset()
            raise KiCadPcbParseError('Cannot read KiCad PCB file: %s' % error) from error
        return self.parse_text(text)

    def parse_text(self, text):
        self._reset()
        try:
            self._parse(_sexpr(text))
        except KiCadPcbParseError:
            self.layers = {}
            self.drill_groups = {}
            raise
        except Exception as error:
            self.layers = {}
            self.drill_groups = {}
            raise KiCadPcbParseError('Cannot read KiCad PCB: %s' % error) from error
        return self

    # ------------------------------------------------------------------ node helpers

    @staticmethod
    def _child(node, key):
        for item in node[1:]:
            if isinstance(item, list) and item and item[0] == key:
                return item
        return None

    @staticmethod
    def _children(node, key):
        return [item for item in node[1:] if isinstance(item, list) and item and item[0] == key]

    @staticmethod
    def _num(value):
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise KiCadPcbParseError('Invalid number: %r' % (value,)) from error
        if not math.isfinite(number):
            raise KiCadPcbParseError('Non-finite number: %r' % (value,))
        return number

    def _nums(self, node, count, what):
        if node is None or len(node) < count + 1:
            raise KiCadPcbParseError('Missing or short "%s" in %s' % (what, node[0] if node else '?'))
        return [self._num(value) for value in node[1:count + 1]]

    def _value(self, node, key, default=None):
        child = self._child(node, key)
        if child is None or len(child) < 2:
            return default
        return self._num(child[1])

    def _flag(self, node, key, default=False):
        child = self._child(node, key)
        if child is None:
            return default
        return len(child) == 1 or str(child[1]).lower() not in ('no', 'false', '0')

    def _pt(self, x, y):
        """File coordinates (Y down) to the output frame (Y up)."""
        return x, -y

    def _xy_node(self, node, key):
        x, y = self._nums(self._child(node, key), 2, key)
        return self._pt(x, y)

    # ------------------------------------------------------------------ geometry helpers

    def _steps(self, radius):
        # Segments of a whole circle of this radius (millimetres): the fixed override or the Gerber preference.
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _segs(self, radius):
        return circle_quad_segs(self._steps(radius))

    def _stroke(self, points, width):
        """Round-capped stroke along a polyline."""
        if width <= 0:
            return Polygon()
        points = list(points)
        if all(point == points[0] for point in points):
            return Point(points[0]).buffer(width / 2, quad_segs=self._segs(width / 2))
        return LineString(points).buffer(width / 2, quad_segs=self._segs(width / 2))

    def _arc_points(self, start, mid, end):
        """Points along the circular arc start -> mid -> end (exact ends)."""
        (x1, y1), (x2, y2), (x3, y3) = start, mid, end
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-12:
            return [start, mid, end]
        s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
        cx = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
        cy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
        radius = math.hypot(x1 - cx, y1 - cy)
        a1, a2, a3 = (math.atan2(y - cy, x - cx) for x, y in (start, mid, end))
        full = 2 * math.pi
        if (a2 - a1) % full < (a3 - a1) % full:
            sweep = (a3 - a1) % full
        else:
            sweep = -((a1 - a3) % full)
        count = max(2, math.ceil(self._steps(radius) * abs(sweep) / full))
        points = [start]
        for i in range(1, count):
            angle = a1 + sweep * i / count
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        points.append(end)
        return points

    def _bezier_points(self, p0, p1, p2, p3):
        # the curve is sized by its control polygon
        size = max(math.hypot(q[0] - p0[0], q[1] - p0[1]) for q in (p1, p2, p3))
        count = max(8, self._steps(size / 2) // 4)
        points = []
        for i in range(count + 1):
            t = i / count
            u = 1 - t
            points.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                           u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
        return points

    def _pts_list(self, node):
        """Vertices of a (pts ...) node; (arc ...) members are sampled."""
        if node is None:
            raise KiCadPcbParseError('Missing (pts ...) list')
        points = []
        for item in node[1:]:
            if not isinstance(item, list) or not item:
                raise KiCadPcbParseError('Malformed (pts ...) list')
            if item[0] == 'xy':
                points.append(self._pt(*self._nums(item, 2, 'xy')))
            elif item[0] == 'arc':
                arc = [self._xy_node(item, key) for key in ('start', 'mid', 'end')]
                sampled = self._arc_points(*arc)
                points.extend(sampled if not points or points[-1] != sampled[0] else sampled[1:])
            else:
                raise KiCadPcbParseError('Unsupported (pts ...) member: %s' % item[0])
        return points

    @staticmethod
    def _polygon(points):
        if len(points) < 3:
            raise KiCadPcbParseError('Polygon has fewer than three vertices')
        try:
            geo = Polygon(points)
        except (ValueError, TypeError) as error:
            raise KiCadPcbParseError('Invalid polygon vertices') from error
        return geo if geo.is_valid else geo.buffer(0)

    @staticmethod
    def _polygons_of(geo):
        if geo.is_empty:
            return []
        if isinstance(geo, Polygon):
            return [geo]
        if geo.geom_type in ('MultiPolygon', 'GeometryCollection'):
            return [polygon for child in geo.geoms for polygon in KiCadPcbParser._polygons_of(child)]
        if geo.geom_type in ('LineString', 'MultiLineString', 'Point', 'MultiPoint', 'LinearRing'):
            return []
        raise KiCadPcbParseError('Non-polygon material geometry: %s' % geo.geom_type)

    def _place(self, geo, at):
        """Move geometry from footprint-local (Y up) to board coordinates; at = (x, y, rot) in file units."""
        if at is None:
            return geo
        x, y, angle = at
        if angle:
            geo = rotate(geo, angle, origin=(0, 0))
        return translate(geo, x, -y)

    def _place_point(self, point, at):
        if at is None:
            return point
        x, y, angle = at
        rad = math.radians(angle)
        c, s = math.cos(rad), math.sin(rad)
        return (x + point[0] * c - point[1] * s, -y + point[0] * s + point[1] * c)

    # ------------------------------------------------------------------ layers

    def _read_layers(self, node):
        ordered = []
        for item in node[1:]:
            if not isinstance(item, list) or len(item) < 3:
                raise KiCadPcbParseError('Malformed (layers ...) entry')
            canonical = item[1]
            user = item[4] if len(item) > 4 else canonical
            self._layer_names[canonical] = user or canonical
            ordered.append(canonical)
        inner = []
        for name in ordered:
            match = self.COPPER_INNER.match(name)
            if match:
                inner.append((int(match.group(1)), name))
        self.copper_layers = [name for name in ('F.Cu',) if name in ordered]
        self.copper_layers += [name for _, name in sorted(inner)]
        self.copper_layers += [name for name in ('B.Cu',) if name in ordered]
        if len(self.copper_layers) < 1:
            raise KiCadPcbParseError('Board has no copper layers')
        self._layer_order = ordered

    def _read_setup(self, node):
        self._mask_margin = self._value(node, 'pad_to_mask_clearance', 0.0)
        self._paste_margin = self._value(node, 'pad_to_paste_clearance', 0.0)
        self._paste_ratio = self._value(node, 'pad_to_paste_clearance_ratio', 0.0)
        self._tenting = self._sides(self._child(node, 'tenting'), {'front': False, 'back': False})

    @staticmethod
    def _sides(node, default):
        """Parse (tenting (front yes) (back no)) or the old (tenting front back) form."""
        if node is None:
            return dict(default)
        result = {'front': False, 'back': False}
        for item in node[1:]:
            if isinstance(item, list) and item:
                if item[0] in result:
                    result[item[0]] = len(item) == 1 or str(item[1]).lower() not in ('no', 'false', '0')
            elif item in result:
                result[item] = True
        return result

    def _check_layer(self, layer):
        if layer.endswith('.Cu') and layer not in self.copper_layers:
            raise KiCadPcbParseError('Item uses copper layer %s that is not in the board stackup' % layer)

    def _add(self, layer, geo, follow=None):
        if not layer:
            raise KiCadPcbParseError('Item without a layer')
        self._check_layer(layer)
        if layer not in self.copper_layers and layer not in self.TECH_LAYERS and layer not in self._layer_names:
            self._warn('layer:' + layer, 'Items on unknown layer %s were skipped' % layer)
            return
        if not geo.is_valid:
            geo = geo.buffer(0)
            if not geo.is_valid:
                raise KiCadPcbParseError('Invalid geometry on layer %s' % layer)
        polygons = self._polygons_of(geo)
        if polygons:
            self._solids.setdefault(layer, []).extend(polygons)
        if follow:
            self._follows.setdefault(layer, []).extend(follow)

    def _expand_layers(self, names):
        out = []
        for name in names:
            if name.startswith('*.'):
                suffix = name[2:]
                candidates = list(self.copper_layers) if suffix == 'Cu' else ['F.' + suffix, 'B.' + suffix]
            elif name.startswith('F&B.'):
                suffix = name[4:]
                candidates = ['F.' + suffix, 'B.' + suffix]
            else:
                candidates = [name]
            for candidate in candidates:
                if candidate not in out:
                    out.append(candidate)
        return out

    # ------------------------------------------------------------------ main parse

    def _parse(self, root):
        if len(root) != 1 or not isinstance(root[0], list) or not root[0] or root[0][0] != 'kicad_pcb':
            raise KiCadPcbParseError('Not a KiCad PCB file: expected a (kicad_pcb ...) expression')
        board = root[0]
        version = self._child(board, 'version')
        if version is None or len(version) < 2:
            raise KiCadPcbParseError('KiCad PCB file has no (version ...); it is older than KiCad 6')
        self.version = int(self._num(version[1]))
        if self.version < self.MIN_VERSION:
            raise KiCadPcbParseError('KiCad PCB file format %d is older than KiCad 6 (%d); '
                                     'open and save it in KiCad 6 or newer first' %
                                     (self.version, self.MIN_VERSION))
        layers = self._child(board, 'layers')
        if layers is None:
            raise KiCadPcbParseError('KiCad PCB file has no (layers ...) table')
        self._read_layers(layers)
        setup = self._child(board, 'setup')
        if setup is not None:
            self._read_setup(setup)
        handlers = {'segment': self._segment, 'arc': self._track_arc, 'via': self._via,
                    'zone': self._zone, 'footprint': self._footprint, 'module': self._footprint,
                    'dimension': self._dimension, 'gr_text': self._gr_text,
                    'gr_text_box': self._text_box}
        for item in board[1:]:
            if not isinstance(item, list) or not item:
                continue
            key = item[0]
            if key in handlers:
                handlers[key](item)
            elif key in ('gr_line', 'gr_arc', 'gr_circle', 'gr_rect', 'gr_poly', 'gr_curve', 'gr_bezier'):
                self._graphic(item, None)
            elif key in ('image', 'target', 'table'):
                self._warn('skip:' + key, 'Board item "%s" is not imported' % key)
            elif key not in self.IGNORED_TOP:
                self._warn('top:' + key, 'Unsupported board item "%s" was skipped' % key)
        self._resolve_pending()
        self._finish()

    # ------------------------------------------------------------------ tracks and vias

    @staticmethod
    def _net(node):
        child = KiCadPcbParser._child(node, 'net')
        if child is None or len(child) < 2:
            return None
        return None if child[1] in ('', '0') else child[1]

    def _remember(self, layer, net, geo):
        if net is not None:
            self._conn.setdefault(layer, []).append((net, geo))

    def _segment(self, node):
        start = self._xy_node(node, 'start')
        end = self._xy_node(node, 'end')
        self._track(node, [start, end])

    def _track_arc(self, node):
        arc = [self._xy_node(node, key) for key in ('start', 'mid', 'end')]
        self._track(node, self._arc_points(*arc))

    def _track(self, node, points):
        layer = self._child(node, 'layer')
        if layer is None or len(layer) < 2:
            raise KiCadPcbParseError('Track without a layer')
        width = self._value(node, 'width')
        if width is None or width <= 0:
            raise KiCadPcbParseError('Track with missing or non-positive width')
        follow = LineString(points) if len(set(points)) > 1 else None
        geo = self._stroke(points, width)
        self._add(layer[1], geo, [follow] if follow is not None else None)
        self._primitives.stroke(layer[1], geo, follow if follow is not None else Point(points[0]), width)
        self._remember(layer[1], self._net(node), geo)

    def _via(self, node):
        kind = node[1] if len(node) > 1 and not isinstance(node[1], list) else 'through'
        if kind not in ('through', 'blind', 'micro'):
            raise KiCadPcbParseError('Unsupported via type: %s' % kind)
        x, y = self._xy_node(node, 'at')
        size = self._value(node, 'size')
        drill = self._value(node, 'drill')
        names = self._child(node, 'layers')
        if size is None or size <= 0:
            raise KiCadPcbParseError('Via with missing or non-positive size')
        if drill is None or drill <= 0 or drill > size:
            raise KiCadPcbParseError('Via with invalid drill')
        if kind == 'through':
            span = list(self.copper_layers)
        else:
            if names is None or len(names) != 3:
                raise KiCadPcbParseError('%s via without two layers' % kind)
            span = self._span(names[1], names[2])
        outer_front, outer_back = self.copper_layers[0], self.copper_layers[-1]
        pad = Point(x, y).buffer(size / 2, quad_segs=self._segs(size / 2))
        net = self._net(node)
        prune = self._flag(node, 'remove_unused_layers')
        keep_end = self._flag(node, 'keep_end_layers')
        for layer in span:
            if prune and len(self.copper_layers) > 2 and not (keep_end and layer in (span[0], span[-1])):
                self._pending.append((layer, net, pad, ((x, y), 'circle', size, size, 0.0)))
            else:
                self._add(layer, pad)
                self._primitives.flash(layer, pad, (x, y), 'circle', size, size)
                self._remember(layer, net, pad)
        self._drill((x, y), drill, True, (span[0], span[-1]))
        tenting = self._sides(self._child(node, 'tenting'), self._tenting)
        for side, layer, mask in (('front', outer_front, 'F.Mask'), ('back', outer_back, 'B.Mask')):
            if layer in span and not tenting[side]:
                opening = Point(x, y).buffer(size / 2 + self._mask_margin,
                                             quad_segs=self._segs(size / 2 + self._mask_margin))
                self._add(mask, opening)
                self._primitives.flash(mask, opening, (x, y), 'circle',
                                       size + 2 * self._mask_margin, size + 2 * self._mask_margin)

    def _span(self, first, last):
        try:
            start = self.copper_layers.index(first)
            end = self.copper_layers.index(last)
        except ValueError as error:
            raise KiCadPcbParseError('Via/drill span uses a layer outside the copper stackup') from error
        if end < start:
            start, end = end, start
        return self.copper_layers[start:end + 1]

    def _drill(self, position, diameter, plated, span, slot=0.0, angle=0.0):
        """Record a hole. span is (first, last) copper layer names; slot is the overall slot length."""
        if diameter <= 0:
            raise KiCadPcbParseError('Non-positive hole diameter')
        group = '%s_%s-%s' % ('PTH' if plated else 'NPTH', span[0], span[-1])
        tools = self.drill_groups.setdefault(group, {})
        tool_id = next((key for key, tool in tools.items() if abs(tool['tooldia'] - diameter) < 1e-9),
                       len(tools) + 1)
        tool = tools.setdefault(tool_id, {'tooldia': diameter, 'drills': [], 'slots': []})
        if slot > diameter + 1e-9:
            half = (slot - diameter) / 2
            dx = half * math.cos(math.radians(angle))
            dy = half * math.sin(math.radians(angle))
            first = Point(position[0] - dx, position[1] - dy)
            last = Point(position[0] + dx, position[1] + dy)
            tool['slots'].append((first, last))
            return LineString([first, last]).buffer(diameter / 2, quad_segs=self._segs(diameter / 2))
        tool['drills'].append(Point(position))
        return Point(position).buffer(diameter / 2, quad_segs=self._segs(diameter / 2))

    def _resolve_pending(self):
        """Annular rings marked remove_unused_layers: keep those that touch same-net copper on that layer."""
        for layer, net, shape, primitive in self._pending:
            if net is None:
                continue
            if any(other == net and geo.intersects(shape) for other, geo in self._conn.get(layer, [])):
                self._add(layer, shape)
                self._primitives.flash(layer, shape, *primitive)
        if self._pending:
            self._warn('prune', 'Pads/vias with "remove unused layers": unconnected inner rings were dropped '
                                'using a same-net touch test (approximation of the KiCad connectivity)')

    # ------------------------------------------------------------------ zones

    def _zone(self, node, at=None):
        fills = self._children(node, 'filled_polygon')
        if self._child(node, 'keepout') is not None or self._child(node, 'rule_area') is not None:
            return  # Rule area: no copper
        if self._child(node, 'fill_segments') is not None:
            raise KiCadPcbParseError('Zone with fill_segments (hatch segments) is not supported')
        net = self._net(node)
        if net is None:
            name = self._child(node, 'net_name')
            net = name[1] if name and len(name) > 1 and name[1] else None
        if not fills:
            fill = self._child(node, 'fill')
            if fill is not None and (len(fill) == 1 or fill[1] == 'yes'):
                self._warn('zone-nofill', 'Zone on net "%s" is marked filled but has no saved fill; '
                                          'it was not refilled' % (net or ''))
            return
        if at is not None:
            raise KiCadPcbParseError('Filled zone inside a footprint is not supported')
        for fill in fills:
            layer = self._child(fill, 'layer')
            if layer is None or len(layer) < 2:
                raise KiCadPcbParseError('filled_polygon without a layer')
            geo = self._polygon(self._pts_list(self._child(fill, 'pts')))
            self._add(layer[1], geo)
            self._remember(layer[1], net, geo)

    # ------------------------------------------------------------------ graphics

    def _width_of(self, node):
        stroke = self._child(node, 'stroke')
        if stroke is not None:
            kind = self._child(stroke, 'type')
            if kind is not None and len(kind) > 1 and kind[1] not in ('solid', 'default'):
                self._warn('dash', 'Dashed/dotted lines are drawn solid')
            width = self._value(stroke, 'width', 0.0)
        else:
            width = self._value(node, 'width', 0.0)
        if width < 0:
            raise KiCadPcbParseError('Negative graphic line width')
        return width

    def _fill_of(self, node):
        fill = self._child(node, 'fill')
        if fill is None or len(fill) < 2:
            return False
        value = fill[1]
        if isinstance(value, list):
            value = value[1] if len(value) > 1 else 'none'
        if value in ('yes', 'solid', 'true'):
            return True
        if value in ('no', 'none', 'false'):
            return False
        self._warn('fill:' + str(value), 'Fill type "%s" is drawn as outline only' % value)
        return False

    def _shape(self, node):
        """Geometry of one gr_/fp_ drawing in local (Y up) coordinates -> (solid, follow lines)."""
        kind = node[0].split('_', 1)[1]
        width = self._width_of(node)
        filled = self._fill_of(node)
        follows = []
        if kind == 'line':
            points = [self._xy_node(node, 'start'), self._xy_node(node, 'end')]
            solid = self._stroke(points, width)
            follows = [LineString(points)] if points[0] != points[1] else []
            return solid, follows
        if kind == 'arc':
            points = self._arc_points(*[self._xy_node(node, key) for key in ('start', 'mid', 'end')])
            return self._stroke(points, width), [LineString(points)]
        if kind in ('curve', 'bezier'):
            controls = self._pts_list(self._child(node, 'pts'))
            if len(controls) != 4:
                raise KiCadPcbParseError('Bezier curve needs four control points')
            points = self._bezier_points(*controls)
            return self._stroke(points, width), [LineString(points)]
        if kind == 'circle':
            center = self._xy_node(node, 'center')
            edge = self._xy_node(node, 'end')
            radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
            if radius <= 0:
                self._warn('zero-circle', 'Zero-radius circles were skipped')
                return Polygon(), []
            disc = Point(center).buffer(radius, quad_segs=self._segs(radius))
            ring = Point(center).buffer(radius, quad_segs=self._segs(radius)).exterior
            follows = [LineString(ring.coords)]
            if filled:
                return (disc.buffer(width / 2, quad_segs=self._segs(width / 2)) if width > 0 else disc), follows
            if width <= 0:
                return Polygon(), follows
            outer = Point(center).buffer(radius + width / 2, quad_segs=self._segs(radius + width / 2))
            inner = Point(center).buffer(max(radius - width / 2, 0), quad_segs=self._segs(max(radius - width / 2, 0)))
            return outer.difference(inner), follows
        if kind == 'rect':
            a = self._xy_node(node, 'start')
            b = self._xy_node(node, 'end')
            points = [a, (b[0], a[1]), b, (a[0], b[1])]
        elif kind == 'poly':
            points = self._pts_list(self._child(node, 'pts'))
        else:
            raise KiCadPcbParseError('Unsupported drawing item: %s' % node[0])
        if len(points) < 2:
            raise KiCadPcbParseError('Drawing %s has too few points' % node[0])
        closed = points + [points[0]]
        follows = [LineString(closed)]
        parts = []
        if filled and len(points) >= 3:
            parts.append(self._polygon(points))
        if width > 0:
            parts.append(self._stroke(closed, width))
        return (unary_union(parts) if parts else Polygon()), follows

    def _graphic(self, node, at, fields=None):
        layer = self._child(node, 'layer')
        if layer is None or len(layer) < 2:
            raise KiCadPcbParseError('Drawing %s without a layer' % node[0])
        solid, follows = self._shape(node)
        if layer[1] == 'Edge.Cuts' and solid.is_empty and follows:
            self._warn('edge-zero', 'Zero-width Edge.Cuts lines were drawn with 0.05 mm width')
            solid = unary_union([line.buffer(0.025, quad_segs=self._segs(0.025)) for line in follows])
        if solid.is_empty and not follows:
            return
        self._add(layer[1], self._place(solid, at), [self._place(line, at) for line in follows])
        width = self._width_of(node)
        if not self._fill_of(node) and follows and width > 0:
            self._primitives.stroke(layer[1], self._place(solid, at), self._place(follows[0], at), width)
        elif self._fill_of(node) and node[0].split('_')[-1] in ('rect', 'circle') and not solid.is_empty:
            minx, miny, maxx, maxy = solid.bounds
            self._primitives.flash(layer[1], self._place(solid, at),
                                   self._place(Point((minx + maxx) / 2, (miny + maxy) / 2), at),
                                   'block', maxx - minx, maxy - miny)

    # ------------------------------------------------------------------ text

    def _text_style(self, effects):
        size_w = size_h = 1.0
        thickness = None
        bold = False
        justify = []
        mirror = False
        hide = False
        face = None
        if effects is not None:
            font = self._child(effects, 'font')
            if font is not None:
                size = self._child(font, 'size')
                if size is not None and len(size) >= 3:
                    size_w, size_h = self._num(size[1]), self._num(size[2])
                thickness = self._value(font, 'thickness')
                bold = self._flag(font, 'bold')
                face = self._child(font, 'face')
            justification = self._child(effects, 'justify')
            if justification is not None:
                justify = [item for item in justification[1:] if not isinstance(item, list)]
                mirror = 'mirror' in justify
            # KiCad 8+ writes (hide yes); KiCad 6/7 write a bare 'hide' atom
            hide = self._flag(effects, 'hide') or 'hide' in effects[1:]
        if size_w <= 0 or size_h <= 0:
            raise KiCadPcbParseError('Text with non-positive size')
        if thickness is None or thickness <= 0:
            thickness = size_h * (0.2 if bold else 0.15)
        return size_w, size_h, thickness, justify, mirror, hide, face

    def _expand_text(self, text, fields):
        def replace(match):
            name = match.group(1)
            if fields and name in fields:
                return fields[name]
            self._warn('textvar', 'Text variables such as ${%s} are not expanded' % name)
            return match.group(0)
        return re.sub(r'\$\{([^}]*)\}', replace, text)

    def _text_geometry(self, text, size_w, size_h, thickness, justify, mirror):
        """Stroke text at the origin (anchor), Y up -> (solid, follow lines)."""
        cell = size_h * self.CAP_RATIO / 6
        hscale = size_w / size_h
        text = re.sub(r'[~^_]\{([^{}]*)\}', r'\1', text)
        lines = text.split('\n')
        follows_all = []
        widths = []
        for row, line in enumerate(lines):
            pen = 0.0
            baseline = -row * size_h * self.LINE_SPACING
            row_follows = []
            for char in line:
                if char == ' ':
                    pen += self.SPACE_CELLS * cell * hscale
                    continue
                paths = self._glyphs.get(char) or self._glyphs.get(char.upper() if char.islower() else char)
                if paths is None:
                    self._unsupported_chars.add(char)
                    pen += self.SPACE_CELLS * cell * hscale
                    continue
                for path in paths:
                    row_follows.append(LineString([(pen + gx * cell * hscale, baseline + gy * cell)
                                                   for gx, gy in path]))
                pen += (max(gx for path in paths for gx, _ in path) + self.ADVANCE_PAD) * cell * hscale
            width = max(pen - self.ADVANCE_PAD * cell * hscale, 0.0)
            if 'right' in justify:
                shift = -width
            elif 'left' in justify:
                shift = 0.0
            else:
                shift = -width / 2
            widths.append(width)
            follows_all.extend(translate(line, shift, 0) for line in row_follows)
        top = 6 * cell
        bottom = -(len(lines) - 1) * size_h * self.LINE_SPACING
        if 'top' in justify:
            dy = -top
        elif 'bottom' in justify:
            dy = -bottom
        else:
            dy = -(top + bottom) / 2
        follows_all = [translate(line, 0, dy) for line in follows_all]
        if mirror:
            follows_all = [scale(line, xfact=-1, yfact=1, origin=(0, 0)) for line in follows_all]
        if not follows_all:
            return Polygon(), []
        # 1 buffer of all strokes gives the same shape as a union of 1 buffer per stroke, 2x faster
        solid = MultiLineString(follows_all).buffer(thickness / 2, quad_segs=self._segs(thickness / 2))
        return solid, follows_all

    def _text(self, layer, text, position, angle, effects, fields=None, at=None):
        """position/angle are in the parent frame (footprint-local when at is given)."""
        size_w, size_h, thickness, justify, mirror, hide, face = self._text_style(effects)
        if hide or not text:
            return
        text = self._expand_text(text, fields)
        if not text.strip():
            return
        self._text_used = True
        if face is not None:
            self._warn('truetype', 'TrueType text fonts are drawn with the approximate stroke font')
        solid, follows = self._text_geometry(text, size_w, size_h, thickness, justify, mirror)
        if solid.is_empty:
            return
        solid = translate(rotate(solid, angle, origin=(0, 0)), position[0], position[1])
        follows = [translate(rotate(line, angle, origin=(0, 0)), position[0], position[1]) for line in follows]
        self._add(layer, self._place(solid, at), [self._place(line, at) for line in follows])
        if self._primitives.enabled:
            placed_solid = self._place(solid, at)
            for line in follows:
                stroke = line.buffer(thickness / 2, quad_segs=self._segs(thickness / 2))
                self._primitives.stroke(layer, self._place(stroke, at).intersection(placed_solid),
                                        self._place(line, at), thickness)

    def _text_at(self, node):
        at = self._child(node, 'at')
        if at is None or len(at) < 3:
            raise KiCadPcbParseError('Text without a position')
        x, y = self._num(at[1]), self._num(at[2])
        angle = self._num(at[3]) if len(at) > 3 and not isinstance(at[3], list) else 0.0
        return self._pt(x, y), angle

    def _gr_text(self, node, at=None, fields=None):
        layer = self._child(node, 'layer')
        if layer is None or len(layer) < 2 or len(node) < 2:
            raise KiCadPcbParseError('Malformed text item')
        position, angle = self._text_at(node)
        self._text(layer[1], node[1], position, angle, self._child(node, 'effects'), fields, at)

    def _text_box(self, node, at=None, fields=None):
        layer = self._child(node, 'layer')
        if layer is None or len(layer) < 2 or len(node) < 2:
            raise KiCadPcbParseError('Malformed text box')
        if self._child(node, 'pts') is not None:
            corners = self._pts_list(self._child(node, 'pts'))
        else:
            a = self._xy_node(node, 'start')
            b = self._xy_node(node, 'end')
            corners = [a, (b[0], a[1]), b, (a[0], b[1])]
        width = self._width_of(node)
        if width > 0 and len(corners) >= 2:
            ring = corners + [corners[0]]
            self._add(layer[1], self._place(self._stroke(ring, width), at),
                      [self._place(LineString(ring), at)])
        center = (sum(p[0] for p in corners) / len(corners), sum(p[1] for p in corners) / len(corners))
        angle = 0.0
        pos = self._child(node, 'pos')
        if pos is not None and len(pos) > 3:
            angle = self._num(pos[3])
        self._warn('textbox', 'Text boxes are drawn with their border and centred, unwrapped text')
        self._text(layer[1], node[1], center, angle, self._child(node, 'effects'), fields, at)

    def _dimension(self, node):
        self._warn('dimension', 'Dimension lines and arrows are not drawn; only their text is imported')
        text = self._child(node, 'gr_text')
        if text is not None:
            self._gr_text(text)

    # ------------------------------------------------------------------ footprints and pads

    def _footprint(self, node):
        self._footprint_count += 1
        at = self._child(node, 'at')
        if at is None or len(at) < 3:
            raise KiCadPcbParseError('Footprint without a position')
        place = (self._num(at[1]), self._num(at[2]),
                 self._num(at[3]) if len(at) > 3 and not isinstance(at[3], list) else 0.0)
        fields = {}
        for item in node[1:]:
            if isinstance(item, list) and item and item[0] == 'property' and len(item) >= 3:
                fields[item[1]] = item[2]
            elif isinstance(item, list) and item and item[0] == 'fp_text' and len(item) >= 3:
                if item[1] == 'reference':
                    fields.setdefault('REFERENCE', item[2])
                elif item[1] == 'value':
                    fields.setdefault('VALUE', item[2])
        fields.setdefault('REFERENCE', fields.get('Reference', ''))
        fields.setdefault('VALUE', fields.get('Value', ''))
        local = {'mask': self._value(node, 'solder_mask_margin'),
                 'paste': self._value(node, 'solder_paste_margin'),
                 'ratio': self._value(node, 'solder_paste_margin_ratio')}
        for item in node[1:]:
            if not isinstance(item, list) or not item:
                continue
            key = item[0]
            if key in ('fp_line', 'fp_arc', 'fp_circle', 'fp_rect', 'fp_poly', 'fp_curve', 'fp_bezier'):
                self._graphic(item, place)
            elif key == 'pad':
                self._pad(item, place, local)
            elif key == 'fp_text':
                self._fp_text(item, place, fields)
            elif key == 'property':
                self._fp_property(item, place, fields)
            elif key == 'fp_text_box':
                self._text_box(item, place, fields)
            elif key == 'zone':
                self._zone(item, place)
            elif key == 'gr_text':
                self._gr_text(item, place, fields)
            elif key in ('dimension', 'image', 'target', 'table'):
                self._warn('skip:' + key, 'Footprint item "%s" is not imported' % key)
            elif key not in self.IGNORED_FOOTPRINT:
                self._warn('fp:' + key, 'Unsupported footprint item "%s" was skipped' % key)

    def _fp_text(self, node, place, fields):
        if len(node) < 3:
            raise KiCadPcbParseError('Malformed fp_text')
        layer = self._child(node, 'layer')
        if layer is None or len(layer) < 2 or 'hide' in node[1:]:
            return
        position, angle = self._text_at(node)
        self._text(layer[1], node[2], position, angle + place[2], self._child(node, 'effects'), fields, place)

    def _fp_property(self, node, place, fields):
        if len(node) < 3 or self._child(node, 'at') is None:
            return      # Metadata property without a text position
        layer = self._child(node, 'layer')
        if layer is None or len(layer) < 2 or self._flag(node, 'hide') or 'hide' in node[1:]:
            return
        position, angle = self._text_at(node)
        self._text(layer[1], node[2], position, angle + place[2], self._child(node, 'effects'), fields, place)

    # --- pad shapes

    def _rect_poly(self, w, h, radius=0.0, chamfer=0.0, chamfered=(), square=()):
        """Rectangle centred at the origin; corners TL TR BR BL may be chamfered, rounded or square."""
        if w <= 0 or h <= 0:
            return Polygon()
        radius = max(0.0, min(radius, w / 2, h / 2))
        chamfer = max(0.0, min(chamfer, w, h))
        hx, hy = w / 2, h / 2
        corners = (('top_left', (-hx, hy), (1, -1)), ('top_right', (hx, hy), (-1, -1)),
                   ('bottom_right', (hx, -hy), (-1, 1)), ('bottom_left', (-hx, -hy), (1, 1)))
        points = []
        for name, (cx, cy), (sx, sy) in corners:
            if name in chamfered and chamfer > 0:
                # Walk the corner along the boundary: edge point before, edge point after.
                before, after = {'top_left': ((cx, cy + sy * chamfer), (cx + sx * chamfer, cy)),
                                 'top_right': ((cx + sx * chamfer, cy), (cx, cy + sy * chamfer)),
                                 'bottom_right': ((cx, cy + sy * chamfer), (cx + sx * chamfer, cy)),
                                 'bottom_left': ((cx + sx * chamfer, cy), (cx, cy + sy * chamfer))}[name]
                points.extend([before, after])
            elif radius > 0 and name not in square:
                centre = (cx + sx * radius, cy + sy * radius)
                start_angle = {'top_left': 180, 'top_right': 90, 'bottom_right': 0, 'bottom_left': 270}[name]
                steps = max(2, self._steps(radius) // 4)
                for i in range(steps + 1):
                    angle = math.radians(start_angle - 90 * i / steps)
                    points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
            else:
                points.append((cx, cy))
        return Polygon(points)

    def _pad_spec(self, node):
        number = node[1] if len(node) > 1 else ''
        if len(node) < 4 or isinstance(node[2], list) or isinstance(node[3], list):
            raise KiCadPcbParseError('Malformed pad "%s"' % number)
        spec = {'number': number, 'type': node[2], 'shape': node[3]}
        if spec['type'] not in ('smd', 'thru_hole', 'np_thru_hole', 'connect'):
            raise KiCadPcbParseError('Pad "%s": unsupported pad type %s' % (number, spec['type']))
        if self._child(node, 'padstack') is not None:
            stack = self._child(node, 'padstack')
            mode = self._child(stack, 'mode')
            if mode is None or len(mode) < 2 or mode[1] != 'normal':
                raise KiCadPcbParseError('Pad "%s": per-layer padstacks are not supported' % number)
        size = self._child(node, 'size')
        if size is None or len(size) < 2:
            raise KiCadPcbParseError('Pad "%s" without a size' % number)
        spec['w'] = self._num(size[1])
        spec['h'] = self._num(size[2]) if len(size) > 2 else spec['w']
        if spec['w'] < 0 or spec['h'] < 0:
            raise KiCadPcbParseError('Pad "%s" has a negative size' % number)
        shape = spec['shape']
        if shape not in ('circle', 'rect', 'oval', 'roundrect', 'trapezoid', 'custom'):
            raise KiCadPcbParseError('Pad "%s": unsupported pad shape %s' % (number, shape))
        spec['rratio'] = self._value(node, 'roundrect_rratio', 0.0)
        spec['cratio'] = self._value(node, 'chamfer_ratio', 0.0)
        chamfer = self._child(node, 'chamfer')
        spec['chamfered'] = tuple(item for item in chamfer[1:] if not isinstance(item, list)) if chamfer else ()
        for corner in spec['chamfered']:
            if corner not in ('top_left', 'top_right', 'bottom_left', 'bottom_right'):
                raise KiCadPcbParseError('Pad "%s": unknown chamfer corner %s' % (number, corner))
        if spec['chamfered'] and spec['cratio'] <= 0:
            raise KiCadPcbParseError('Pad "%s": chamfered corners without chamfer_ratio' % number)
        delta = self._child(node, 'rect_delta')
        spec['delta'] = (self._num(delta[1]), self._num(delta[2])) if delta and len(delta) > 2 else (0.0, 0.0)
        spec['custom'] = None
        spec['convexhull'] = False
        if shape == 'custom':
            options = self._child(node, 'options')
            anchor = self._child(options, 'anchor') if options else None
            spec['anchor'] = anchor[1] if anchor and len(anchor) > 1 else 'circle'
            clearance = self._child(options, 'clearance') if options else None
            spec['convexhull'] = bool(clearance and len(clearance) > 1 and clearance[1] == 'convexhull')
            primitives = self._child(node, 'primitives')
            parts = []
            if primitives is not None:
                for item in primitives[1:]:
                    if not isinstance(item, list) or not item:
                        continue
                    if item[0] in ('gr_poly', 'gr_line', 'gr_arc', 'gr_circle', 'gr_rect', 'gr_curve',
                                   'gr_bezier'):
                        parts.append(self._shape(item)[0])
                    elif item[0] in ('width', 'fill'):
                        continue
                    else:
                        raise KiCadPcbParseError('Pad "%s": unsupported custom primitive %s' % (number, item[0]))
            spec['custom'] = unary_union(parts) if parts else Polygon()
        return spec

    def _pad_shape(self, spec, grow=0.0, node=None):
        """Pad outline at the origin, unrotated, Y up; grow expands (mask/paste margin) or shrinks."""
        shape, w, h = spec['shape'], spec['w'], spec['h']
        if shape == 'circle':
            radius = w / 2 + grow
            return Point(0, 0).buffer(radius, quad_segs=self._segs(radius)) if radius > 0 else Polygon()
        if shape == 'oval':
            gw, gh = w + 2 * grow, h + 2 * grow
            if gw <= 0 or gh <= 0:
                return Polygon()
            radius = min(gw, gh) / 2
            half = abs(gw - gh) / 2
            ends = [(-half, 0), (half, 0)] if gw >= gh else [(0, -half), (0, half)]
            if half == 0:
                return Point(0, 0).buffer(radius, quad_segs=self._segs(radius))
            return LineString(ends).buffer(radius, quad_segs=self._segs(radius))
        if shape in ('rect', 'roundrect'):
            small = min(w, h)
            radius = spec['rratio'] * small + grow if shape == 'roundrect' else 0.0
            chamfer = spec['cratio'] * small
            chamfered = spec['chamfered']
            if shape == 'roundrect' and radius <= 0 and not chamfered:
                return self._rect_poly(w + 2 * grow, h + 2 * grow)
            return self._rect_poly(w + 2 * grow, h + 2 * grow, max(radius, 0.0), chamfer, chamfered)
        if shape == 'trapezoid':
            dx, dy = spec['delta']
            hx, hy = w / 2, h / 2
            poly = Polygon([(-hx - dy / 2, -(hy + dx / 2)), (-hx + dy / 2, hy + dx / 2),
                            (hx - dy / 2, hy - dx / 2), (hx + dy / 2, -(hy - dx / 2))])
            if not poly.is_valid or poly.area <= 0:
                raise KiCadPcbParseError('Pad "%s": invalid trapezoid delta' % spec['number'])
            return poly if grow == 0 else poly.buffer(grow, join_style='mitre')
        # custom
        anchor = Point(0, 0).buffer(w / 2, quad_segs=self._segs(w / 2)) if spec['anchor'] == 'circle' \
            else box(-w / 2, -h / 2, w / 2, h / 2)
        if spec['anchor'] not in ('circle', 'rect'):
            raise KiCadPcbParseError('Pad "%s": unsupported custom anchor %s' % (spec['number'], spec['anchor']))
        poly = unary_union([anchor, spec['custom']])
        if grow:
            poly = (poly.convex_hull if spec['convexhull'] else poly).buffer(grow, quad_segs=self._segs(grow))
        return poly

    def _drill_spec(self, node, number):
        drill = self._child(node, 'drill')
        if drill is None:
            return None
        atoms = [item for item in drill[1:] if not isinstance(item, list)]
        oval = bool(atoms) and atoms[0] == 'oval'
        numbers = [self._num(item) for item in (atoms[1:] if oval else atoms)]
        if not numbers or numbers[0] <= 0:
            return None
        offset = self._child(drill, 'offset')
        ox, oy = (self._num(offset[1]), self._num(offset[2])) if offset and len(offset) > 2 else (0.0, 0.0)
        if oval and len(numbers) > 1 and abs(numbers[0] - numbers[1]) > 1e-9:
            return numbers[0], numbers[1], ox, oy
        return numbers[0], numbers[0], ox, oy

    def _margin(self, local_value, fp_value, board_value):
        if local_value is not None:
            return local_value
        if fp_value is not None:
            return fp_value
        return board_value

    def _pad(self, node, place, local):
        spec = self._pad_spec(node)
        number = spec['number']
        at = self._child(node, 'at')
        if at is None or len(at) < 3:
            raise KiCadPcbParseError('Pad "%s" without a position' % number)
        ax, ay = self._num(at[1]), self._num(at[2])
        angle = self._num(at[3]) if len(at) > 3 and not isinstance(at[3], list) else 0.0
        centre = self._place_point(self._pt(ax, ay), place)
        names = self._child(node, 'layers')
        if names is None:
            raise KiCadPcbParseError('Pad "%s" without a layer list' % number)
        layers = self._expand_layers([item for item in names[1:] if not isinstance(item, list)])
        ptype = spec['type']
        net = self._net(node)

        def put(shape):
            return translate(rotate(shape, angle, origin=(0, 0)), centre[0], centre[1])

        native_shape = spec['shape'] if spec['shape'] in ('circle', 'rect', 'oval') and not spec['chamfered'] else 'block'

        def add_pad(layer, shape, grow=0.0):
            self._add(layer, shape)
            width, height = spec['w'] + 2 * grow, spec['h'] + 2 * grow
            if native_shape == 'block' and not shape.is_empty:
                minx, miny, maxx, maxy = shape.bounds
                width, height = maxx - minx, maxy - miny
            self._primitives.flash(layer, shape, centre, native_shape,
                                   width, height, angle)

        # Hole first: it decides whether an NPTH pad leaves any copper.
        hole_geo = None
        drill = self._drill_spec(node, number)
        if ptype in ('thru_hole', 'np_thru_hole'):
            if drill is None:
                raise KiCadPcbParseError('Pad "%s" is %s but has no drill' % (number, ptype))
            dw, dh, ox, oy = drill
            rad = math.radians(angle)
            offset_x = ox * math.cos(rad) + oy * math.sin(rad)
            offset_y = ox * math.sin(rad) - oy * math.cos(rad)
            hole_pos = (centre[0] + offset_x, centre[1] + offset_y)
            diameter = min(dw, dh)
            along = angle if dw >= dh else angle + 90
            hole_geo = self._drill(hole_pos, diameter, ptype == 'thru_hole',
                                   (self.copper_layers[0], self.copper_layers[-1]),
                                   slot=max(dw, dh), angle=along)
        body = put(self._pad_shape(spec))
        prune = (ptype == 'thru_hole' and self._flag(node, 'remove_unused_layers')
                 and len(self.copper_layers) > 2)
        keep_end = self._flag(node, 'keep_end_layers')
        for layer in layers:
            if layer in self.copper_layers:
                if ptype == 'np_thru_hole' and hole_geo is not None and \
                        body.difference(hole_geo.buffer(1e-6)).area < 1e-9:
                    continue        # Pad no bigger than its hole: no copper
                if prune and not (keep_end and layer in (self.copper_layers[0], self.copper_layers[-1])):
                    self._pending.append((layer, net, body, (centre, native_shape,
                                          spec['w'], spec['h'], angle)))
                else:
                    add_pad(layer, body)
                    self._remember(layer, net, body)
            elif layer.endswith('.Cu'):
                continue        # Layer the board does not have (footprint written for more layers)
            elif layer.endswith('.Mask'):
                margin = self._margin(self._value(node, 'solder_mask_margin'), local['mask'], self._mask_margin)
                add_pad(layer, put(self._pad_shape(spec, margin)), margin)
            elif layer.endswith('.Paste'):
                margin = self._margin(self._value(node, 'solder_paste_margin'), local['paste'],
                                      self._paste_margin)
                ratio = self._margin(self._value(node, 'solder_paste_margin_ratio'), local['ratio'],
                                     self._paste_ratio)
                grow = margin + min(spec['w'], spec['h']) * ratio
                add_pad(layer, put(self._pad_shape(spec, grow)), grow)
            else:
                add_pad(layer, body)

    # ------------------------------------------------------------------ result

    def _outline(self):
        lines = [set_precision(line, 1e-5) for line in self._follows.get('Edge.Cuts', [])]
        if not lines:
            return Polygon()
        faces = list(polygonize(unary_union(lines)))
        if not faces:
            self._warn('outline', 'Edge.Cuts does not form a closed outline; board outline left empty')
            return Polygon()
        kept = []
        for face in faces:
            probe = face.representative_point()
            if not any(other is not face and other.interiors and
                       any(Polygon(ring).contains(probe) for ring in other.interiors) for other in faces):
                kept.append(face)
        return unary_union(kept) if len(kept) > 1 else kept[0]

    def _finish(self):
        self.board_outline = self._outline()
        ordered = list(self.copper_layers)
        ordered += [name for name in self._layer_order if name not in ordered]
        ordered += [name for name in self._solids if name not in ordered]
        for layer in ordered:
            geo = unary_union(self._solids.get(layer, []))
            polygons = self._polygons_of(geo)
            if not polygons:
                continue
            # Enclosing polygons MUST precede islands, otherwise an enclosing hole erases the island.
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            self.layers[layer] = {
                'name': self._layer_names.get(layer, layer),
                'solid_geometry': polygons,
                'follow_geometry': list(self._follows.get(layer, [])),
                'tools': {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': polygon} for polygon in polygons]}}}
            self._primitives.finish(layer, self.layers[layer])
        self.default_layers = [name for name in self.layers
                               if name in self.copper_layers or name in self.DEFAULT_TECH]
        if self._text_used:
            self.warnings.append('Text is drawn with an approximate stroke font; glyph shapes and spacing '
                                 'differ from KiCad')
        if self._unsupported_chars:
            self.warnings.append('Characters without a glyph were left blank: %s' %
                                 ''.join(sorted(self._unsupported_chars)))
