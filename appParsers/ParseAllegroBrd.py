# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native OrCAD PCB / Cadence Allegro board (.brd) reader    #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read the manufacturing geometry of a saved Cadence Allegro / OrCAD PCB Editor board (.brd) without Allegro.

The format is binary and has no public specification. The layout used here was worked out from two sample
boards and the Gerber output Allegro made from them (a 17.2 two-layer board and a six-layer board saved by
Allegro 24.1 with the 0x00150200 format), with the public KiCad developer notes on the format as a guide. Block
sizes, field offsets and the class/subclass codes were also cross-checked against the layout facts of KiCad's
Allegro importer (facts only, no code); every field read here was compared with the Gerber output of the same
board. Only two layouts were checked this way (17.2 and the 0x00150200 format of 24.1); they are read without
a remark. The other 17.x and 18.x/19.0 releases are tried with the layout the KiCad notes give for them, but
they were never compared with a Gerber: they are read only if the whole file walks cleanly and every
coordinate and size is sane (strict gate), and the result carries a warning. 16.x and older, and unknown
formats, are refused by name.

Output is in millimetres, Y up, in the board's own coordinates (the frame of Allegro's photoplot output).
Copper is built from tracks, stored shape fills (with their voids), pads and vias. Anything that would
change copper and cannot be represented safely (shapes that were never filled, unsupported pad shapes,
damaged data) raises AllegroBrdParseError.

File layout, as far as it is used:

* header: magic (release), file type (1 = board), file size, string count, board unit and divisor. The layer
  map at 0x430 names the list of copper layers. Offsets differ between the two layouts.
* string table from 0x1200: (id, NUL terminated text padded to 4 bytes)
* blocks until the end of the file: a type byte, three more header bytes, then 32-bit words. Most blocks
  have a fixed size per release; a few (padstack, texts, tables, ...) derive their size from fields. The
  walk over all blocks ends exactly at the end of the file, which proves the sizes.
* blocks are linked by 32-bit keys; chains of segments end at the key of their owner. Coordinates are 32-bit
  integers in board units / divisor; arc centres and radii are doubles stored as two swapped 32-bit words.
* pads sit in footprint-local coordinates, everything else is in board coordinates. A footprint on the bottom is
  mirrored left to right and then turned by its angle.
