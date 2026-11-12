# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native Eagle board (.brd, XML) geometry reader            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read saved Eagle XML board (.brd) manufacturing geometry without Eagle.

Written from the public eagle.dtd and the documented design-rule parameter names, not from
Eagle or any other CAD source. Output is in millimetres, Y up, in the file's own coordinates
(the frame of Eagle's CAM output before any CAM offset). Pad, via and mask sizes follow the
board's design rules. Anything that would change copper and cannot be represented (signal
polygons that need a pour, unsupported pad shapes or rule settings) raises EagleBrdParseError;
skipped non-copper items are warned.
"""

import math
import re
import xml.etree.ElementTree as ET

from shapely import LineString, Point, Polygon, box, set_precision
from shapely.affinity import affine_transform
from shapely.ops import polygonize, unary_union

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.ParseKiCadPcb import KiCadPcbParser
from appParsers.board_primitives import BoardPrimitives


class EagleBrdParseError(ValueError):
    """Malformed data or copper geometry that cannot be represented safely."""


_ROT = re.compile(r'^(S?)(M?)R(-?\d+(?:\.\d+)?)$')
_LENGTH = re.compile(r'^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(mil|mm|mic|inch|in)?\s*$')
_UNITS_MM = {None: 1.0, 'mm': 1.0, 'mil': 0.0254, 'mic': 0.001, 'inch': 25.4, 'in': 25.4}
_ENCODING = re.compile(r'^(\s*<\?xml[^>]*?)\s+encoding\s*=\s*(?:"[^"]*"|\'[^\']*\')')

# Standard Eagle layer names, used when the file's <layers> table lacks an entry.
_STANDARD_LAYERS = {
    1: 'Top', 16: 'Bottom', 17: 'Pads', 18: 'Vias', 19: 'Unrouted', 20: 'Dimension', 21: 'tPlace',
    22: 'bPlace', 23: 'tOrigins', 24: 'bOrigins', 25: 'tNames', 26: 'bNames', 27: 'tValues',
    28: 'bValues', 29: 'tStop', 30: 'bStop', 31: 'tCream', 32: 'bCream', 33: 'tFinish',
    34: 'bFinish', 35: 'tGlue', 36: 'bGlue', 37: 'tTest', 38: 'bTest', 39: 'tKeepout',
    40: 'bKeepout', 41: 'tRestrict', 42: 'bRestrict', 43: 'vRestrict', 44: 'Drills', 45: 'Holes',
    46: 'Milling', 47: 'Measures', 48: 'Document', 49: 'Reference', 51: 'tDocu', 52: 'bDocu'}
for _i in range(2, 16):
    _STANDARD_LAYERS[_i] = 'Route%d' % _i

# Layer that an object moves to when its element is placed on the other side of the board.
_MIRROR_LAYER = {}
for _a, _b in ((1, 16), (21, 22), (23, 24), (25, 26), (27, 28), (29, 30), (31, 32), (33, 34),
               (35, 36), (37, 38), (39, 40), (41, 42), (51, 52)):
    _MIRROR_LAYER[_a] = _b
    _MIRROR_LAYER[_b] = _a

_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

# Design rules in force when the file carries none (Eagle's own defaults).
_DEFAULT_RULES = {
    'rvPadTop': 0.25, 'rvPadInner': 0.25, 'rvPadBottom': 0.25,
    'rvViaOuter': 0.25, 'rvViaInner': 0.25,
    'rlMinPadTop': 0.254, 'rlMaxPadTop': 0.508, 'rlMinPadInner': 0.254, 'rlMaxPadInner': 0.508,
    'rlMinPadBottom': 0.254, 'rlMaxPadBottom': 0.508,
    'rlMinViaOuter': 0.2032, 'rlMaxViaOuter': 0.508, 'rlMinViaInner': 0.2032, 'rlMaxViaInner': 0.508,
    'psTop': -1.0, 'psBottom': -1.0, 'psFirst': -1.0, 'psElongationLong': 100.0,
    'psElongationOffset': 100.0, 'mvStopFrame': 1.0, 'mvCreamFrame': 0.0,
    'mlMinStopFrame': 0.1016, 'mlMaxStopFrame': 0.1016, 'mlMinCreamFrame': 0.0,
    'mlMaxCreamFrame': 0.0, 'mlViaStopLimit': 0.0, 'srRoundness': 0.0, 'srMinRoundness': 0.0,
    'srMaxRoundness': 0.0}


class EagleBrdParser:
    MIN_VERSION = 6                 # first release that saved XML
    HAIRLINE = 0.01                 # width given to zero-width outline lines (Dimension, Milling)
    DEFAULT_TECH = (29, 30, 31, 32, 21, 22, 25, 26, 20)
    SKIPPED_LAYERS = frozenset([19] + list(range(91, 100)))     # Unrouted and schematic layers
    OUTLINE_LAYERS = frozenset([20, 46])

    # Eagle's vector font is not public: glyph shapes come from the KiCad reader's stroke font.
    CAP_RATIO = 0.95                # ascender height / Eagle text size (calibrated on the GTO/GBO reference)
    DEFAULT_RATIO = 8.0             # stroke width as percent of the text size
    MIRROR_FIRST = False            # True: mirror, then rotate; False: rotate, then mirror

    PAD_SHAPES = ('square', 'round', 'octagon', 'long', 'offset')
    RULE_SHAPES = {0: 'square', 1: 'round', 2: 'octagon'}

    def __init__(self, app=None, circle_steps=None):
        # circle_steps: a fixed number of segments for every circle; None: the Gerber "Circle Steps" preference
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        self._text_engine = KiCadPcbParser(app=app, circle_steps=circle_steps)
        self._reset()

    # ------------------------------------------------------------------ state

    def _reset(self):
        self._primitives = BoardPrimitives(self._options)
        self.layers = {}
        self.drill_groups = {}
        self.warnings = []
        self.board_outline = Polygon()
        self.default_layers = []
        self.version = ''
        self.copper_layers = []         # copper layer numbers, top to bottom
        self._layer_names = {}
        self._rules = dict(_DEFAULT_RULES)
        self._solids = {}
        self._follows = {}
        self._packages = {}
        self._warned = set()
        self._text_used = False
        self._holes = []
        self._drills = {}
        self._layer_setup = None

    def _warn(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(message)

    # ------------------------------------------------------------------ entry points

    def parse_file(self, filename):
        try:
            with open(str(filename), 'rb') as handle:
                data = handle.read()
        except OSError as error:
            self._reset()
            raise EagleBrdParseError('Cannot read Eagle board file: %s' % error) from error
        return self._parse_bytes(data)

    def parse_text(self, text):
        if not isinstance(text, str):
            raise EagleBrdParseError('parse_text needs a str')
        # A str cannot also carry an encoding declaration for the XML parser.
        return self._parse_bytes(_ENCODING.sub(r'\1', text, count=1).encode('utf-8'))

    def _parse_bytes(self, data):
        self._reset()
        try:
            self._parse(self._read_xml(data))
        except EagleBrdParseError:
            self._fail()
            raise
        except Exception as error:
            self._fail()
            raise EagleBrdParseError('Cannot read Eagle board: %s' % error) from error
        return self

    def _fail(self):
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []
        self.board_outline = Polygon()

    @staticmethod
    def _read_xml(data):
        stripped = data.lstrip(b'\xef\xbb\xbf \t\r\n')
        if not stripped.startswith(b'<'):
            raise EagleBrdParseError('Not an Eagle XML board: this looks like the binary format of Eagle 5 '
                                     'or older; open it in Eagle 6 or newer and save it again')
        if b'<!ENTITY' in data:
            raise EagleBrdParseError('XML entity declarations are not supported')
        try:
            return ET.fromstring(data, parser=ET.XMLParser())
        except ET.ParseError as error:
            raise EagleBrdParseError('Malformed XML: %s' % error) from error

    # ------------------------------------------------------------------ value helpers

    @staticmethod
    def _num(value, what='number'):
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise EagleBrdParseError('Invalid %s: %r' % (what, value)) from error
        if not math.isfinite(number):
            raise EagleBrdParseError('Non-finite %s: %r' % (what, value))
        return number

    def _attr(self, node, key, default=None):
        """Numeric attribute; missing and no default means the file is malformed."""
        value = node.get(key)
        if value is None:
            if default is None:
                raise EagleBrdParseError('<%s> without required attribute "%s"' % (node.tag, key))
            return default
        return self._num(value, '%s of <%s>' % (key, node.tag))

    def _layer_no(self, node):
        value = node.get('layer')
        if value is None:
            raise EagleBrdParseError('<%s> without a layer' % node.tag)
        try:
            return int(value)
        except ValueError as error:
            raise EagleBrdParseError('Invalid layer %r in <%s>' % (value, node.tag)) from error

    @staticmethod
    def _length(value, what):
        match = _LENGTH.match(value)
        if not match:
            raise EagleBrdParseError('Invalid design rule %s: %r' % (what, value))
        return float(match.group(1)) * _UNITS_MM[match.group(2)]

    @staticmethod
    def _rot(spec):
        """Rotation string -> (spin, mirror, angle in degrees)."""
        if not spec:
            return False, False, 0.0
        match = _ROT.match(spec)
        if not match:
            raise EagleBrdParseError('Invalid rotation %r' % spec)
        return bool(match.group(1)), bool(match.group(2)), float(match.group(3))

    def _steps(self, radius):
        # Segments of a whole circle of this radius (millimetres): the fixed override or the Gerber preference.
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _segs(self, radius):
        return circle_quad_segs(self._steps(radius))

    # ------------------------------------------------------------------ transforms

    @staticmethod
    def _compose(outer, inner):
        """Matrix that applies inner first, then outer. (a, b, c, d, e, f): x' = ax+by+c, y' = dx+ey+f."""
        a1, b1, c1, d1, e1, f1 = outer
        a2, b2, c2, d2, e2, f2 = inner
        return (a1 * a2 + b1 * d2, a1 * b2 + b1 * e2, a1 * c2 + b1 * f2 + c1,
                d1 * a2 + e1 * d2, d1 * b2 + e1 * e2, d1 * c2 + e1 * f2 + f1)

    def _matrix(self, x, y, angle, mirror):
        rad = math.radians(angle)
        c, s = math.cos(rad), math.sin(rad)
        rotation = (c, -s, 0.0, s, c, 0.0)
        flip = (-1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        if not mirror:
            body = rotation
        elif self.MIRROR_FIRST:
            body = self._compose(rotation, flip)
        else:
            body = self._compose(flip, rotation)
        return self._compose((1.0, 0.0, x, 0.0, 1.0, y), body)

    @staticmethod
    def _apply(geo, matrix):
        a, b, c, d, e, f = matrix
        return affine_transform(geo, (a, b, d, e, c, f))

    @staticmethod
    def _apply_pt(point, matrix):
        a, b, c, d, e, f = matrix
        return (a * point[0] + b * point[1] + c, d * point[0] + e * point[1] + f)

    # ------------------------------------------------------------------ geometry helpers

    def _stroke(self, points, width, flat=False):
        """Round-capped (or flat-capped) stroke along a polyline."""
        if width <= 0:
            return Polygon()
        points = list(points)
        if all(point == points[0] for point in points):
            return Point(points[0]).buffer(width / 2, quad_segs=self._segs(width / 2))
        return LineString(points).buffer(width / 2, quad_segs=self._segs(width / 2),
                                         cap_style='flat' if flat else 'round')

    def _arc_points(self, p1, p2, curve):
        """Points along the arc from p1 to p2 that sweeps `curve` degrees (positive = counter-clockwise)."""
        if curve == 0:
            return [p1, p2]
        if abs(curve) >= 360:
            raise EagleBrdParseError('Arc with a sweep of %g degrees' % curve)
        if curve < 0:
            return self._arc_points(p2, p1, -curve)[::-1]
        theta = math.radians(curve)
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        chord = math.hypot(dx, dy)
        if chord == 0:
            raise EagleBrdParseError('Arc with identical end points')
        radius = chord / (2 * math.sin(theta / 2))
        offset = radius * math.cos(theta / 2)
        cx = (p1[0] + p2[0]) / 2 - dy / chord * offset
        cy = (p1[1] + p2[1]) / 2 + dx / chord * offset
        start = math.atan2(p1[1] - cy, p1[0] - cx)
        count = max(2, math.ceil(self._steps(radius) * theta / (2 * math.pi)))
        points = [p1]
        for i in range(1, count):
            angle = start + theta * i / count
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        points.append(p2)
        return points

    @staticmethod
    def _polygons_of(geo):
        if geo.is_empty:
            return []
        if isinstance(geo, Polygon):
            return [geo]
        if geo.geom_type in ('MultiPolygon', 'GeometryCollection'):
            return [polygon for child in geo.geoms for polygon in EagleBrdParser._polygons_of(child)]
        if geo.geom_type in ('LineString', 'MultiLineString', 'Point', 'MultiPoint', 'LinearRing'):
            return []
        raise EagleBrdParseError('Non-polygon material geometry: %s' % geo.geom_type)

    def _disc(self, radius, centre=(0.0, 0.0)):
        return Point(centre).buffer(radius, quad_segs=self._segs(radius))

    def _octagon(self, flats):
        """Regular octagon with flat sides on the axes; `flats` is the distance between opposite flats."""
        radius = flats / 2 / math.cos(math.pi / 8)
        return Polygon([(radius * math.cos(math.pi / 8 + k * math.pi / 4),
                         radius * math.sin(math.pi / 8 + k * math.pi / 4)) for k in range(8)])

    def _stadium(self, start, end, diameter):
        return LineString([start, end]).buffer(diameter / 2, quad_segs=self._segs(diameter / 2))

    def _rounded_box(self, width, height, radius):
        if width <= 0 or height <= 0:
            return Polygon()
        radius = min(radius, width / 2, height / 2)
        if radius <= 0:
            return box(-width / 2, -height / 2, width / 2, height / 2)
        if radius * 2 >= min(width, height) - 1e-12:
            if width >= height:
                return self._stadium((-(width - height) / 2, 0), ((width - height) / 2, 0), height)
            return self._stadium((0, -(height - width) / 2), (0, (height - width) / 2), width)
        return box(-width / 2 + radius, -height / 2 + radius,
                   width / 2 - radius, height / 2 - radius).buffer(radius, quad_segs=self._segs(radius))

    # ------------------------------------------------------------------ layers and storage

    def _name_of(self, number):
        return self._layer_names.get(number) or _STANDARD_LAYERS.get(number) or 'Layer%d' % number

    def _is_copper(self, number):
        return 1 <= number <= 16

    def _add(self, number, geo, follow=None, strict=False):
        """Store geometry on a layer number. Pads/Vias layers spread over every copper layer."""
        if number in self.SKIPPED_LAYERS:
            return
        if number in (17, 18):
            targets = list(self.copper_layers)
        elif self._is_copper(number):
            if number not in self.copper_layers:
                if strict:
                    raise EagleBrdParseError('Item uses copper layer %d that is not in the board stackup'
                                             % number)
                self._warn('layer:%d' % number, 'Items on copper layer %d, which the board does not use, '
                                                'were skipped' % number)
                return
            targets = [number]
        else:
            targets = [number]
        if not geo.is_valid:
            geo = geo.buffer(0)
            if not geo.is_valid:
                raise EagleBrdParseError('Invalid geometry on layer %d' % number)
        polygons = self._polygons_of(geo)
        for target in targets:
            if polygons:
                self._solids.setdefault(target, []).extend(polygons)
            if follow:
                self._follows.setdefault(target, []).extend(follow)

    def _read_layers(self, root):
        table = root.find('drawing/layers')
        if table is not None:
            for layer in table.findall('layer'):
                try:
                    number = int(layer.get('number'))
                except (TypeError, ValueError) as error:
                    raise EagleBrdParseError('Malformed <layer> entry') from error
                if layer.get('name'):
                    self._layer_names[number] = layer.get('name')

    def _read_rules(self, board):
        rules = board.find('designrules')
        if rules is None:
            self._warn('rules', 'Board has no design rules; Eagle default rules were assumed')
            return
        for param in rules.findall('param'):
            name, value = param.get('name'), param.get('value')
            if name is None or value is None:
                raise EagleBrdParseError('Malformed design rule parameter')
            if name == 'layerSetup':
                self._layer_setup = value
            elif name in _DEFAULT_RULES:
                self._rules[name] = self._length(value, name)

    def _read_stackup(self, board):
        setup = self._layer_setup
        numbers = []
        if setup:
            numbers = sorted({int(item) for item in re.findall(r'\d+', setup) if 1 <= int(item) <= 16})
        if not numbers:
            used = {1, 16}
            for wire in board.iter('wire'):
                try:
                    used.add(int(wire.get('layer')))
                except (TypeError, ValueError):
                    pass
            numbers = sorted(number for number in used if 1 <= number <= 16)
        if 1 not in numbers or 16 not in numbers:
            raise EagleBrdParseError('Board stackup %r lacks the Top or Bottom copper layer' % (setup,))
        self.copper_layers = numbers

    # ------------------------------------------------------------------ main parse

    def _parse(self, root):
        if root.tag != 'eagle':
            raise EagleBrdParseError('Not an Eagle file: expected an <eagle> document')
        self.version = root.get('version', '')
        match = re.match(r'\d+', self.version)
        if not match:
            raise EagleBrdParseError('Eagle file without a version')
        if int(match.group(0)) < self.MIN_VERSION:
            raise EagleBrdParseError('Eagle file format %s is older than Eagle %d; open and save it in '
                                     'Eagle 6 or newer first' % (self.version, self.MIN_VERSION))
        board = root.find('drawing/board')
        if board is None:
            raise EagleBrdParseError('Eagle file has no <board> (it is a schematic or a library)')
        self._read_layers(root)
        self._read_rules(board)
        self._read_stackup(board)
        self._read_libraries(board)
        plain = board.find('plain')
        if plain is not None:
            for item in plain:
                self._item(item, _IDENTITY, False, None, 'plain')
        elements = board.find('elements')
        if elements is not None:
            for element in elements.findall('element'):
                self._element(element)
        signals = board.find('signals')
        if signals is not None:
            for signal in signals.findall('signal'):
                self._signal(signal)
        self._finish()

    def _read_libraries(self, board):
        libraries = board.find('libraries')
        if libraries is None:
            return
        for library in libraries.findall('library'):
            packages = library.find('packages')
            if packages is None:
                continue
            for package in packages.findall('package'):
                self._packages[(library.get('name', ''), package.get('name', ''))] = package

    # ------------------------------------------------------------------ signals

    def _signal(self, signal):
        for item in signal:
            tag = item.tag
            if tag == 'wire':
                number = self._layer_no(item)
                if number == 19:
                    continue
                if not self._is_copper(number):
                    self._warn('sigwire:%d' % number, 'Signal wires on non-copper layer %d were skipped' % number)
                    continue
                self._wire(item, _IDENTITY, False, 'signal')
            elif tag == 'via':
                self._via(item)
            elif tag == 'polygon':
                raise EagleBrdParseError(
                    'Signal polygon (copper pour) in signal "%s" is not supported: Eagle recomputes '
                    'pours from the design rules, which this importer does not do' % signal.get('name', ''))
            elif tag == 'contactref':
                continue
            else:
                self._warn('signal:' + tag, 'Unsupported signal item <%s> was skipped' % tag)

    # ------------------------------------------------------------------ rule helpers

    def _clamp(self, value, low, high):
        return min(max(value, low), high)

    def _kind(self, number):
        if number == self.copper_layers[0]:
            return 'Top'
        if number == self.copper_layers[-1]:
            return 'Bottom'
        return 'Inner'

    def _restring_diameter(self, drill, kind, prefix):
        """Smallest copper diameter for a drill: drill plus a restring that follows the Restring rules.

        A diameter written in the file only counts when it is larger (Eagle never goes below the rules).
        """
        key = 'Outer' if (prefix == 'Via' and kind != 'Inner') else ('Inner' if prefix == 'Via' else kind)
        ratio = self._rules['rv%s%s' % (prefix, key)]
        low = self._rules['rlMin%s%s' % (prefix, key)]
        high = self._rules['rlMax%s%s' % (prefix, key)]
        return drill + 2 * self._clamp(ratio * drill, low, high)

    def _frame(self, which, width, height):
        """Mask frame around (stop) or inside (cream) a pad, from the Masks rules."""
        ratio = self._rules['mv%sFrame' % which]
        low = self._rules['mlMin%sFrame' % which]
        high = self._rules['mlMax%sFrame' % which]
        return self._clamp(ratio * min(width, height), low, high)

    # ------------------------------------------------------------------ pads and vias

    def _pad_shape(self, shape, diameter, kind, first):
        """Pad copper outline at the origin, unrotated."""
        shape = self._effective_shape(shape, kind, first)
        if shape == 'round':
            return self._disc(diameter / 2)
        if shape == 'square':
            return box(-diameter / 2, -diameter / 2, diameter / 2, diameter / 2)
        if shape == 'octagon':
            return self._octagon(diameter)
        if shape == 'long':
            length = diameter * (1 + self._rules['psElongationLong'] / 100)
            half = (length - diameter) / 2
            return self._stadium((-half, 0), (half, 0), diameter)
        if shape == 'offset':
            length = diameter * (1 + self._rules['psElongationOffset'] / 100)
            return self._stadium((0, 0), (length - diameter, 0), diameter)
        raise EagleBrdParseError('Unsupported pad shape %r' % shape)

    @staticmethod
    def _join(shape):
        return 'mitre' if shape in ('square', 'octagon') else 'round'

    def _record_drill(self, position, diameter, plated, extent=None):
        if diameter <= 0:
            raise EagleBrdParseError('Non-positive hole diameter')
        group = 'NPTH' if not plated else ('PTH' if extent is None else 'PTH_%d-%d' % extent)
        tools = self._drills.setdefault(group, {})
        key = round(diameter, 6)
        tool = tools.setdefault(key, {'tooldia': diameter, 'drills': [], 'slots': []})
        tool['drills'].append(Point(position))

    def _pad(self, node, xf, mirror):
        x, y = self._attr(node, 'x'), self._attr(node, 'y')
        drill = self._attr(node, 'drill')
        if drill <= 0:
            raise EagleBrdParseError('Pad "%s" has a non-positive drill' % node.get('name', ''))
        diameter = self._attr(node, 'diameter', 0.0)
        shape = node.get('shape', 'round')
        if shape not in self.PAD_SHAPES:
            raise EagleBrdParseError('Pad "%s": unsupported pad shape %s' % (node.get('name', ''), shape))
        first = node.get('first', 'no') == 'yes'
        _, pad_mirror, angle = self._rot(node.get('rot'))
        if pad_mirror:
            raise EagleBrdParseError('Pad "%s" has a mirrored rotation' % node.get('name', ''))
        place = self._compose(xf, self._matrix(x, y, angle, False))
        centre = self._apply_pt((0.0, 0.0), place)
        self._record_drill(centre, drill, True)
        stop = node.get('stop', 'yes') != 'no'
        for number in self.copper_layers:
            kind = self._kind(number)
            size = max(diameter, self._restring_diameter(drill, kind, 'Pad'))
            outline = self._pad_shape(shape, size, kind, first)
            solid = self._apply(outline, place)
            self._add(number, solid)
            effective = self._effective_shape(shape, kind, first)
            length = size * (1 + self._rules['psElongationLong' if effective == 'long' else
                                            'psElongationOffset'] / 100) if effective in ('long', 'offset') else size
            flash_centre = self._apply_pt(((length - size) / 2 if effective == 'offset' else 0, 0), place)
            native_shape = {'round': 'circle', 'square': 'rect', 'long': 'oval', 'offset': 'oval'}.get(effective, 'block')
            rotation = math.degrees(math.atan2(place[3], place[0]))
            self._primitives.flash(number, solid, flash_centre, native_shape, length, size, rotation)
            side = 29 if number == self.copper_layers[0] else (30 if number == self.copper_layers[-1] else None)
            if side and stop:
                minor = min(*self._extent_of(outline))
                frame = self._frame('Stop', minor, minor)
                join = self._join(self._effective_shape(shape, kind, first))
                opening = outline.buffer(frame, quad_segs=self._segs(frame), join_style=join)
                opening = self._apply(opening, place)
                self._add(side, opening)
                self._primitives.flash(side, opening, flash_centre, native_shape,
                                       length + 2 * frame, size + 2 * frame, rotation)

    def _effective_shape(self, shape, kind, first):
        """Library shape, unless the Shapes design rules (psTop, psBottom, psFirst) force another."""
        rule = self._rules['psFirst'] if first else (-1.0 if kind == 'Inner' else self._rules['ps' + kind])
        if rule == -1.0:
            return shape
        if int(rule) != rule or int(rule) not in self.RULE_SHAPES:
            raise EagleBrdParseError('Unsupported pad shape design rule value %g' % rule)
        return self.RULE_SHAPES[int(rule)]

    @staticmethod
    def _extent_of(geo):
        minx, miny, maxx, maxy = geo.bounds
        return maxx - minx, maxy - miny

    def _via(self, node):
        x, y = self._attr(node, 'x'), self._attr(node, 'y')
        drill = self._attr(node, 'drill')
        if drill <= 0:
            raise EagleBrdParseError('Via with a non-positive drill')
        diameter = self._attr(node, 'diameter', 0.0)
        shape = node.get('shape', 'round')
        if shape not in ('square', 'round', 'octagon'):
            raise EagleBrdParseError('Via with unsupported shape %s' % shape)
        extent = node.get('extent')
        if extent is None:
            raise EagleBrdParseError('Via without an extent')
        try:
            first, last = (int(part) for part in extent.split('-'))
        except ValueError as error:
            raise EagleBrdParseError('Invalid via extent %r' % extent) from error
        if first > last:
            first, last = last, first
        span = [number for number in self.copper_layers if first <= number <= last]
        if not span or first not in self.copper_layers or last not in self.copper_layers:
            raise EagleBrdParseError('Via extent %r is outside the copper stackup' % extent)
        full = span == self.copper_layers
        self._record_drill((x, y), drill, True, None if full else (first, last))
        limit = self._rules['mlViaStopLimit']
        for number in span:
            kind = self._kind(number)
            size = max(diameter, self._restring_diameter(drill, kind, 'Via'))
            if shape == 'round':
                outline = self._disc(size / 2, (x, y))
            elif shape == 'square':
                outline = box(x - size / 2, y - size / 2, x + size / 2, y + size / 2)
            else:
                outline = self._octagon(size)
                outline = affine_transform(outline, (1, 0, 0, 1, x, y))
            self._add(number, outline)
            self._primitives.flash(number, outline, (x, y), {'round': 'circle', 'square': 'rect'}.get(shape, 'block'),
                                   size, size)
            side = 29 if number == self.copper_layers[0] else (30 if number == self.copper_layers[-1] else None)
            if side and not (limit > 0 and drill < limit):
                frame = self._frame('Stop', size, size)
                opening = outline.buffer(frame, quad_segs=self._segs(frame), join_style=self._join(shape))
                self._add(side, opening)
                self._primitives.flash(side, opening, (x, y), {'round': 'circle', 'square': 'rect'}.get(shape, 'block'),
                                       size + 2 * frame, size + 2 * frame)

    def _smd(self, node, xf, mirror):
        x, y = self._attr(node, 'x'), self._attr(node, 'y')
        dx, dy = self._attr(node, 'dx'), self._attr(node, 'dy')
        if dx <= 0 or dy <= 0:
            raise EagleBrdParseError('SMD pad "%s" has a non-positive size' % node.get('name', ''))
        number = self._layer_no(node)
        if number not in (1, 16):
            raise EagleBrdParseError('SMD pad "%s" is on layer %d, not Top or Bottom' % (node.get('name', ''), number))
        if mirror:
            number = _MIRROR_LAYER[number]
        if number not in self.copper_layers:
            raise EagleBrdParseError('SMD pad on layer %d, which the board does not have' % number)
        roundness = self._attr(node, 'roundness', 0.0)
        if not 0 <= roundness <= 100:
            raise EagleBrdParseError('SMD pad "%s" has roundness %g outside 0-100' % (node.get('name', ''), roundness))
        smaller = min(dx, dy)
        radius = roundness / 100 * smaller / 2
        if roundness == 0 and (self._rules['srRoundness'] > 0 or self._rules['srMaxRoundness'] > 0):
            rule = self._rules['srRoundness'] / 100 * smaller / 2
            low, high = self._rules['srMinRoundness'], self._rules['srMaxRoundness']
            radius = self._clamp(rule, low, high) if high > 0 else rule
            self._warn('roundness', 'The SMD roundness design rule was applied to pads without their own '
                                    'roundness (rule semantics assumed)')
        _, pad_mirror, angle = self._rot(node.get('rot'))
        if pad_mirror:
            raise EagleBrdParseError('SMD pad "%s" has a mirrored rotation' % node.get('name', ''))
        place = self._compose(xf, self._matrix(x, y, angle, False))
        outline = self._rounded_box(dx, dy, radius)
        solid = self._apply(outline, place)
        self._add(number, solid)
        centre = self._apply_pt((0, 0), place)
        rotation = math.degrees(math.atan2(place[3], place[0]))
        native_shape = 'rect' if radius == 0 else 'oval' if radius == smaller / 2 else 'block'
        self._primitives.flash(number, solid, centre, native_shape, dx, dy, rotation)
        join = 'round' if radius > 0 else 'mitre'
        top = number == self.copper_layers[0]
        if node.get('stop', 'yes') != 'no':
            frame = self._frame('Stop', dx, dy)
            opening = outline.buffer(frame, quad_segs=self._segs(frame), join_style=join)
            opening = self._apply(opening, place)
            self._add(29 if top else 30, opening)
            self._primitives.flash(29 if top else 30, opening, centre, native_shape,
                                   dx + 2 * frame, dy + 2 * frame, rotation)
        if node.get('cream', 'yes') != 'no':
            frame = self._frame('Cream', dx, dy)
            paste = outline.buffer(-frame, quad_segs=self._segs(-frame), join_style=join) if frame else outline
            if not paste.is_empty:
                paste = self._apply(paste, place)
                self._add(31 if top else 32, paste)
                self._primitives.flash(31 if top else 32, paste, centre, native_shape,
                                       dx - 2 * frame, dy - 2 * frame, rotation)

    def _hole(self, node, xf):
        x, y = self._attr(node, 'x'), self._attr(node, 'y')
        drill = self._attr(node, 'drill')
        centre = self._apply_pt((x, y), xf)
        self._record_drill(centre, drill, False)
        self._holes.append((centre, drill))
        # A hole is open in both stop masks, by the stop frame beyond its drill.
        opening = self._disc(drill / 2 + self._frame('Stop', drill, drill), centre)
        self._add(29, opening)
        self._add(30, opening)

    # ------------------------------------------------------------------ drawing items

    def _item(self, node, xf, mirror, ctx, where):
        """Dispatch one item of <plain> or of a package placed with matrix xf."""
        tag = node.tag
        if tag == 'wire':
            self._wire(node, xf, mirror, where)
        elif tag == 'circle':
            self._circle(node, xf, mirror)
        elif tag == 'rectangle':
            self._rectangle(node, xf, mirror)
        elif tag == 'polygon':
            self._polygon(node, xf, mirror)
        elif tag == 'text':
            self._text_node(node, xf, mirror, ctx)
        elif tag == 'pad':
            self._pad(node, xf, mirror)
        elif tag == 'smd':
            self._smd(node, xf, mirror)
        elif tag == 'hole':
            self._hole(node, xf)
        elif tag == 'dimension':
            self._warn('dimension', 'Dimension annotations (measure lines) are not drawn')
        elif tag in ('description', 'note'):
            return
        elif tag in ('frame', 'spline'):
            number = node.get('layer')
            if tag == 'spline' and number is not None and number.isdigit() and self._is_copper(int(number)):
                raise EagleBrdParseError('Spline on a copper layer is not supported')
            self._warn('skip:' + tag, 'Items of type <%s> are not imported' % tag)
        else:
            self._warn('skip:' + tag, 'Unsupported item <%s> was skipped' % tag)

    def _layer_of(self, node, mirror):
        number = self._layer_no(node)
        return _MIRROR_LAYER.get(number, number) if mirror else number

    def _width(self, node, number, what):
        width = self._attr(node, 'width', 0.0)
        if width < 0:
            raise EagleBrdParseError('Negative width in <%s>' % what)
        if width == 0:
            if number in self.OUTLINE_LAYERS:
                self._warn('zero-outline', 'Zero-width Dimension/Milling lines were drawn with %.2f mm width'
                           % self.HAIRLINE)
                return self.HAIRLINE
            if self._is_copper(number) or number in (17, 18):
                self._warn('zero-copper', 'Zero-width lines on copper layers were skipped')
            elif number in (29, 30, 31, 32):
                self._warn('zero-mask', 'Zero-width lines on stop/cream layers were skipped')
        return width

    def _wire(self, node, xf, mirror, where):
        number = self._layer_of(node, mirror)
        p1 = (self._attr(node, 'x1'), self._attr(node, 'y1'))
        p2 = (self._attr(node, 'x2'), self._attr(node, 'y2'))
        curve = self._attr(node, 'curve', 0.0)
        width = self._width(node, number, 'wire')
        if p1 == p2 and curve:
            raise EagleBrdParseError('Arc with identical end points')
        points = [self._apply_pt(point, xf) for point in self._arc_points(p1, p2, curve)]
        follow = [LineString(points)] if len(set(points)) > 1 else None
        solid = self._stroke(points, width, flat=bool(curve) and node.get('cap', 'round') == 'flat')
        if solid.is_empty and not follow:
            return
        self._add(number, solid, follow, strict=where == 'signal')
        if not (curve and node.get('cap', 'round') == 'flat'):
            self._primitives.stroke(number, solid, follow[0] if follow else Point(points[0]), width)

    def _circle(self, node, xf, mirror):
        number = self._layer_of(node, mirror)
        centre = (self._attr(node, 'x'), self._attr(node, 'y'))
        radius = self._attr(node, 'radius')
        width = self._attr(node, 'width', 0.0)
        if radius <= 0 or width < 0:
            raise EagleBrdParseError('Circle with non-positive radius or negative width')
        centre = self._apply_pt(centre, xf)
        ring = LineString(self._disc(radius, centre).exterior.coords)
        if width == 0:
            solid = self._disc(radius, centre)             # width 0 draws a filled disc
        else:
            solid = self._disc(radius + width / 2, centre).difference(
                self._disc(max(radius - width / 2, 0), centre))
        self._add(number, solid, [ring])
        if width == 0:
            self._primitives.flash(number, solid, centre, 'circle', 2 * radius, 2 * radius)
        else:
            self._primitives.stroke(number, solid, ring, width)

    def _rectangle(self, node, xf, mirror):
        number = self._layer_of(node, mirror)
        x1, y1 = self._attr(node, 'x1'), self._attr(node, 'y1')
        x2, y2 = self._attr(node, 'x2'), self._attr(node, 'y2')
        _, _, angle = self._rot(node.get('rot'))
        shape = box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if angle:
            shape = self._apply(shape, self._compose(
                self._matrix((x1 + x2) / 2, (y1 + y2) / 2, angle, False),
                (1.0, 0.0, -(x1 + x2) / 2, 0.0, 1.0, -(y1 + y2) / 2)))
        shape = self._apply(shape, xf)
        self._add(number, shape, [LineString(list(shape.exterior.coords))] if not shape.is_empty else None)
        self._primitives.flash(number, shape, self._apply_pt(((x1 + x2) / 2, (y1 + y2) / 2), xf),
                               'block', abs(x2 - x1), abs(y2 - y1))

    def _polygon(self, node, xf, mirror):
        number = self._layer_of(node, mirror)
        pour = node.get('pour', 'solid')
        if pour == 'cutout':
            return          # Only removes pour copper; nothing is drawn
        width = self._attr(node, 'width', 0.0)
        vertices = node.findall('vertex')
        if len(vertices) < 3:
            raise EagleBrdParseError('Polygon with fewer than three vertices')
        points = []
        for index, vertex in enumerate(vertices):
            start = (self._attr(vertex, 'x'), self._attr(vertex, 'y'))
            following = vertices[(index + 1) % len(vertices)]
            end = (self._attr(following, 'x'), self._attr(following, 'y'))
            sampled = self._arc_points(start, end, self._attr(vertex, 'curve', 0.0))[:-1]
            points.extend(self._apply_pt(point, xf) for point in sampled)
        outline = points + [points[0]]
        try:
            fill = Polygon(points)
        except (ValueError, TypeError) as error:
            raise EagleBrdParseError('Invalid polygon vertices') from error
        fill = fill if fill.is_valid else fill.buffer(0)
        parts = [self._stroke(outline, width)] if width > 0 else []
        if pour == 'hatch':
            if self._is_copper(number) or number in (17, 18, 29, 30, 31, 32):
                raise EagleBrdParseError('Hatched polygon on layer %d is not supported' % number)
            self._warn('hatch', 'Hatched polygons are drawn as outline only')
        else:
            parts.append(fill)
        self._add(number, unary_union(parts) if parts else Polygon(), [LineString(outline)])

    # ------------------------------------------------------------------ text

    ALIGN = {'bottom-left': ('bottom', 'left'), 'bottom-center': ('bottom', 'center'),
             'bottom-right': ('bottom', 'right'), 'center-left': ('center', 'left'),
             'center': ('center', 'center'), 'center-right': ('center', 'right'),
             'top-left': ('top', 'left'), 'top-center': ('top', 'center'), 'top-right': ('top', 'right')}

    def _align_justify(self, align, flip):
        """Eagle alignment -> justify words for the stroke-text engine (flip = text turned by 180 degrees)."""
        if align not in self.ALIGN:
            raise EagleBrdParseError('Invalid text alignment %r' % align)
        row, col = self.ALIGN[align]
        if flip:
            row = {'top': 'bottom', 'bottom': 'top', 'center': 'center'}[row]
            col = {'left': 'right', 'right': 'left', 'center': 'center'}[col]
        return [word for word in (row, col) if word != 'center']

    def _text_node(self, node, xf, mirror, ctx):
        content = node.text or ''
        if ctx is not None:
            for tag, key in (('>NAME', 'name'), ('>VALUE', 'value')):
                if tag in content:
                    if key.upper() in ctx['smashed']:
                        return
                    content = content.replace(tag, ctx[key])
        if '>' in content and re.search(r'>[A-Za-z_]', content):
            self._warn('textvar', 'Text variables other than >NAME and >VALUE are not expanded')
        self._text(node, content, xf, mirror)

    def _attribute_text(self, attribute, ctx):
        """Text of a smashed element attribute (absolute position)."""
        name = attribute.get('name', '')
        display = attribute.get('display', 'value')
        if display == 'off' or attribute.get('x') is None:
            return          # Hidden, or a plain attribute value with no text position
        if name == 'NAME':
            content = ctx['name']
        elif name == 'VALUE':
            content = ctx['value']
        elif display == 'name':
            content = name
        elif display == 'both':
            content = '%s = %s' % (name, attribute.get('value', ''))
        else:
            content = attribute.get('value', '')
        self._text(attribute, content, _IDENTITY, False)

    def _text(self, node, content, xf, mirror):
        number = self._layer_of(node, mirror)
        if not content.strip() or number in self.SKIPPED_LAYERS:
            return
        size = self._attr(node, 'size')
        if size <= 0:
            raise EagleBrdParseError('Text with non-positive size')
        ratio = self._attr(node, 'ratio', self.DEFAULT_RATIO)
        spin, text_mirror, angle = self._rot(node.get('rot'))
        x, y = self._attr(node, 'x'), self._attr(node, 'y')
        local = self._matrix(x, y, angle, text_mirror)
        place = self._compose(xf, local)
        linear = place[0] * place[4] - place[1] * place[3]
        direction = math.degrees(math.atan2(place[3], place[0]))
        viewed = direction if linear > 0 else 180 - direction
        viewed %= 360
        flip = not spin and 90 + 1e-6 < viewed <= 270 + 1e-6
        if flip:
            place = self._compose(place, self._matrix(0, 0, 180, False))
        engine = self._text_engine
        height = size * self.CAP_RATIO / engine.CAP_RATIO
        justify = self._align_justify(node.get('align', 'bottom-left'), flip)
        solid, follows = engine._text_geometry(content, height, height, size * ratio / 100, justify, False)
        self._text_used = True
        if self._is_copper(number) or number in (29, 30, 31, 32):
            self._warn('text-copper', 'Text on copper, stop mask or cream layers is drawn with the approximate '
                                      'stroke font; check it before using those layers')
        if solid.is_empty:
            return
        self._add(number, self._apply(solid, place), [self._apply(line, place) for line in follows])
        if self._primitives.enabled:
            width = size * ratio / 100
            placed_solid = self._apply(solid, place)
            for line in follows:
                stroke = line.buffer(width / 2, quad_segs=engine._segs(width / 2))
                self._primitives.stroke(number, self._apply(stroke, place).intersection(placed_solid),
                                        self._apply(line, place), width)

    # ------------------------------------------------------------------ elements

    def _element(self, node):
        key = (node.get('library', ''), node.get('package', ''))
        package = self._packages.get(key)
        if package is None:
            raise EagleBrdParseError('Element "%s" refers to unknown package %s / %s' %
                                     (node.get('name', ''), key[0], key[1]))
        x, y = self._attr(node, 'x'), self._attr(node, 'y')
        _, mirror, angle = self._rot(node.get('rot'))
        xf = self._matrix(x, y, angle, mirror)
        attributes = [item for item in node.findall('attribute')]
        ctx = {'name': node.get('name', ''), 'value': node.get('value', ''),
               # A smashed element carries its own name/value texts; a missing one is hidden.
               'smashed': {'NAME', 'VALUE'} if node.get('smashed') == 'yes' else
                          {item.get('name', '').upper() for item in attributes
                           if item.get('name', '').upper() in ('NAME', 'VALUE')}}
        for item in package:
            self._item(item, xf, mirror, ctx, 'package')
        for item in attributes:
            self._attribute_text(item, ctx)

    # ------------------------------------------------------------------ result

    def _build_drills(self):
        for group in sorted(self._drills, key=lambda name: (name != 'PTH', name != 'NPTH', name)):
            tools = {}
            for number, key in enumerate(sorted(self._drills[group]), 1):
                tools[number] = self._drills[group][key]
            self.drill_groups[group] = tools

    def _holes_layer(self):
        for centre, drill in self._holes:
            ring = LineString(self._disc(drill / 2, centre).exterior.coords)
            thin = self._disc(drill / 2 + self.HAIRLINE / 2, centre).difference(
                self._disc(max(drill / 2 - self.HAIRLINE / 2, 0), centre))
            self._solids.setdefault(45, []).extend(self._polygons_of(thin))
            self._follows.setdefault(45, []).append(ring)

    def _outline(self):
        lines = [set_precision(line, 1e-5) for line in self._follows.get(20, [])]
        if not lines:
            return Polygon()
        faces = list(polygonize(unary_union(lines)))
        if not faces:
            self._warn('outline', 'Dimension layer does not form a closed outline; board outline left empty')
            return Polygon()
        kept = []
        for face in faces:
            probe = face.representative_point()
            if not any(other is not face and other.interiors and
                       any(Polygon(ring).contains(probe) for ring in other.interiors) for other in faces):
                kept.append(face)
        return unary_union(kept) if len(kept) > 1 else kept[0]

    def _finish(self):
        self._build_drills()
        self._holes_layer()
        self.board_outline = self._outline()
        ordered = list(self.copper_layers)
        ordered += sorted(number for number in self._solids if number not in ordered)
        for number in ordered:
            polygons = self._polygons_of(unary_union(self._solids.get(number, [])))
            if not polygons:
                continue
            # Enclosing polygons MUST precede islands, otherwise an enclosing hole erases the island.
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            self.layers[self._name_of(number)] = {
                'name': 'layer %d' % number,
                'solid_geometry': polygons,
                'follow_geometry': list(self._follows.get(number, [])),
                'tools': {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': polygon} for polygon in polygons]}}}
            self._primitives.finish(number, self.layers[self._name_of(number)])
        self.copper_layers = [self._name_of(number) for number in self.copper_layers]
        defaults = [self._name_of(number) for number in self.DEFAULT_TECH]
        self.default_layers = [name for name in self.layers if name in self.copper_layers or name in defaults]
        if self._text_used:
            self.warnings.append('Text is drawn with an approximate stroke font; glyph shapes and spacing '
                                 'differ from Eagle')
        if self._text_engine._unsupported_chars:
            self.warnings.append('Characters without a glyph were left blank: %s' %
                                 ''.join(sorted(self._text_engine._unsupported_chars)))
