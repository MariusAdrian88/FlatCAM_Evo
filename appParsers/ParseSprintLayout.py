# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native Sprint Layout 6 (.lay6) geometry reader           #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read Sprint Layout 6 (.lay6) manufacturing geometry without Sprint Layout.

The format is not officially documented. This reader was written from the
public reverse-engineering notes (field order and enumerations) and checked
against a real file and the Gerber/Excellon files Sprint Layout exported from
it. Nothing is copied from GPL code.

File (little-endian, Delphi packed records, lengths are byte counts):
    magic 06 33 AA FF, u32 board count, <board>..., trailer (ignored).
Board:
    header   shortstring[30] name (1 + 30 bytes), u32, u32 width, u32 height
             (units below), 7 ground-plane flags (C1 S1 C2 S2 I1 I2 O), view
             and scan settings, u8 multilayer, u32 object count
    objects  <object> * count
    pad data u32 n + n * u32 for every pad object (THT or SMD); ignored
Object (fixed part, 77 bytes):
    u8 type (2 THT pad, 4 polygon, 5 circle, 6 line, 7 text, 8 SMD pad),
    f32 x, f32 y, f32 outer radius, f32 inner (drill) radius, u32 line width,
    u8, u8 layer (1 C1, 2 S1, 3 C2, 4 S2, 5 I1, 6 I2, 7 O), u8 pad shape
    (1 round, 2 octagon, 3 square, 4..9 oval/rect H/V), u32, u16 component id,
    u8, 4 bytes style, 5 bytes, u8 style flag (text: 1 = shown), u32 plane
    clearance, u32, u8, u8 thermal, u8, u8 cut-out, u32, u8 plated, u8 mask
    open, 2 bytes, 16 bytes. Several of these slots hold uninitialised
    memory for object types that do not use them.
    Then (not for text children): u32 len + text, u32 len + marker, u32 n +
    n * u32 group ids; then, unless the type is circle: for text u32 n +
    n child objects (the glyph strokes, already laid out) and, when shape = 1,
    a component record; for all other types u32 n + n * (f32 x, f32 y).
Units: 0.1 micrometre (1e-4 mm). Y is stored negated and measured from the
board top: Y(mm, up) = board height + y. The Gerber/Excellon frame is the
board rectangle with its lower-left corner at (0, 0).