"""

import math
import struct

from shapely import LineString, MultiLineString, Point, Polygon, box
from shapely.affinity import affine_transform
from shapely.ops import polygonize, unary_union

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.ParseKiCadPcb import KiCadPcbParser


class AllegroBrdParseError(ValueError):
    """Malformed data, an unsupported release or copper geometry that cannot be represented safely."""


_U32 = struct.Struct('<I')
_I32 = struct.Struct('<i')
_U16 = struct.Struct('<H')
_F32PAIR = struct.Struct('<II')

# Releases whose layout was checked against sample boards: magic (low byte masked) -> (name, layout number).
_SUPPORTED = {0x00140400: ('17.2', 172), 0x00150200: ('18.1/24.1', 181)}
# Known magic numbers of other releases, used to name them.
_KNOWN_RELEASES = {
    0x00130000: '16.0', 0x00130400: '16.2', 0x00130500: '16.2', 0x00130C00: '16.4', 0x00131000: '16.5',
    0x00131500: '16.6', 0x00140900: '17.4', 0x00141500: '17.5', 0x00150000: '18.0', 0x00160100: '19.0'}
# Largest sizes a board may have (mm) for the strict gate of releases that were not checked
_MAX_BOARD = 2000.0
_MAX_PADSTACK = 100.0
_GATE_MARGIN = 1.0


def _layout_for(magic):
    """(name, layout number, checked) of a magic number (low byte masked); None when the release is refused.

    Layout numbers follow the version gates of the KiCad notes: 172 (the first of the 17.x family), 174, 175, 180,
    181 and 190. A magic between two known releases gets the layout of the nearest known release below it.
    """
    if magic in _SUPPORTED:
        return _SUPPORTED[magic] + (True,)
    low = magic & 0xFFFF
    family = magic >> 16
    name = _KNOWN_RELEASES.get(magic)
    if family == 0x0014 and low > 0x0400:
        layout = 175 if low >= 0x1500 else 174 if low >= 0x0900 else 172
        return (name or '17.x (format 0x%08X)' % magic), layout, False
    if family == 0x0015 and low != 0x0200:
        return (name or '18.x (format 0x%08X)' % magic), 180 if low < 0x0200 else 181, False
    if magic == 0x00160100:
        return '19.0', 190, False
    return None

# Board unit code -> millimetres per (coordinate / divisor). Only the units seen in sample boards.
_UNIT_MM = {1: 0.0254, 4: 10.0}

_STRINGS_AT = 0x1200
_LAYER_MAP_AT = 0x430
_LAYER_MAP_ENTRIES = 25

# Class codes
C_BOARD, C_FORMAT, C_ETCH, C_MFR, C_PKG, C_REFDES, C_BOUNDARY = 0x01, 0x04, 0x06, 0x07, 0x09, 0x0D, 0x15

# Fixed subclass codes (>= 0xEA) of each class that end up in an output layer.
_OUTLINE = ((C_BOARD, 0xEA), (C_BOARD, 0xFD), (C_FORMAT, 0xFD))
_SUBCLASSES = {
    'SOLDERMASK_TOP': ((C_PKG, 0xF4), (C_BOARD, 0xEE)),
    'SOLDERMASK_BOTTOM': ((C_PKG, 0xF3), (C_BOARD, 0xED)),
    'PASTEMASK_TOP': ((C_PKG, 0xED),),
    'PASTEMASK_BOTTOM': ((C_PKG, 0xEC),),
    'SILKSCREEN_TOP': ((C_PKG, 0xF7), (C_BOARD, 0xF1), (C_REFDES, 0xFB), (C_MFR, 0xF4)),
    'SILKSCREEN_BOTTOM': ((C_PKG, 0xF6), (C_BOARD, 0xF0), (C_REFDES, 0xFA), (C_MFR, 0xF3)),
    'ASSEMBLY_TOP': ((C_PKG, 0xFD), (C_REFDES, 0xFD)),
    'ASSEMBLY_BOTTOM': ((C_PKG, 0xFC), (C_REFDES, 0xFC)),
    'OUTLINE': _OUTLINE,
}
_NONCOPPER_ORDER = ['SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM', 'PASTEMASK_TOP', 'PASTEMASK_BOTTOM',
                    'SILKSCREEN_TOP', 'SILKSCREEN_BOTTOM', 'ASSEMBLY_TOP', 'ASSEMBLY_BOTTOM', 'OUTLINE']

# Padstack component table: 21 fixed entries, then four entries per copper layer.
_FIXED_COMPS = 21
_PER_LAYER = 4
_SLOT_PAD = 2                   # per layer: 0 antipad, 1 thermal relief, 2 pad, 3 unused
_COMP_SOLDERMASK = (14, 15)     # top, bottom
_COMP_PASTEMASK = (16, 17)

# Padstack shape codes
SH_NULL, SH_CIRCLE, SH_OCTAGON, SH_SQUARE, SH_RECT = 0x00, 0x02, 0x03, 0x05, 0x06
SH_OBLONG_X, SH_OBLONG_Y, SH_ROUND_RECT, SH_CHAMFER_RECT = 0x0B, 0x0C, 0x1B, 0x1C
_SHAPE_NAMES = {0x04: 'cross', 0x07: 'diamond', 0x0F: 'hexagon X', 0x10: 'hexagon Y', 0x12: 'triangle',
                0x16: 'shape symbol', 0x17: 'flash', 0x19: 'donut', 0x1E: 'N-sided polygon'}


class AllegroBrdParser:
    HAIRLINE = 0.01                 # width of zero-width outline lines
    MAX_BLOCKS_PER_BYTE = 4         # a block is at least 4 bytes
    # Allegro's vector font is proprietary: glyph shapes come from the KiCad reader's stroke font.
    CAP_TO_HEIGHT = 1.0             # cap height / Allegro character height

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
        self.layers = {}
        self.drill_groups = {}
        self.warnings = []
        self.board_outline = Polygon()
        self.default_layers = []
        self.version = ''
        self.copper_layers = []
        self._d = b''
        self._ver = 0
        self._scale = 0.0
        self._strings = {}
        self._blocks = []
        self._index = {}
        self._by_type = {}
        self._warned = set()
        self._solids = {}
        self._follows = {}
        self._drills = {}
        self._padstacks = {}
        self._text_used = False
        self._etch_names = []
        self._routed = set()
        self._untested = False

    def _warn(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(message)

    def _steps(self, radius):
        # Segments of a whole circle of this radius (millimetres): the fixed override or the Gerber preference.
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _segs(self, radius):
        return circle_quad_segs(self._steps(radius))

    # ------------------------------------------------------------------ entry points

    def parse_file(self, filename):
        try:
            with open(str(filename), 'rb') as handle:
                data = handle.read()
        except OSError as error:
            self._reset()
            raise AllegroBrdParseError('Cannot read Allegro board file: %s' % error) from error
        return self.parse_bytes(data)

    def parse_bytes(self, data):
        self._reset()
        try:
            if not isinstance(data, (bytes, bytearray)):
                raise AllegroBrdParseError('parse_bytes needs bytes')
            self._d = bytes(data)
            self._parse()
        except AllegroBrdParseError as error:
            self._fail()
            if self._untested:
                raise self._untested_error(str(error)) from error
            raise
        except Exception as error:
            self._fail()
            if self._untested:
                raise self._untested_error(str(error)) from error
            raise AllegroBrdParseError('Cannot read Allegro board: %s' % error) from error
        finally:
            self._d = b''
            self._blocks = []
            self._index = {}
            self._by_type = {}
        return self

    def _untested_error(self, reason):
        return AllegroBrdParseError('Allegro %s is not tested and this board could not be read safely: %s' %
                                    (self.version, reason))

    def _fail(self):
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []
        self.board_outline = Polygon()

    # ------------------------------------------------------------------ raw reads

    def _u32(self, offset):
        if offset < 0:
            raise AllegroBrdParseError('Read before the start of the data')
        try:
            return _U32.unpack_from(self._d, offset)[0]
        except struct.error:
            raise AllegroBrdParseError('Truncated data at offset 0x%X' % offset) from None

    def _i32(self, offset):
        if offset < 0:
            raise AllegroBrdParseError('Read before the start of the data')
        try:
            return _I32.unpack_from(self._d, offset)[0]
        except struct.error:
            raise AllegroBrdParseError('Truncated data at offset 0x%X' % offset) from None

    def _u16(self, offset):
        if offset < 0:
            raise AllegroBrdParseError('Read before the start of the data')
        try:
            return _U16.unpack_from(self._d, offset)[0]
        except struct.error:
            raise AllegroBrdParseError('Truncated data at offset 0x%X' % offset) from None

    def _u8(self, offset):
        if offset < 0 or offset >= len(self._d):
            raise AllegroBrdParseError('Truncated data at offset 0x%X' % offset)
        return self._d[offset]

    def _f64(self, offset):
        """Double stored as two little-endian 32-bit words, the high word first."""
        if offset < 0:
            raise AllegroBrdParseError('Read before the start of the data')
        try:
            high, low = _F32PAIR.unpack_from(self._d, offset)
        except struct.error:
            raise AllegroBrdParseError('Truncated data at offset 0x%X' % offset) from None
        value = struct.unpack('<d', _F32PAIR.pack(low, high))[0]
        if not math.isfinite(value) or abs(value) > 1e10:
            raise AllegroBrdParseError('Invalid number at offset 0x%X' % offset)
        return value

    def _mm(self, raw):
        return raw * self._scale

    # ------------------------------------------------------------------ header and strings

    def _header(self):
        data = self._d
        if len(data) < 4:
            raise AllegroBrdParseError('Not an Allegro board: the file is empty or too short')
        if data.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<'):
            raise AllegroBrdParseError('Not an Allegro board: this is an XML file (Eagle board?)')
        magic = self._u32(0) & 0xFFFFFF00
        layout = _layout_for(magic)
        if layout is None:
            known = _KNOWN_RELEASES.get(magic)
            if known or (magic >> 16) == 0x0013 or magic < 0x00140400 and (magic >> 16) == 0x0014:
                raise AllegroBrdParseError(
                    'Allegro %s boards are not supported (magic 0x%08X): releases before 17.2 store padstacks, '
                    'drills and many blocks differently. Open and save the board in Allegro 17.2 or newer first' % (
                        known or '16.x/17.0-17.1', self._u32(0)))
            raise AllegroBrdParseError('Not an Allegro board or an unknown release (magic 0x%08X)' % self._u32(0))
        self.version, self._ver, checked = layout
        role = self._u32(8) if len(data) >= 12 else 0
        if role != 1:
            raise AllegroBrdParseError(
                'This Allegro file is not a board (file type %d: %s); open a .brd board file' % (
                    role, 'a footprint (.dra) or pad file' if role == 2 else 'a symbol or library file'))
        self._untested = not checked
        if len(data) < _STRINGS_AT + 8:
            raise AllegroBrdParseError('Truncated Allegro board: the header is incomplete')
        if self._u32(0x20) != len(data):
            raise AllegroBrdParseError('The file size (%d) does not match the size stored in the header (%d): '
                                       'the file is truncated or damaged' % (len(data), self._u32(0x20)))
        if self._ver < 180:
            # 17.x: the header of the checked 17.2 layout (the KiCad notes give no header change before 18)
            self._string_count_at, self._end27_at, self._unit_at, self._div_at = 0x194, 0x18C, 0x180, 0x26C
        else:
            # 18.x: 18.1 and later have eight more header words before the version string than 18.0
            shift = 0x20 if self._ver >= 181 else 0
            self._string_count_at, self._end27_at = 0x34, 0x28
            self._unit_at, self._div_at = 0x18C + shift, 0x26C + shift
        unit = self._u8(self._unit_at)
        if unit not in _UNIT_MM:
            raise AllegroBrdParseError('Board unit code %d is not supported (only mils and centimetres were '
                                       'checked against sample boards)' % unit)
        divisor = self._u32(self._div_at)
        if divisor <= 0 or divisor > 10 ** 7:
            raise AllegroBrdParseError('Invalid unit divisor %d' % divisor)
        self._scale = _UNIT_MM[unit] / divisor

    def _read_strings(self):
        data = self._d
        count = self._u32(self._string_count_at)
        if count > len(data) // 8:
            raise AllegroBrdParseError('Invalid string count %d' % count)
        offset = _STRINGS_AT
        for _ in range(count):
            string_id = self._u32(offset)
            end = data.find(b'\0', offset + 4)
            if end < 0:
                raise AllegroBrdParseError('Unterminated string in the string table')
            self._strings[string_id] = data[offset + 4:end].decode('utf-8', 'replace')
            offset += 4 + (end - offset - 4 + 1 + 3) // 4 * 4
        return offset

    def _string(self, string_id):
        return self._strings.get(string_id, '')

    # ------------------------------------------------------------------ block scan

    # Fixed block sizes (bytes, with the 4-byte block header) per layout number.
    @staticmethod
    def _fixed_sizes(ver):
        newer = ver >= 174
        # 0x30 (text): the extra 4 bytes the checked 18.1 has beyond the 17.4 layout are put at 18.0, a guess
        sizes = {0x01: 84, 0x04: 20 + 4 * newer, 0x05: 68, 0x06: 40, 0x07: 48, 0x08: 32, 0x09: 48 + 4 * newer,
                 0x0A: 72 + 4 * newer, 0x0C: 64 + (4 if ver >= 174 else 0), 0x0D: 44 + 4 * newer, 0x0E: 68,
                 0x0F: 60 + 4 * newer + 4 * (ver >= 190), 0x10: 36 + 4 * newer, 0x11: 24 + 4 * newer,
                 0x12: 28 + 4 * newer,
                 0x14: 36, 0x15: 44, 0x16: 44, 0x17: 44, 0x1B: 60, 0x20: 40 + 40 * newer, 0x22: 44,
                 0x23: 84 + 4 * newer, 0x24: 56, 0x26: 24 + 4 * newer, 0x28: 76, 0x29: 56, 0x2B: 76, 0x2C: 44,
                 0x2D: 72, 0x2E: 40, 0x2F: 32, 0x30: 56 + 4 * newer + 4 * (ver >= 180), 0x32: 84, 0x33: 80,
                 0x34: 36, 0x35: 124, 0x37: 428 + 4 * newer, 0x38: 52 + 4 * newer, 0x39: 60,
                 0x3A: 16 + 4 * newer}
        return sizes

    _TABLE_ITEM = {0x02: 108, 0x06: 8, 0x0B: 1016, 0x0C: 232, 0x0D: 200, 0x0F: 20, 0x12: 1052}

    def _field_size(self, o):
        """Size of a 0x03 field block: 24 bytes plus a payload chosen by its sub type."""
        sub = self._u8(o + 16)
        size = self._u16(o + 18)
        if sub == 0x65:
            payload = 0
        elif sub in (0x64, 0x66, 0x67, 0x6A):
            payload = 4
        elif sub == 0x69:
            payload = 8
        elif sub in (0x68, 0x6B, 0x6D, 0x6E, 0x6F, 0x71, 0x73, 0x78):
            payload = (size + 3) // 4 * 4
        elif sub == 0x6C:
            payload = 4 + 4 * self._u32(o + 24)
        elif sub in (0x70, 0x74):
            payload = 4 + (self._u16(o + 26) + 4 * self._u16(o + 24) + 3) // 4 * 4
        elif size in (4, 8):
            payload = size
        else:
            raise AllegroBrdParseError('Unknown field block sub type 0x%02X at offset 0x%X' % (sub, o))
        return 24 + payload

    def _block_size(self, t, o, fixed, end27):
        ver = self._ver
        newer = ver >= 174
        if t in fixed:
            return fixed[t]
        if t == 0x1C:
            count = self._u8(o + 2)
            layers = self._u16(o + 44)
            if not 1 <= layers <= 64:
                raise AllegroBrdParseError('Padstack with %d layers at offset 0x%X' % (layers, o))
            return 192 + count * 40 + (32 if ver >= 180 else 0) + (_FIXED_COMPS + _PER_LAYER * layers) * 36
        if t == 0x21:
            return self._u32(o + 4)
        if t == 0x1D:
            return 28 + self._u16(o + 20) * 256 + self._u16(o + 22) * 56
        if t == 0x1F:
            return 28 + (384 if ver >= 175 else 280) * self._u16(o + 26) + 8
        if t == 0x03:
            return self._field_size(o)
        if t == 0x2A:
            return 8 + 4 * newer + 12 * self._u16(o + 2)
        if t == 0x31:
            return 24 + 4 * newer + (self._u16(o + 22) + 3) // 4 * 4
        if t == 0x27:
            return end27 - 1 - o
        if t == 0x3B:
            return 180 + (self._u32(o + 4) + 3) // 4 * 4
        if t == 0x36:
            code = self._u16(o + 2)
            items = self._u32(o + 16)
            if code == 0x03:
                item = 64 + 4 * newer
            elif code == 0x05:
                item = 28 + 4 * (ver >= 175)
            elif code == 0x08:
                item = 64 + (4 if 174 <= ver < 190 else 0)
            elif code == 0x10:
                item = 108 + 4 * (ver >= 180)
            elif code in self._TABLE_ITEM:
                item = self._TABLE_ITEM[code]
            else:
                raise AllegroBrdParseError('Unknown definition table 0x%02X at offset 0x%X' % (code, o))
            return 32 + 4 * newer + items * item
        if t == 0x3C:
            return 16 + 4 * newer + 4 * self._u32(o + 12 + 4 * newer)
        raise AllegroBrdParseError('Unsupported block type 0x%02X at offset 0x%X' % (t, o))

    def _scan(self, offset):
        data = self._d
        total = len(data)
        fixed = self._fixed_sizes(self._ver)
        end27 = self._u32(self._end27_at)
        limit = total // self.MAX_BLOCKS_PER_BYTE + 1
        keyless = (0x27, 0x3B, 0x21)
        while offset < total:
            t = data[offset]
            if t == 0:
                if any(data[offset:]):
                    raise AllegroBrdParseError('Unexpected data after the end of the block stream at 0x%X' % offset)
                break
            size = self._block_size(t, offset, fixed, end27)
            if size < 4 or size % 4 or offset + size > total:
                raise AllegroBrdParseError('Block 0x%02X at offset 0x%X has an invalid size %d' % (t, offset, size))
            if len(self._blocks) >= limit:
                raise AllegroBrdParseError('Too many blocks')
            entry = (t, offset, size)
            self._blocks.append(entry)
            self._by_type.setdefault(t, []).append(entry)
            if t == 0x2A:
                self._index.setdefault(self._u32(offset + size - 4), entry)
            elif t == 0x21:
                self._index.setdefault(self._u32(offset + 8), entry)
            elif t not in keyless:
                self._index.setdefault(self._u32(offset + 4), entry)
            offset += size
        else:
            if offset != total:
                raise AllegroBrdParseError('The block stream does not end at the end of the file')

    def _blk(self, key, *types):
        entry = self._index.get(key)
        if entry is None or (types and entry[0] not in types):
            return None
        return entry

    def _need(self, key, *types):
        entry = self._blk(key, *types)
        if entry is None:
            raise AllegroBrdParseError('Reference to a missing block (key 0x%X)' % key)
        return entry

    # ------------------------------------------------------------------ layers

    def _read_layers(self):
        refs = {}
        for i in range(_LAYER_MAP_ENTRIES):
            key = self._u32(_LAYER_MAP_AT + 8 * i + 4)
            entry = self._blk(key, 0x2A)
            if entry is not None and self._u16(entry[1] + 2) >= 2:
                refs[key] = refs.get(key, 0) + 1
        if not refs:
            raise AllegroBrdParseError('The board has no copper layer list')
        best = max(refs.values())
        winners = [key for key, count in refs.items() if count == best]
        if len(winners) != 1:
            raise AllegroBrdParseError('Cannot tell which layer list holds the copper layers')
        _t, o, _n = self._blk(winners[0])
        newer = self._ver >= 174
        count = self._u16(o + 2)
        if not 2 <= count <= 64:
            raise AllegroBrdParseError('Invalid copper layer count %d' % count)
        names = []
        for i in range(count):
            name = self._string(self._u32(o + 4 + 4 * newer + 12 * i))
            if not name:
                raise AllegroBrdParseError('Copper layer %d has no name' % (i + 1))
            names.append(name)
        if len(set(names)) != len(names):
            raise AllegroBrdParseError('Duplicate copper layer names')
        self._etch_names = names
        self.copper_layers = list(names)

    # ------------------------------------------------------------------ geometry helpers

    def _disc(self, radius, centre=(0.0, 0.0)):
        return Point(centre).buffer(radius, quad_segs=self._segs(radius))

    def _stroke(self, points, width, flat=False):
        if width <= 0:
            return Polygon()
        if all(p == points[0] for p in points):
            return Point(points[0]).buffer(width / 2, quad_segs=self._segs(width / 2))
        return LineString(points).buffer(width / 2, quad_segs=self._segs(width / 2), cap_style='flat' if flat else 'round')

    @staticmethod
    def _polygons_of(geo):
        if geo.is_empty:
            return []
        if isinstance(geo, Polygon):
            return [geo]
        if geo.geom_type in ('MultiPolygon', 'GeometryCollection'):
            return [p for child in geo.geoms for p in AllegroBrdParser._polygons_of(child)]
        return []

    def _arc_points(self, start, end, centre, clockwise):
        radius = math.hypot(start[0] - centre[0], start[1] - centre[1])
        radius_end = math.hypot(end[0] - centre[0], end[1] - centre[1])
        # the end points are whole board units, the centre is not: allow the rounding of both
        if radius <= 0 or abs(radius - radius_end) > 3 * self._scale + 0.002 * radius:
            raise AllegroBrdParseError('Inconsistent arc: the end points are not on one circle')
        first = math.atan2(start[1] - centre[1], start[0] - centre[0])
        last = math.atan2(end[1] - centre[1], end[0] - centre[0])
        if math.hypot(start[0] - end[0], start[1] - end[1]) < 1e-9:
            sweep = -2 * math.pi if clockwise else 2 * math.pi
        else:
            sweep = last - first
            if clockwise:
                while sweep >= 0:
                    sweep -= 2 * math.pi
            else:
                while sweep <= 0:
                    sweep += 2 * math.pi
        steps = max(2, math.ceil(self._steps(radius) * abs(sweep) / (2 * math.pi)))
        points = [start]
        for i in range(1, steps):
            angle = first + sweep * i / steps
            points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
        points.append(end)
        return points

    def _segment(self, key):
        """One line or arc of a segment chain -> (width, [points], next key, start, end)."""
        entry = self._need(key, 0x01, 0x15, 0x16, 0x17)
        t, o, _n = entry
        width = self._mm(self._i32(o + 24))
        if width < 0:
            raise AllegroBrdParseError('Negative line width')
        start = (self._mm(self._i32(o + 28)), self._mm(self._i32(o + 32)))
        end = (self._mm(self._i32(o + 36)), self._mm(self._i32(o + 40)))
        if t == 0x01:
            centre = (self._mm(self._f64(o + 44)), self._mm(self._f64(o + 52)))
            clockwise = bool(self._u8(o + 2) & 0x40)
            points = self._arc_points(start, end, centre, clockwise)
        else:
            points = [start, end]
        return width, points, self._u32(o + 8), start, end

    def _chain(self, first, owner, limit=100000):
        """Follow a segment chain from `first` until it returns to the owner block."""
        out = []
        key = first
        seen = set()
        while key != owner:
            if key in seen or len(out) > limit:
                raise AllegroBrdParseError('Endless segment chain')
            seen.add(key)
            width, points, key, start, end = self._segment(key)
            out.append((width, points, start, end))
            if not out:
                break
        return out

    def _ring(self, chain):
        """Closed outline of a chain of segments as a point list."""
        points = []
        for _w, seg, _s, _e in chain:
            if points:
                if math.hypot(points[-1][0] - seg[0][0], points[-1][1] - seg[0][1]) > 1e-3:
                    raise AllegroBrdParseError('Shape outline is not continuous')
                points.extend(seg[1:])
            else:
                points.extend(seg)
        if len(points) < 4 or math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) > 1e-3:
            raise AllegroBrdParseError('Shape outline is not closed')
        return points[:-1]

    def _polygon(self, points):
        polygon = Polygon(points)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
            if not polygon.is_valid:
                raise AllegroBrdParseError('Invalid shape outline')
        if polygon.is_empty:
            raise AllegroBrdParseError('Empty shape outline')
        return polygon

    # ------------------------------------------------------------------ transforms

    @staticmethod
    def _compose(outer, inner):
        a1, b1, c1, d1, e1, f1 = outer
        a2, b2, c2, d2, e2, f2 = inner
        return (a1 * a2 + b1 * d2, a1 * b2 + b1 * e2, a1 * c2 + b1 * f2 + c1,
                d1 * a2 + e1 * d2, d1 * b2 + e1 * e2, d1 * c2 + e1 * f2 + f1)

    @staticmethod
    def _rotation(angle):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        return (c, -s, 0.0, s, c, 0.0)

    @staticmethod
    def _apply(geo, matrix):
        a, b, c, d, e, f = matrix
        return affine_transform(geo, (a, b, d, e, c, f))

    @staticmethod
    def _apply_pt(point, matrix):
        a, b, c, d, e, f = matrix
        return (a * point[0] + b * point[1] + c, d * point[0] + e * point[1] + f)

    # ------------------------------------------------------------------ padstacks

    def _padstack(self, key):
        cached = self._padstacks.get(key)
        if cached is not None:
            return cached
        _t, o, n = self._need(key, 0x1C)
        count = self._u8(o + 2)
        layers = self._u16(o + 44)
        if count:
            # the extra table of such a padstack was not met in any sample board, so where the component table
            # lies in it is not known
            raise AllegroBrdParseError('Padstack %s has an extra data table, which is not supported' %
                                       self._string(self._u32(o + 12)))
        base = o + 192 + (32 if self._ver >= 180 else 0)
        comps = []
        for i in range(_FIXED_COMPS + _PER_LAYER * layers):
            e = base + 36 * i
            if e + 36 > o + n:
                raise AllegroBrdParseError('Padstack component table is cut off')
            comps.append({'type': self._u8(e), 'w': abs(self._mm(self._i32(e + 8))),
                          'h': abs(self._mm(self._i32(e + 12))), 'z1': self._mm(self._i32(e + 16)),
                          'ox': self._mm(self._i32(e + 20)), 'oy': self._mm(self._i32(e + 24))})
        pad_type = self._u8(o + 28) & 0xF0
        flags = self._u8(o + 30)
        slot_x, slot_y = self._mm(self._i32(o + 76)), self._mm(self._i32(o + 80))
        stack = {'name': self._string(self._u32(o + 12)), 'layers': layers, 'start': self._u8(o + 3),
                 'comps': comps, 'drill': self._mm(self._i32(o + 64)), 'slot': (slot_x, slot_y),
                 # bit 0x20 of the flag byte is set on every plated hole of the sample boards and clear on the
                 # mounting holes; pad type 0x80 is the documented non-plated type
                 'plated': bool(flags & 0x20) and pad_type != 0x80}
        if stack['drill'] < 0 or slot_x < 0 or slot_y < 0:
            raise AllegroBrdParseError('Negative drill size in padstack %s' % stack['name'])
        if self._untested:
            sizes = [stack['drill'], slot_x, slot_y] + [v for c in comps for v in (c['w'], c['h'])]
            if any(v >= _MAX_PADSTACK for v in sizes):
                raise AllegroBrdParseError('Padstack %s has a drill or pad size of %.0f mm or more' %
                                           (stack['name'], _MAX_PADSTACK))
        if stack['start'] + layers > len(self._etch_names):
            raise AllegroBrdParseError('Padstack %s uses copper layers the board does not have' % stack['name'])
        self._padstacks[key] = stack
        return stack

    def _comp_shape(self, comp, what):
        """Pad outline of a padstack component at the origin, unrotated; None for a null component."""
        t = comp['type']
        if t == SH_NULL:
            return None
        w, h = comp['w'], comp['h']
        if t in (SH_CIRCLE, SH_OCTAGON, SH_SQUARE, SH_RECT, SH_OBLONG_X, SH_OBLONG_Y, SH_ROUND_RECT,
                 SH_CHAMFER_RECT) and (w <= 0 or h <= 0):
            raise AllegroBrdParseError('Padstack shape with a zero size on %s' % what)
        if t == SH_CIRCLE:
            shape = self._disc(w / 2)
        elif t == SH_SQUARE:
            shape = box(-w / 2, -w / 2, w / 2, w / 2)
        elif t == SH_RECT:
            shape = box(-w / 2, -h / 2, w / 2, h / 2)
        elif t in (SH_OBLONG_X, SH_OBLONG_Y):
            # the round ends sit on the short sides, whichever of the two oblong codes is stored
            if w >= h:
                shape = LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=self._segs(h / 2))
            else:
                shape = LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=self._segs(w / 2))
        elif t == SH_OCTAGON:
            shape = self._chamfered(w, h, (1 - 1 / math.sqrt(2)) * min(w, h))
        elif t == SH_ROUND_RECT:
            self._warn('shape:%x' % t, 'Rounded rectangle pads are drawn with the corner radius from the padstack '
                                       '(not checked against a reference file)')
            radius = comp['z1'] if comp['z1'] > 0 else 0.25 * min(w, h)
            radius = min(radius, min(w, h) / 2)
            shape = box(-w / 2 + radius, -h / 2 + radius, w / 2 - radius, h / 2 - radius).buffer(
                radius, quad_segs=self._segs(radius)) if radius * 2 < min(w, h) - 1e-12 else \
                (LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=self._segs(h / 2)) if w >= h
                 else LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=self._segs(w / 2)))
        elif t == SH_CHAMFER_RECT:
            self._warn('shape:%x' % t, 'Chamfered rectangle pads are drawn with the chamfer from the padstack '
                                       '(not checked against a reference file)')
            shape = self._chamfered(w, h, comp['z1'] if comp['z1'] > 0 else 0.25 * min(w, h))
        else:
            raise AllegroBrdParseError('Unsupported pad shape %s (code 0x%02X) on %s' % (
                _SHAPE_NAMES.get(t, 'unknown'), t, what))
        if comp['ox'] or comp['oy']:
            shape = affine_transform(shape, (1, 0, 0, 1, comp['ox'], comp['oy']))
        return shape

    @staticmethod
    def _chamfered(w, h, c):
        c = min(c, w / 2, h / 2)
        x, y = w / 2, h / 2
        if c <= 0:
            return box(-x, -y, x, y)
        return Polygon([(-x + c, -y), (x - c, -y), (x, -y + c), (x, y - c), (x - c, y), (-x + c, y),
                        (-x, y - c), (-x, -y + c)])

    # ------------------------------------------------------------------ storage

    def _add(self, name, geo, follow=None):
        if geo is None:
            return
        if not geo.is_valid:
            geo = geo.buffer(0)
            if not geo.is_valid:
                raise AllegroBrdParseError('Invalid geometry on %s' % name)
        polygons = self._polygons_of(geo)
        if polygons:
            self._solids.setdefault(name, []).extend(polygons)
        if follow:
            self._follows.setdefault(name, []).extend(follow)

    def _copper_name(self, index):
        if not 0 <= index < len(self._etch_names):
            raise AllegroBrdParseError('Copper layer index %d is outside the stackup' % index)
        return self._etch_names[index]

    # ------------------------------------------------------------------ owners

    def _classify(self):
        """Footprint instances (placed, absolute geometry) and definitions (library copies, local geometry)."""
        self._insts = {}
        for t, o, _n in self._by_type.get(0x2D, []):
            side = self._u8(o + 2)
            if side not in (0, 1, 3):
                raise AllegroBrdParseError('Unknown footprint side code %d' % side)
            self._insts[self._u32(o + 4)] = {
                'o': o, 'bottom': side != 0, 'rot': self._i32(o + 28) / 1000.0,
                'x': self._mm(self._i32(o + 32)), 'y': self._mm(self._i32(o + 36))}
        self._defs = {self._u32(o + 4) for _t, o, _n in self._by_type.get(0x2B, [])}
        self._text_owner = {}

    def _is_def(self, key):
        return key in self._defs

    def _owner_of_text(self, key):
        """The block a text chain ends at (footprint instance, definition or the board)."""
        trail = []
        current = key
        while True:
            if current in self._text_owner:
                result = self._text_owner[current]
                break
            trail.append(current)
            entry = self._blk(current, 0x30)
            if entry is None:
                result = current
                break
            current = self._u32(entry[1] + 8)
            if len(trail) > len(self._blocks):
                raise AllegroBrdParseError('Endless text chain')
        for item in trail:
            self._text_owner[item] = result
        return result

    # ------------------------------------------------------------------ copper

    def _build_pads_and_vias(self):
        count = len(self._etch_names)
        for _t, o, _n in self._by_type.get(0x32, []):
            parent = self._u32(o + 28)
            inst = self._insts.get(parent)
            if inst is None:
                continue
            pad_entry = self._blk(self._u32(o + 36), 0x0D)
            if pad_entry is None:
                raise AllegroBrdParseError('Placed pad refers to a missing pad definition')
            self._place_pad(inst, pad_entry[1], count)
        for _t, o, _n in self._by_type.get(0x33, []):
            stack = self._padstack(self._u32(o + 44))
            x, y = self._mm(self._i32(o + 32)), self._mm(self._i32(o + 36))
            matrix = (1.0, 0.0, x, 0.0, 1.0, y)
            span = list(range(stack['start'], stack['start'] + stack['layers']))
            self._pad_layers(stack, span, matrix)
            self._pad_masks(stack, matrix, False, (0 in span, (count - 1) in span))
            if stack['drill'] > 0:
                self._record_drill((x, y), stack, span, 'via', 0.0)

    def _pad_matrix(self, inst, pad_o):
        px, py = self._mm(self._i32(pad_o + self._PAD_X)), self._mm(self._i32(pad_o + self._PAD_Y))
        prot = self._i32(pad_o + self._PAD_ROT) / 1000.0
        flip = inst['bottom']
        if flip:
            px = -px
        c, s = math.cos(math.radians(inst['rot'])), math.sin(math.radians(inst['rot']))
        origin = (inst['x'] + px * c - py * s, inst['y'] + px * s + py * c)
        # local shape: rotate by the pad angle, mirror when the footprint is on the bottom, then rotate by the
        # footprint angle
        local = self._rotation(prot)
        if flip:
            local = self._compose((-1.0, 0.0, 0.0, 0.0, 1.0, 0.0), local)
        matrix = self._compose(self._rotation(inst['rot']), local)
        matrix = (matrix[0], matrix[1], origin[0], matrix[3], matrix[4], origin[1])
        return matrix, origin, prot

    def _place_pad(self, inst, pad_o, count):
        stack = self._padstack(self._u32(pad_o + self._PAD_STACK))
        if stack['start'] != 0:
            raise AllegroBrdParseError('Pad with padstack %s starting below the top layer is not supported' %
                                       stack['name'])
        matrix, origin, prot = self._pad_matrix(inst, pad_o)
        flip = inst['bottom']
        # the padstack's first layer is the side the footprint stands on: a bottom footprint is mirrored
        span = [count - 1 - (stack['start'] + g) if flip else stack['start'] + g for g in range(stack['layers'])]
        self._pad_layers(stack, span, matrix)
        self._pad_masks(stack, matrix, flip, (True, True))
        if stack['drill'] > 0 and stack['layers'] > 1:
            self._record_drill(origin, stack, sorted(span), 'pad', inst['rot'] + (-prot if flip else prot))

    def _pad_layers(self, stack, span, matrix):
        """Pad copper of one padstack: span[g] is the board layer of padstack layer g."""
        for g, board_layer in enumerate(span):
            comp = stack['comps'][_FIXED_COMPS + _PER_LAYER * g + _SLOT_PAD]
            shape = self._comp_shape(comp, 'copper of padstack %s' % stack['name'])
            if shape is not None:
                self._add(self._copper_name(board_layer), self._apply(shape, matrix))

    def _pad_masks(self, stack, matrix, flip, allowed):
        """Mask and paste openings; slot 0 of each pair belongs to the side the padstack is defined on."""
        comps = stack['comps']
        sides = ('BOTTOM', 'TOP') if flip else ('TOP', 'BOTTOM')
        order = (1, 0) if flip else (0, 1)
        for kind, slots in (('SOLDERMASK_', _COMP_SOLDERMASK), ('PASTEMASK_', _COMP_PASTEMASK)):
            for i in range(2):
                if not allowed[order[i]]:
                    continue
                shape = self._comp_shape(comps[slots[i]], '%s of padstack %s' % (kind.lower().strip('_'),
                                                                                  stack['name']))
                if shape is not None:
                    self._add(kind + sides[i], self._apply(shape, matrix))

    # ------------------------------------------------------------------ drills

    def _record_drill(self, position, stack, span, what, angle):
        plated = stack['plated'] if what == 'pad' else True
        diameter = stack['drill']
        slot_x, slot_y = stack['slot']
        count = len(self._etch_names)
        if not plated:
            group = 'NPTH'
        elif span and span[0] == 0 and span[-1] == count - 1:
            group = 'PTH'
        else:
            group = 'PTH_%s-%s' % (self._copper_name(span[0]), self._copper_name(span[-1]))
        slot = None
        if slot_y > 0 and slot_x > 0 and abs(slot_x - slot_y) > 1e-9:
            self._warn('slot', 'Slotted holes were read from the padstack slot size without a reference file; '
                               'check their length and direction')
            length, width = max(slot_x, slot_y), min(slot_x, slot_y)
            diameter = width
            along_x = slot_x >= slot_y
            slot = (length, along_x)
        if diameter <= 0:
            raise AllegroBrdParseError('Non-positive hole diameter')
        tools = self._drills.setdefault(group, {})
        key = round(diameter, 6)
        tool = tools.setdefault(key, {'tooldia': diameter, 'drills': [], 'slots': []})
        if slot is None:
            tool['drills'].append(Point(position))
        else:
            length, along_x = slot
            half = (length - diameter) / 2
            direction = angle if along_x else angle + 90.0
            dx, dy = half * math.cos(math.radians(direction)), half * math.sin(math.radians(direction))
            tool['slots'].append((Point(position[0] - dx, position[1] - dy), Point(position[0] + dx, position[1] + dy)))

    # ------------------------------------------------------------------ drawn objects

    def _layer_targets(self, cls, sub):
        """Output layer names that receive geometry of (class, subclass)."""
        names = []
        if cls == C_ETCH:
            if sub < len(self._etch_names):
                names.append(self._etch_names[sub])
            return names
        for name, pairs in _SUBCLASSES.items():
            if (cls, sub) in pairs:
                names.append(name)
        return names

    def _build_graphics(self):
        for _t, o, _n in self._by_type.get(0x14, []):
            parent = self._u32(o + 12)
            if self._is_def(parent):
                continue
            cls, sub = self._u8(o + 2), self._u8(o + 3)
            targets = self._layer_targets(cls, sub)
            if not targets:
                continue
            if cls == C_ETCH:
                self._routed.add(targets[0])
            chain = self._chain(self._u32(o + 24), self._u32(o + 4))
            solids, follows = [], []
            copperish = cls == C_ETCH or any(name.startswith(('SOLDERMASK', 'PASTEMASK')) for name in targets)
            for width, points, _s, _e in chain:
                effective = width
                if width == 0:
                    if copperish:
                        self._warn('zero-copper', 'Zero-width lines on copper, solder mask and paste layers were '
                                                  'skipped')
                    else:
                        effective = self.HAIRLINE
                solids.append(self._stroke(points, effective))
                if len(set(points)) > 1:
                    follows.append(LineString(points))
            for name in targets:
                self._add(name, unary_union(solids) if solids else None, follows)

    def _build_tracks(self):
        for _t, o, _n in self._by_type.get(0x05, []):
            cls, sub = self._u8(o + 2), self._u8(o + 3)
            if cls != C_ETCH:
                continue
            name = self._copper_name(sub)
            self._routed.add(name)
            chain = self._chain(self._u32(o + 56), self._u32(o + 4))
            solids, follows = [], []
            for width, points, _s, _e in chain:
                if width == 0:
                    self._warn('zero-track', 'Zero-width tracks on copper layers were skipped')
                solids.append(self._stroke(points, width))
                follows.append(LineString(points))
            self._add(name, unary_union(solids) if solids else None, follows)

    def _shape_polygon(self, o, key):
        """Filled outline of a 0x28 shape (its segment chain) minus its void chain."""
        chain = self._chain(self._u32(o + 40), key)
        if not chain:
            raise AllegroBrdParseError('Shape without segments')
        polygon = self._polygon(self._ring(chain))
        voids = []
        kout = self._u32(o + 36)
        seen = set()
        while kout and kout != key:
            entry = self._need(kout, 0x34)
            if kout in seen:
                raise AllegroBrdParseError('Endless void chain')
            seen.add(kout)
            void_chain = self._chain(self._u32(entry[1] + 24), kout)
            if void_chain:
                voids.append(self._polygon(self._ring(void_chain)))
            kout = self._u32(entry[1] + 8)
        if voids:
            polygon = polygon.difference(unary_union(voids))
        return polygon, chain

    def _build_shapes(self):
        fills = {}
        boundaries = []
        for _t, o, _n in self._by_type.get(0x28, []):
            parent = self._u32(o + 12)
            if self._is_def(parent):
                continue
            cls, sub = self._u8(o + 2), self._u8(o + 3)
            key = self._u32(o + 4)
            if cls == C_BOUNDARY:
                if sub < len(self._etch_names):
                    boundaries.append((sub, key, o))
                continue
            if cls == C_ETCH:
                polygon, chain = self._shape_polygon(o, key)
                name = self._copper_name(sub)
                self._routed.add(name)
                fills.setdefault(sub, []).append(polygon)
                self._add(name, polygon, [LineString(points) for _w, points, _s, _e in chain if len(set(points)) > 1])
                continue
            targets = self._layer_targets(cls, sub)
            if not targets:
                continue
            polygon, chain = self._shape_polygon(o, key)
            lines = [LineString(points) for _w, points, _s, _e in chain if len(set(points)) > 1]
            for name in targets:
                if name == 'OUTLINE':
                    # an outline is a line, however the shape is filled
                    ring = list(polygon.exterior.coords)
                    self._add(name, self._stroke(ring, self.HAIRLINE), lines)
                    continue
                if name.startswith(('SILKSCREEN', 'ASSEMBLY')):
                    self._warn('shape-fill', 'Shapes on silkscreen and assembly layers are drawn filled')
                self._add(name, polygon, lines)
        for sub, key, o in boundaries:
            outline = self._polygon(self._ring(self._chain(self._u32(o + 40), key)))
            if not any(outline.intersects(fill) and outline.intersection(fill).area > 0 for fill in fills.get(sub, [])):
                raise AllegroBrdParseError(
                    'The copper shape on %s was never filled (no stored fill found): open the board in Allegro, '
                    'update the shapes and save it again' % self._etch_names[sub])

    def _build_rects(self):
        for t, o, _n in self._by_type.get(0x24, []) + self._by_type.get(0x0E, []):
            cls, sub = self._u8(o + 2), self._u8(o + 3)
            targets = self._layer_targets(cls, sub)
            if t == 0x24:
                parent, base = self._u32(o + 12), o + 24
                rot_at = o + 52
            else:
                parent, base = self._u32(o + 12), o + 36
                rot_at = o + 64
            if self._is_def(parent) or not targets:
                continue
            if cls == C_ETCH:
                raise AllegroBrdParseError('Rectangles on copper layers are not supported')
            x1, y1, x2, y2 = (self._mm(self._i32(base + 4 * i)) for i in range(4))
            rot = self._i32(rot_at) / 1000.0
            ring = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
            if rot:
                # the first corner is the pivot: the rectangle is turned about it
                matrix = self._compose((1.0, 0.0, x1, 0.0, 1.0, y1),
                                       self._compose(self._rotation(rot), (1.0, 0.0, -x1, 0.0, 1.0, -y1)))
                ring = [self._apply_pt(p, matrix) for p in ring]
            line = LineString(ring)
            for name in targets:
                self._add(name, self._stroke(ring, self.HAIRLINE), [line])

    # ------------------------------------------------------------------ text

    # text block fields: 17.2 as checked; from 17.4 one more word before the string pointer (as in the checked 18.1)
    _TEXT_FIELDS = {False: {'str': 28, 'x': 40, 'y': 44, 'rot': 52}, True: {'str': 32, 'x': 44, 'y': 48, 'rot': 56}}

    def _fonts(self):
        fonts = {}
        for _t, o, _n in self._by_type.get(0x36, []):
            if self._u16(o + 2) != 0x08:
                continue
            newer = self._ver >= 174
            items = self._u32(o + 16)
            size = 64 + (4 if 174 <= self._ver < 190 else 0)
            first = o + 32 + 4 * newer
            for i in range(items):
                e = first + size * i
                words = [self._i32(e + 4 * k) for k in range(size // 4)]
                fonts[i + 1] = words
            break
        return fonts

    def _text_shape(self, text, height, width, space, line_space, stroke, align, mirror):
        """Text at its anchor (baseline, left/right/centre by `align` 1/2/3) -> (solid, follow lines).

        Allegro sets text on a fixed pitch of character width plus character space. The glyphs come from the
        stroke font of the KiCad reader, one character at a time, so that the pitch is Allegro's.
        """
        engine = self._text_engine
        size_h = height * self.CAP_TO_HEIGHT / engine.CAP_RATIO
        pitch = width + space
        cache = {}
        follows = []
        for row, line in enumerate(text.split('\n')):
            extent = max(len(line) * pitch - space, 0.0)
            shift = {2: -extent, 3: -extent / 2}.get(align, 0.0)
            for index, char in enumerate(line):
                if char == ' ':
                    continue
                if char not in cache:
                    _solid, lines = engine._text_geometry(char, size_h, size_h, stroke, ['left', 'bottom'], False)
                    cache[char] = lines
                dx, dy = shift + index * pitch, -row * (line_space or 1.25 * height)
                follows.extend(affine_transform(glyph, (1, 0, 0, 1, dx, dy)) for glyph in cache[char])
        if mirror:
            follows = [affine_transform(line, (-1, 0, 0, 1, 0, 0)) for line in follows]
        if not follows:
            return Polygon(), []
        # 1 buffer of all strokes gives the same shape as a union of 1 buffer per stroke, 2x faster
        solid = MultiLineString(follows).buffer(stroke / 2, quad_segs=self._segs(stroke / 2))
        return solid, follows

    def _build_text(self):
        fields = self._TEXT_FIELDS[self._ver >= 174]
        fonts = None
        newer = self._ver >= 174
        for _t, o, _n in self._by_type.get(0x30, []):
            cls, sub = self._u8(o + 2), self._u8(o + 3)
            targets = self._layer_targets(cls, sub)
            if not targets:
                continue
            owner = self._owner_of_text(self._u32(o + 8))
            if self._is_def(owner):
                continue
            string_entry = self._blk(self._u32(o + fields['str']), 0x31)
            if string_entry is None:
                continue
            so = string_entry[1]
            length = self._u16(so + 22)
            raw = self._d[so + 24 + 4 * newer: so + 24 + 4 * newer + length]
            if len(raw) != length:
                raise AllegroBrdParseError('Truncated text')
            text = raw.split(b'\0')[0].decode('latin-1')
            if not text.strip():
                continue
            if fonts is None:
                fonts = self._fonts()
            font_key, _flags, align, reverse = (self._u8(o + 20 + i) for i in range(4))
            words = fonts.get(font_key)
            if words is None:
                raise AllegroBrdParseError('Text refers to a missing font definition')
            height, width = abs(self._mm(words[2])), abs(self._mm(words[3]))
            if height <= 0 or width <= 0:
                continue
            x, y = self._mm(self._i32(o + fields['x'])), self._mm(self._i32(o + fields['y']))
            rot = self._i32(o + fields['rot']) / 1000.0
            mirror = reverse in (1, 3)
            # words of a font entry: 2 height, 3 width, 4 character space, 5 line space, 7 line width. A font
            # without a line width is plotted by Allegro with its undefined line width, a hairline
            stroke = abs(self._mm(words[7])) or self.HAIRLINE
            solid, follows = self._text_shape(text, height, width, abs(self._mm(words[4])),
                                              abs(self._mm(words[5])), stroke, align, mirror)
            self._text_used = True
            if solid.is_empty:
                continue
            matrix = self._compose((1.0, 0.0, x, 0.0, 1.0, y), self._rotation(rot))
            solid = self._apply(solid, matrix)
            follows = [self._apply(line, matrix) for line in follows]
            for name in targets:
                if name in self._etch_names:
                    self._warn('text-copper', 'Text on copper layers is drawn with the approximate stroke font; '
                                              'check it before using those layers')
                self._add(name, solid, follows)

    # ------------------------------------------------------------------ main parse

    _PAD_X, _PAD_Y, _PAD_STACK, _PAD_ROT = 16, 20, 24, 40

    def _parse(self):
        self._header()
        if self._ver >= 174:
            self._PAD_X, self._PAD_Y, self._PAD_STACK, self._PAD_ROT = 20, 24, 28, 44
        else:
            self._PAD_X, self._PAD_Y, self._PAD_STACK, self._PAD_ROT = 16, 20, 24, 40
        offset = self._read_strings()
        self._scan(offset)
        self._read_layers()
        self._classify()
        self._build_pads_and_vias()
        self._build_tracks()
        self._build_graphics()
        self._build_shapes()
        self._build_rects()
        self._build_text()
        self._finish()

    # ------------------------------------------------------------------ result

    def _outline(self):
        lines = [line for line in self._follows.get('OUTLINE', [])]
        if not lines:
            return Polygon()
        faces = list(polygonize(unary_union(lines)))
        if not faces:
            self._warn('outline', 'The board outline is not a closed shape; the board outline was left empty')
            return Polygon()
        kept = []
        for face in faces:
            probe = face.representative_point()
            if not any(other is not face and other.interiors and
                       any(Polygon(ring).contains(probe) for ring in other.interiors) for other in faces):
                kept.append(face)
        return unary_union(kept) if len(kept) > 1 else kept[0]

    def _build_drills(self):
        for group in sorted(self._drills, key=lambda name: (name != 'PTH', name != 'NPTH', name)):
            tools = {}
            for number, key in enumerate(sorted(self._drills[group]), 1):
                tools[number] = self._drills[group][key]
            self.drill_groups[group] = tools

    def _drop_unrouted_inner_layers(self):
        """An inner layer with no track, shape or line may be a negative plane layer, whose copper is implied."""
        for name in self._etch_names[1:-1]:
            if name in self._routed or (name not in self._solids and name not in self._follows):
                continue
            self._solids.pop(name, None)
            self._follows.pop(name, None)
            self._warn('plane:' + name, 'Inner layer %s has no tracks or shapes; it may be a negative plane layer, '
                                        'whose copper is not stored, so the layer was left out' % name)

    def _finish(self):
        self._drop_unrouted_inner_layers()
        self._build_drills()
        self.board_outline = self._outline()
        ordered = list(self._etch_names) + _NONCOPPER_ORDER
        for name in ordered:
            polygons = self._polygons_of(unary_union(self._solids.get(name, [])))
            if not polygons:
                continue
            # Enclosing polygons MUST precede islands, otherwise an enclosing hole erases the island.
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            label = ('ETCH/%s' % name) if name in self._etch_names else name
            self.layers[name] = {
                'name': label,
                'solid_geometry': polygons,
                'follow_geometry': list(self._follows.get(name, [])),
                'tools': {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': polygon} for polygon in polygons]}}}
        self.default_layers = [name for name in self.layers if not name.startswith('ASSEMBLY')]
        if self._untested:
            self._strict_gate()
        if self._text_used:
            self.warnings.append('Text is drawn with an approximate stroke font; glyph shapes, sizes and spacing '
                                 'differ from Allegro')
        if self._text_engine._unsupported_chars:
            self.warnings.append('Characters without a glyph were left blank: %s' %
                                 ''.join(sorted(self._text_engine._unsupported_chars)))
        if self._untested:
            self.warnings.insert(0, 'Allegro %s is not tested: check the result against Gerbers made by Allegro.' %
                                 self.version)

    def _strict_gate(self):
        """Extra checks for a release that was not compared with a sample board: finite, plausible geometry."""
        points = [point for tools in self.drill_groups.values() for tool in tools.values()
                  for point in tool['drills'] + [p for slot in tool['slots'] for p in slot]]
        boxes = [(name, polygon.bounds) for name, layer in self.layers.items() if name != 'OUTLINE'
                 for polygon in layer['solid_geometry']]
        boxes += [('drill', point.bounds) for point in points]
        if self.board_outline.is_empty:
            limit = (-_MAX_BOARD, -_MAX_BOARD, _MAX_BOARD, _MAX_BOARD)
            where = 'inside +-%.0f m' % (_MAX_BOARD / 1000)
        else:
            minx, miny, maxx, maxy = self.board_outline.bounds
            limit = (minx - _GATE_MARGIN, miny - _GATE_MARGIN, maxx + _GATE_MARGIN, maxy + _GATE_MARGIN)
            where = 'inside the board outline'
        for name, bounds in boxes:
            if not all(math.isfinite(v) for v in bounds):
                raise AllegroBrdParseError('The geometry of %s has a non-finite coordinate' % name)
            if bounds[0] < limit[0] or bounds[1] < limit[1] or bounds[2] > limit[2] or bounds[3] > limit[3]:
                raise AllegroBrdParseError('A %s item at (%.2f, %.2f) mm lies outside the board: all geometry '
                                           'must be %s' % (name, bounds[0], bounds[1], where))