A pad polygon (square, octagon) is stored in the point list; a round pad has
a two-point list (its horizontal diameter). Copper layers with the automatic
ground plane enabled are not stored: the plane is the board rectangle minus
every object grown by its own clearance (clearance 0 means connected), plus
the objects themselves. The O layer is drawn on every copper layer (as in
the Sprint exporter). Solder mask is pad (and flagged item) copper grown by
MASK_EXPANSION. Anything that would change copper and cannot be represented
raises SprintLayoutParseError; skipped non-copper items are warned.
"""

import math
import struct

from shapely import LineString, Point, Polygon, box, set_precision
from shapely.ops import polygonize, unary_union

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.board_primitives import BoardPrimitives


class SprintLayoutParseError(ValueError):
    """Malformed data or copper geometry that cannot be represented safely."""


class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, count):
        if count < 0 or count > len(self.data) - self.pos:
            raise SprintLayoutParseError('Truncated file: record at byte %d runs past the end' % self.pos)
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def unpack(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return self.unpack('H')[0]

    def u32(self):
        return self.unpack('I')[0]

    def f32(self):
        return self.unpack('f')[0]

    def remaining(self):
        return len(self.data) - self.pos


class _Item:
    """One Sprint Layout object in file units (0.1 um), Y as stored."""
    __slots__ = ('type', 'x', 'y', 'rout', 'rin', 'width', 'layer', 'shape', 'comp', 'style', 'flag',
                 'gdist', 'thermal', 'cutoff', 'plated', 'mask', 'text', 'pts', 'children')


class SprintLayoutParser:
    MAGIC = b'\x06\x33\xaa\xff'
    UNIT = 1e-4                         # mm per file unit
    MAX_BOARDS = 1024
    MAX_POINTS = 1000000
    MAX_COORD = 1e9                     # file units (100 m); anything beyond is corrupt
    MAX_WIDTH = 1e7                     # file units (1 m)
    MASK_EXPANSION = 0.12               # mm per side, the Sprint exporter default

    T_THT, T_POLY, T_CIRCLE, T_LINE, T_TEXT, T_SMD = 2, 4, 5, 6, 7, 8
    TYPES = (T_THT, T_POLY, T_CIRCLE, T_LINE, T_TEXT, T_SMD)

    # item layer id -> layer key; plane flags use the same order starting at id 1
    LAYER_KEYS = {1: 'C1', 2: 'S1', 3: 'C2', 4: 'S2', 5: 'I1', 6: 'I2', 7: 'O'}
    COPPER = ('C1', 'C2', 'I1', 'I2')
    ORDER = ('C1', 'C2', 'I1', 'I2', 'M1', 'M2', 'S1', 'S2', 'O')
    NAMES = {'C1': 'Copper Top', 'C2': 'Copper Bottom', 'I1': 'Inner Copper 1', 'I2': 'Inner Copper 2',
             'M1': 'Solder Mask Top', 'M2': 'Solder Mask Bottom', 'S1': 'Silkscreen Top',
             'S2': 'Silkscreen Bottom', 'O': 'Board Outline'}
    DEFAULT = ('C1', 'C2', 'I1', 'I2', 'M1', 'M2', 'S1', 'S2', 'O')

    def __init__(self, app=None, circle_steps=None):
        # circle_steps: a fixed number of segments for every circle; None: the Gerber "Circle Steps" preference
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        self._reset()

    # ------------------------------------------------------------------ state

    def _reset(self):
        self._primitives = BoardPrimitives(self._options)
        self.layers = {}
        self.drill_groups = {}
        self.warnings = []
        self.board_outline = Polygon()
        self.default_layers = []
        self.board_name = ''
        self.board_size = (0.0, 0.0)
        self.board_count = 0
        self._height = 0.0
        self._warned = set()
        self._solids = {}
        self._follows = {}
        self._skipped = {}

    def _steps(self, radius):
        # Segments of a whole circle of this radius (millimetres): the fixed override or the Gerber preference.
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _segs(self, radius):
        return circle_quad_segs(self._steps(radius))

    def _warn(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(message)

    def _skip(self, key, message):
        """Count a skipped non-copper item; the summary warning is written by _finish."""
        entry = self._skipped.setdefault(key, [message, 0])
        entry[1] += 1

    # ------------------------------------------------------------------ entry points

    def parse_file(self, filename):
        try:
            with open(str(filename), 'rb') as handle:
                data = handle.read()
        except OSError as error:
            self._reset()
            raise SprintLayoutParseError('Cannot read Sprint Layout file: %s' % error) from error
        return self.parse_bytes(data)

    def parse_bytes(self, data):
        self._reset()
        try:
            self._parse(bytes(data))
        except SprintLayoutParseError:
            self._fail()
            raise
        except Exception as error:
            self._fail()
            raise SprintLayoutParseError('Cannot read Sprint Layout file: %s' % error) from error
        return self

    def _fail(self):
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []

    # ------------------------------------------------------------------ binary reading

    def _num(self, value, what, limit):
        if not math.isfinite(value) or abs(value) > limit:
            raise SprintLayoutParseError('Invalid %s: %r' % (what, value))
        return value

    def _read_item(self, reader, child=False):
        item = _Item()
        item.type = reader.u8()
        if item.type not in self.TYPES:
            raise SprintLayoutParseError('Unknown object type %d at byte %d' % (item.type, reader.pos - 1))
        item.x = self._num(reader.f32(), 'x coordinate', self.MAX_COORD)
        item.y = self._num(reader.f32(), 'y coordinate', self.MAX_COORD)
        item.rout = self._num(reader.f32(), 'outer radius', self.MAX_COORD)
        item.rin = self._num(reader.f32(), 'inner radius', self.MAX_COORD)
        item.width = reader.u32()
        reader.u8()
        item.layer = reader.u8()
        item.shape = reader.u8()
        reader.u32()
        item.comp = reader.u16()
        reader.u8()
        item.style = reader.take(4)
        reader.take(5)
        item.flag = reader.u8()
        item.gdist = reader.u32()
        reader.u32()
        reader.u8()
        item.thermal = reader.u8()
        reader.u8()
        item.cutoff = reader.u8()
        reader.u32()
        item.plated = reader.u8()
        item.mask = reader.u8()
        reader.take(18)
        item.text = b''
        item.pts = []
        item.children = []
        if not child:
            item.text = reader.take(reader.u32())
            reader.take(reader.u32())
            count = reader.u32()
            reader.take(count * 4)
        if item.type == self.T_CIRCLE:
            return item
        count = reader.u32()
        if item.type == self.T_TEXT:
            if count > self.MAX_POINTS or count > reader.remaining():
                raise SprintLayoutParseError('Invalid text stroke count %d' % count)
            item.children = [self._read_item(reader, True) for _ in range(count)]
            if item.shape == 1:
                reader.take(9)                      # offset x, y, centre mode
                reader.take(8)                      # rotation (double)
                reader.take(reader.u32())           # package
                reader.take(reader.u32())           # comment
                reader.take(1)                      # use
            return item
        if count > self.MAX_POINTS or count * 8 > reader.remaining():
            raise SprintLayoutParseError('Invalid point count %d at byte %d' % (count, reader.pos))
        for _ in range(count):
            x, y = reader.unpack('ff')
            item.pts.append((self._num(x, 'point x', self.MAX_COORD), self._num(y, 'point y', self.MAX_COORD)))
        return item

    def _parse(self, data):
        reader = _Reader(data)
        if reader.take(4) != self.MAGIC:
            raise SprintLayoutParseError('Not a Sprint Layout 6 file (bad signature)')
        self.board_count = reader.u32()
        if self.board_count < 1 or self.board_count > self.MAX_BOARDS:
            raise SprintLayoutParseError('Invalid board count %d' % self.board_count)
        boards = []
        for _ in range(self.board_count):
            boards.append(self._read_board(reader))
        # The trailer (active tab, project name/author/company, comment) is not needed.
        self._build(boards[0])
        if len(boards) > 1:
            self.warnings.append('The file holds %d boards; only the first board ("%s") was imported' %
                                 (len(boards), boards[0]['name']))

    def _read_board(self, reader):
        board = {}
        length = reader.u8()
        raw = reader.take(30)
        if length > 30:
            raise SprintLayoutParseError('Invalid board name length')
        board['name'] = raw[:length].decode('cp1252', errors='replace')
        reader.u32()
        board['w'] = reader.u32()
        board['h'] = reader.u32()
        if board['w'] <= 0 or board['h'] <= 0 or board['w'] > self.MAX_COORD or board['h'] > self.MAX_COORD:
            raise SprintLayoutParseError('Invalid board size %d x %d' % (board['w'], board['h']))
        board['plane'] = list(reader.take(7))
        reader.take(8 + 8 + 4 + 4)                  # grid, zoom, view offset
        reader.u8()                                 # active layer
        reader.take(3)
        board['visible'] = list(reader.take(7))
        reader.take(2 + 201 + 201)                  # scanned copy flags and paths
        reader.take(8 + 16 + 8 + 8)                 # dpi, shifts, reserved, centre
        board['multilayer'] = reader.u8() != 0
        count = reader.u32()
        if count > reader.remaining():
            raise SprintLayoutParseError('Invalid object count %d' % count)
        board['items'] = [self._read_item(reader) for _ in range(count)]
        for item in board['items']:
            if item.type in (self.T_THT, self.T_SMD):
                reader.take(reader.u32() * 4)       # pad connection list
        return board

    # ------------------------------------------------------------------ geometry helpers

    def _mm(self, x, y):
        """File coordinates -> mm in the Gerber frame (origin at the board's lower-left corner)."""
        return (x * self.UNIT, self._height + y * self.UNIT)

    def _stroke(self, points, width):
        """Round-capped stroke along a polyline (width in mm)."""
        if width <= 0:
            return Polygon()
        if all(point == points[0] for point in points):
            return Point(points[0]).buffer(width / 2, quad_segs=self._segs(width / 2))
        return LineString(points).buffer(width / 2, quad_segs=self._segs(width / 2))

    def _width(self, item):
        if item.width > self.MAX_WIDTH:
            raise SprintLayoutParseError('Invalid line width %d' % item.width)
        return item.width * self.UNIT

    @staticmethod
    def _polygons_of(geo):
        if geo.is_empty:
            return []
        if isinstance(geo, Polygon):
            return [geo]
        if geo.geom_type in ('MultiPolygon', 'GeometryCollection'):
            return [polygon for child in geo.geoms for polygon in SprintLayoutParser._polygons_of(child)]
        if geo.geom_type in ('LineString', 'MultiLineString', 'Point', 'MultiPoint', 'LinearRing'):
            return []
        raise SprintLayoutParseError('Non-polygon material geometry: %s' % geo.geom_type)

    def _polygon(self, points):
        if len(points) < 3:
            raise SprintLayoutParseError('Polygon has fewer than three vertices')
        try:
            geo = Polygon(points)
        except (ValueError, TypeError) as error:
            raise SprintLayoutParseError('Invalid polygon vertices') from error
        return geo if geo.is_valid else geo.buffer(0)

    def _pad_shape(self, item):
        """Copper outline of a pad in mm."""
        center = self._mm(item.x, item.y)
        if item.shape == 1:
            if item.rout <= 0:
                raise SprintLayoutParseError('Round pad with non-positive size')
            return Point(center).buffer(item.rout * self.UNIT, quad_segs=self._segs(item.rout * self.UNIT))
        if item.shape in (2, 3, 4, 5, 6, 7, 8, 9) and len(item.pts) >= 3:
            return self._polygon([self._mm(x, y) for x, y in item.pts])
        raise SprintLayoutParseError('Unsupported pad shape %d (%d outline points)' % (item.shape, len(item.pts)))

    def _clearance(self, item):
        return item.gdist * self.UNIT


    # ------------------------------------------------------------------ building

    PLANE_INDEX = {'C1': 0, 'C2': 2, 'I1': 4, 'I2': 5}

    def _add_copper(self, keys, shape, item, follow=(), mask_keys=()):
        """A copper object: dark shape, plane clearance, follow lines and mask openings."""
        for key in keys:
            if not shape.is_empty:
                self._dark[key].append(shape)
                if item.gdist:
                    self._clears[key].append(shape.buffer(self._clearance(item), quad_segs=self._segs(self._clearance(item))))
            self._follows.setdefault(key, []).extend(follow)
        if not shape.is_empty:
            for key in mask_keys:
                self._mask[key].append(shape.buffer(self.MASK_EXPANSION, quad_segs=self._segs(self.MASK_EXPANSION), join_style=2))

    def _place(self, key, shape, follow, item, mask=False):
        """Put a finished shape on the layer an object belongs to."""
        if key in self.COPPER:
            self._add_copper([key], shape, item, follow, [key] if mask and key in self._mask else ())
        elif key == 'O':
            self._o_lines.extend(follow)
            if not shape.is_empty:
                self._o_dark.append(shape)
                if item.gdist:
                    self._o_clear.append(shape.buffer(self._clearance(item), quad_segs=self._segs(self._clearance(item))))
        elif not shape.is_empty:
            self._silk[key].append((shape, list(follow)))

    def _build(self, board):
        self.board_name = board['name']
        self._height = board['h'] * self.UNIT
        width = board['w'] * self.UNIT
        self.board_size = (width, self._height)
        flags = board['plane']
        multilayer = board['multilayer']
        self._dark = {key: [] for key in self.COPPER}
        self._clears = {key: [] for key in self.COPPER}
        self._mask = {'C1': [], 'C2': []}
        self._silk = {'S1': [], 'S2': []}
        self._o_lines, self._o_dark, self._o_clear = [], [], []
        self._approx = set()
        drills = []
        tht_layers = ['C1', 'C2'] + (['I1', 'I2'] if multilayer else [])
        planes = {key: bool(flags[index]) and (multilayer or key in ('C1', 'C2'))
                  for key, index in self.PLANE_INDEX.items()}

        for item in board['items']:
            if item.type in (self.T_THT, self.T_SMD):
                if item.thermal and any(planes.values()):
                    raise SprintLayoutParseError('Thermal pad connections to the ground plane are not supported')
                shape = self._pad_shape(item)
                if item.type == self.T_THT:
                    keys, mask_keys = tht_layers, ['C1', 'C2']
                    if item.rin > 0:
                        drills.append((self._mm(item.x, item.y), round(2 * item.rin * self.UNIT, 4),
                                       bool(item.plated)))
                elif item.layer in (1, 3):
                    keys = mask_keys = [self.LAYER_KEYS[item.layer]]
                else:
                    raise SprintLayoutParseError('SMD pad on unsupported layer %d' % item.layer)
                self._add_copper(keys, shape, item, (), mask_keys if item.mask else ())
                if self._primitives.enabled:
                    minx, miny, maxx, maxy = shape.bounds
                    pad_width, pad_height = maxx - minx, maxy - miny
                    native_shape = 'circle' if item.shape == 1 else 'block'
                    centre = self._mm(item.x, item.y)
                    for key in keys:
                        self._primitives.flash(key, shape, centre, native_shape, pad_width, pad_height)
                    if item.mask:
                        opening = shape.buffer(self.MASK_EXPANSION,
                                               quad_segs=self._segs(self.MASK_EXPANSION), join_style=2)
                        for key in mask_keys:
                            self._primitives.flash('M' + key[1], opening, centre, native_shape,
                                                   pad_width + 2 * self.MASK_EXPANSION, pad_height + 2 * self.MASK_EXPANSION)
            else:
                self._object(item)

        board_box = box(0.0, 0.0, width, self._height)
        for key in self.COPPER:
            if key in ('I1', 'I2') and not multilayer and not self._dark[key]:
                continue                            # two-layer board: the inner layers do not exist
            dark = self._dark[key] + self._o_dark
            clears = self._clears[key] + self._o_clear
            if planes[key]:
                geo = unary_union([board_box.difference(unary_union(clears)) if clears else board_box] + dark)
            else:
                geo = unary_union(dark)
            self._solids[key] = [geo]
        for key, shapes in self._mask.items():
            self._solids['M' + key[1]] = shapes
        for key, entries in self._silk.items():
            self._solids[key] = [shape for shape, _ in entries]
            self._follows[key] = [line for _, lines in entries for line in lines]
        self._solids['O'] = list(self._o_dark)
        self._follows['O'] = list(self._o_lines)
        planed = [key for key in self.COPPER if planes[key]]
        if planed:
            self.warnings.append('The ground plane of %s is not stored in the file; it was recomputed from the '
                                 'board area, the object clearances and the direct pad connections' %
                                 ', '.join('%s (%s)' % (key, self.NAMES[key]) for key in planed))
        self._build_drills(drills)
        self._build_outline()
        if self._approx:
            self.warnings.append('Silkscreen %s are drawn approximately' % ' and '.join(sorted(self._approx)))
        self._finish()

    # -- per object kinds

    def _points(self, item):
        return [self._mm(x, y) for x, y in item.pts]

    def _object(self, item):
        layer = item.layer
        if layer not in self.LAYER_KEYS:
            if item.type == self.T_TEXT and (not item.flag or not item.children):
                return
            self._skip('layer', 'Items on layers other than copper, silkscreen and outline were skipped')
            return
        key = self.LAYER_KEYS[layer]
        if item.cutoff and key in self.COPPER:
            raise SprintLayoutParseError('Cut-out objects on copper layers are not supported')
        if item.type == self.T_LINE:
            self._line(item, key, bool(item.mask))
        elif item.type == self.T_TEXT:
            if not item.flag:
                return                              # hidden component label
            for child in item.children:
                if child.type != self.T_LINE:
                    raise SprintLayoutParseError('Unsupported text stroke element (type %d)' % child.type)
                self._line(child, key, False)
        elif item.type == self.T_POLY:
            self._poly(item, key)
        else:
            self._circle(item, key)

    def _line(self, item, key, mask):
        points = self._points(item)
        if len(points) < 2:
            raise SprintLayoutParseError('Line with fewer than two points')
        width = self._width(item)
        if width <= 0:
            self._skip('zero', 'Zero-width lines were skipped')
        follow = [LineString(points)] if any(point != points[0] for point in points) else []
        shape = self._stroke(points, width)
        self._place(key, shape, follow, item, mask)
        centre_line = follow[0] if follow else Point(points[0])
        self._primitives.stroke(key, shape, centre_line, width)
        if self._primitives.enabled and mask and key in ('C1', 'C2'):
            opening = shape.buffer(self.MASK_EXPANSION,
                                   quad_segs=self._segs(self.MASK_EXPANSION), join_style=2)
            self._primitives.stroke('M' + key[1], opening, centre_line, width + 2 * self.MASK_EXPANSION)

    def _poly(self, item, key):
        points = self._points(item)
        if len(points) < 3:
            raise SprintLayoutParseError('Polygon with fewer than three points')
        width = self._width(item)
        shape = self._polygon(points)
        if width > 0:
            shape = unary_union([shape, self._stroke(points + [points[0]], width)])
        if key in self.COPPER:
            self._warn('polyfill', 'Polygons on copper layers are drawn filled solid; other fill styles are '
                                   'not decoded')
        elif key != 'O':
            self._approx.add('polygons')
        self._place(key, shape, [LineString(points + [points[0]])], item, bool(item.mask))

    def _circle(self, item, key):
        ring = item.shape == 1 and item.rout > item.rin >= 0
        if not ring:
            if key in self.COPPER or key == 'O':
                raise SprintLayoutParseError('Arcs and zero-width circles on copper or outline layers are '
                                             'not supported')
            self._skip('arc', 'Silkscreen arcs and zero-width circles were skipped')
            return
        center = self._mm(item.x, item.y)
        shape = Point(center).buffer(item.rout * self.UNIT, quad_segs=self._segs(item.rout * self.UNIT))
        if item.rin > 0:
            shape = shape.difference(Point(center).buffer(item.rin * self.UNIT, quad_segs=self._segs(item.rin * self.UNIT)))
        if key not in self.COPPER and key != 'O':
            self._approx.add('circles')
        self._place(key, shape, [], item, bool(item.mask))
        if item.rin == 0:
            size = 2 * item.rout * self.UNIT
            self._primitives.flash(key, shape, center, 'circle', size, size)
        else:
            radius = (item.rout + item.rin) * self.UNIT / 2
            follow = LineString(Point(center).buffer(radius, quad_segs=self._segs(radius)).exterior.coords)
            self._primitives.stroke(key, shape, follow, (item.rout - item.rin) * self.UNIT)
        if item.mask and key in ('C1', 'C2'):
            opening = shape.buffer(self.MASK_EXPANSION,
                                   quad_segs=self._segs(self.MASK_EXPANSION), join_style=2)
            self._primitives.flash('M' + key[1], opening, center, 'block',
                                   2 * item.rout * self.UNIT + 2 * self.MASK_EXPANSION,
                                   2 * item.rout * self.UNIT + 2 * self.MASK_EXPANSION)

    # -- drills, outline, finishing

    def _build_drills(self, drills):
        groups = {}
        for position, diameter, plated in drills:
            tools = groups.setdefault('PTH' if plated else 'NPTH', {})
            tools.setdefault(diameter, []).append(Point(position))
        for group in ('PTH', 'NPTH'):
            if group in groups:
                self.drill_groups[group] = {
                    number: {'tooldia': diameter, 'drills': points, 'slots': []}
                    for number, (diameter, points) in enumerate(sorted(groups[group].items()), 1)}

    def _build_outline(self):
        faces = []
        if self._o_lines:
            faces = list(polygonize(unary_union([set_precision(line, 1e-5) for line in self._o_lines])))
        if not faces:
            if self._o_lines:
                self._warn('outline', 'The outline layer does not form a closed shape; the board rectangle is used')
            self.board_outline = box(0.0, 0.0, *self.board_size)
            return
        kept = []
        for face in faces:
            probe = face.representative_point()
            if not any(other is not face and other.interiors and
                       any(Polygon(ring).contains(probe) for ring in other.interiors) for other in faces):
                kept.append(face)
        self.board_outline = unary_union(kept) if len(kept) > 1 else kept[0]

    def _finish(self):
        for key in self.ORDER:
            polygons = self._polygons_of(unary_union(self._solids.get(key, [])))
            if not polygons:
                continue
            # Enclosing polygons MUST precede islands, otherwise an enclosing hole erases the island.
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            self.layers[key] = {
                'name': self.NAMES[key],
                'solid_geometry': polygons,
                'follow_geometry': list(self._follows.get(key, [])),
                'tools': {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': polygon} for polygon in polygons]}}}
            self._primitives.finish(key, self.layers[key])
        self.default_layers = [key for key in self.DEFAULT if key in self.layers]
        for message, count in self._skipped.values():
            self.warnings.append('%s (%d)' % (message, count))
