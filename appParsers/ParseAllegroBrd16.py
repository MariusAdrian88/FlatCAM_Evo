# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native Allegro 16.x board (.brd) reader                   #
# Author: Marius Stanciu (c)                                #
# Format facts also from TRACE Boardviewer (MIT)            #
# https://github.com/trace-boardviewer/trace-boardviewer    #
# and the KiCad Allegro format notes (docs.kicad.org)       #
# MIT Licence                                               #
# ##########################################################

"""Read Cadence Allegro 16.x board (.brd) files: the layout of releases 16.0 to 16.6.

The 16.x releases store the same blocks as 17.2, with fewer fields: a block of 16.x is the 17.2 block with some
words left out, and padstacks keep their pad components in a different table (28 byte entries, three per copper
layer, 10 or 11 fixed entries before them). The record widths and offsets are facts from the layout notes of
the KiCad importer (https://dev-docs.kicad.org/en/import-formats/allegro/index.html and
https://docs.kicad.org/doxygen/md_src_2pcbnew_2pcb__io_2allegro_2FORMAT.html) and of TRACE Boardviewer
(https://github.com/trace-boardviewer/trace-boardviewer, `src/lib/formats/allegro.ts`, `docs/ALLEGRO-BRD.md`);
no code was copied.

This reader walks the 16.x blocks with their own sizes (the walk must end exactly at the end of the file), then
writes the blocks the geometry code needs into the 17.2 layout (left-out words are zero) and lets the checked
17.2 code build the copper, mask, silk and drill geometry from them. Every field was compared with the
Allegro Gerber and drill output of the same boards (see tests/test_allegro16_parser.py for the opt-in board
tests). Padstack features that 16.x boards store differently from what was compared (extra padstack tables,
rounded and chamfered pads) are refused by name.

What the Gerber and drill files of the sample boards showed beyond the layout notes:

* the clockwise bit (0x40) of an arc is in the 4th byte of the block in 16.x, not in the 3rd
* the fixed padstack slots 1, 5 and 6 hold the top solder mask, bottom solder mask and top paste mask openings
  (the 10 or 11 fixed entries come before the 3 entries per copper layer: antipad, thermal relief, pad)
* a pad shape of type 0x16 ("shape symbol") is a 0x28 shape around the pad origin
* rectangles on copper, solder mask and paste mask layers are solid, rectangles elsewhere are outlines
* a via without bit 0x10 in the 4th byte of its block (0x80 only) is in neither the films nor the drill files;
  such vias and the tracks whose owner (word 3) is a footprint definition (0x2B) are library copies
* the board outline is the faces of the outline lines that hold drill holes (drawing frames and closed text
  glyphs on the outline subclass are left out)
* a release is read without a remark when boards of it were compared (16.4 and 16.6); 16.0, 16.2 and 16.5 go
  through the strict gate of the 17.x reader and carry a warning
* where the board asks to leave out the unconnected pads of internal layers (a film option and, from 16.5,
  dynamic unused pad suppression) and where the mask films leave out the via openings: see the notes above
  AllegroBrd16Parser._read_films
"""

import struct

from shapely import LineString, Polygon
from shapely.affinity import affine_transform
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from appParsers.ParseAllegroBrd import (AllegroBrdParser, AllegroBrdParseError, C_ETCH, _FIXED_COMPS,
                                        _PER_LAYER, _STRINGS_AT, _UNIT_MM, _U32)

# Magic number (low byte masked) -> (release name, layout number, checked against Gerbers). The layout numbers
# follow the version gates of the KiCad notes (V_160 ... V_166).
_RELEASES16 = {
    0x00130000: ('16.0', 160, False),
    0x00130400: ('16.2', 162, False),
    0x00130500: ('16.2', 162, False),
    0x00130C00: ('16.4', 164, True),
    0x00131000: ('16.5', 165, False),
    0x00131500: ('16.6', 166, True),
}

# Fixed block sizes (bytes with the 4-byte block header) of the 16.x layouts.
_FIXED16 = {0x01: 80, 0x04: 20, 0x05: 60, 0x06: 36, 0x07: 40, 0x08: 24, 0x09: 44, 0x0A: 68, 0x0C: 56, 0x0D: 40,
            0x0E: 60, 0x0F: 56, 0x10: 32, 0x11: 24, 0x14: 32, 0x15: 40, 0x16: 40, 0x17: 40, 0x1B: 56, 0x20: 40,
            0x22: 40, 0x24: 52, 0x26: 20, 0x28: 68, 0x2C: 36, 0x2D: 64, 0x2E: 36, 0x2F: 32, 0x30: 44, 0x32: 76,
            0x33: 72, 0x34: 32, 0x35: 124, 0x37: 428, 0x39: 60, 0x3A: 16}

# Items of a 0x36 definition table (bytes) in the 16.x layouts
_TABLE_ITEM16 = {0x03: 32, 0x05: 28, 0x06: 208, 0x08: 32, 0x0B: 1016, 0x0C: 232, 0x0D: 200, 0x0F: 20, 0x10: 108,
                 0x12: 1052}

_PAD_MASK_SLOTS16 = {1: 14, 5: 15, 6: 16}   # 16.x fixed padstack slot -> slot of the 17.2 layout


class AllegroBrd16Parser(AllegroBrdParser):
    """Allegro 16.0 - 16.6 boards; the result is the same as that of AllegroBrdParser."""

    # ------------------------------------------------------------------ header

    def _header(self):
        data = self._d
        if len(data) < 4:
            raise AllegroBrdParseError('Not an Allegro board: the file is empty or too short')
        magic = self._u32(0) & 0xFFFFFF00
        release = _RELEASES16.get(magic)
        if release is None:
            if magic >> 16 == 0x0012:
                raise AllegroBrdParseError('Allegro 15.x boards are not supported (magic 0x%08X): save the board in '
                                           'Allegro 16 or newer' % self._u32(0))
            raise AllegroBrdParseError('Not an Allegro 16.x board (magic 0x%08X)' % self._u32(0))
        self.version, self._ver, checked = release
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
        self._string_count_at, self._end27_at, self._unit_at, self._div_at = 0x194, 0x18C, 0x180, 0x26C
        unit = self._u8(self._unit_at)
        if unit not in self._UNIT16:
            raise AllegroBrdParseError('Board unit code %d is not supported (only mils, millimetres and '
                                       'centimetres were checked against sample boards)' % unit)
        divisor = self._u32(self._div_at)
        if divisor <= 0 or divisor > 10 ** 7:
            raise AllegroBrdParseError('Invalid unit divisor %d' % divisor)
        self._scale = self._UNIT16[unit] / divisor

    # millimetres per (coordinate / divisor) of a board unit code
    _UNIT16 = {**_UNIT_MM, 3: 1.0}

    # ------------------------------------------------------------------ block sizes

    @staticmethod
    def _fixed_sizes(ver):
        sizes = dict(_FIXED16)
        sizes[0x12] = 24 + 4 * (ver >= 165)
        sizes[0x23] = 68 + 16 * (ver >= 164)
        sizes[0x2B] = 68 + 4 * (ver >= 164)
        sizes[0x38] = 64 if ver < 166 else 52
        return sizes

    def _field_size16(self, o):
        sub = self._u8(o + 12)
        size = self._u16(o + 14)
        if sub == 0x65:
            payload = 0
        elif sub in (0x64, 0x66, 0x67, 0x6A):
            payload = 4
        elif sub == 0x69:
            payload = 8
        elif sub == 0xF6:
            payload = 80
        elif sub in (0x68, 0x6B, 0x6D, 0x6E, 0x6F, 0x71, 0x73, 0x78):
            payload = (size + 3) // 4 * 4
        elif sub == 0x6C:
            payload = 4 + 4 * self._u32(o + 16)
        elif sub in (0x70, 0x74):
            payload = 4 + (self._u16(o + 18) + 4 * self._u16(o + 16) + 3) // 4 * 4
        elif size in (4, 8):
            payload = size
        else:
            raise AllegroBrdParseError('Unknown field block sub type 0x%02X at offset 0x%X' % (sub, o))
        return 16 + payload

    def _block_size(self, t, o, fixed, end27):
        ver = self._ver
        if t in fixed:
            return fixed[t]
        if t == 0x1C:
            layers = self._u16(o + 50)
            if not 1 <= layers <= 64:
                raise AllegroBrdParseError('Padstack with %d layers at offset 0x%X' % (layers, o))
            head = 88 if ver >= 165 else 84
            comps = (11 if ver >= 165 else 10) + 3 * layers
            return head + comps * 28 - 4 + self._u8(o + 2) * 32
        if t == 0x1D:
            return 24 + self._u16(o + 20) * 256 + self._u16(o + 22) * (56 if ver >= 162 else 48)
        if t == 0x1E:
            return 24 + (self._u32(o + 20) + 3) // 4 * 4
        if t == 0x1F:
            return 28 + (280 if ver >= 162 else 240) * self._u16(o + 26) + 4
        if t == 0x21:
            return self._u32(o + 4)
        if t == 0x03:
            return self._field_size16(o)
        if t == 0x2A:
            return 8 + (36 if ver <= 164 else 12) * self._u16(o + 2)
        if t == 0x31:
            return 24 + (self._u16(o + 22) + 3) // 4 * 4
        if t == 0x27:
            return end27 - 1 - o
        if t == 0x3B:
            return 176 + (self._u32(o + 4) + 3) // 4 * 4
        if t == 0x36:
            code = self._u16(o + 2)
            items = self._u32(o + 12)
            if code == 0x02:
                item = 88 + (12 if ver >= 164 else 0)
            elif code in _TABLE_ITEM16:
                item = _TABLE_ITEM16[code]
            else:
                raise AllegroBrdParseError('Unknown definition table 0x%02X at offset 0x%X' % (code, o))
            return 28 + items * item
        if t == 0x3C:
            return 12 + 4 * self._u32(o + 8)
        raise AllegroBrdParseError('Unsupported block type 0x%02X at offset 0x%X' % (t, o))

    # ------------------------------------------------------------------ conversion to the 17.2 layout

    def _scan(self, offset):
        super()._scan(offset)
        self._convert()

    @staticmethod
    def _splice(block, inserts):
        """The block with zero words put in before the given (offset, byte count) positions."""
        out = bytearray()
        last = 0
        for at, count in inserts:
            out += block[last:at] + bytes(count)
            last = at
        out += block[last:]
        return bytes(out)

    # 17.2 words that 16.x does not have: type -> [(offset in the 16.x block, bytes)]
    _GAPS = {0x01: [(20, 4)], 0x15: [(20, 4)], 0x16: [(20, 4)], 0x17: [(20, 4)], 0x05: [(48, 8)], 0x14: [(20, 4)],
             0x24: [(20, 4)], 0x0E: [(28, 8)], 0x28: [(20, 8)], 0x32: [(20, 4), (52, 4)], 0x33: [(20, 4), (24, 4)],
             0x34: [(16, 4)], 0x0D: [(32, 4)]}

    def _convert(self):
        """Rewrite the blocks that the geometry needs in the 17.2 layout and index them again."""
        old = self._d
        blocks = self._blocks
        self._read_films()
        self._net_keys = {self._u32(o + 4) for t, o, _n in blocks if t == 0x04}
        out = bytearray(old[:_STRINGS_AT])
        converted = []
        next_string = max(self._strings, default=0) + 1
        for t, o, n in blocks:
            block = old[o:o + n]
            if t in (0x2D,):
                # 17.2 puts an unknown word before the two halfwords and one before the flags; the instance
                # reference (word 3) moves behind the coordinates
                block = (block[:12] + bytes(4) + block[16:20] + bytes(4) + block[20:36] + block[12:16] + block[36:])
            elif t == 0x30:
                block = (block[:12] + bytes(8) + block[20:24] + bytes(4) + block[12:16] + block[40:44] + bytes(4) +
                         block[24:28] + block[28:32] + block[32:36] + block[36:40])
            elif t == 0x2B:
                block = block + bytes(76 - n)
            elif t == 0x2A:
                block = self._convert_layer_list(block, next_string)
                next_string += self._u16(o + 2) if self._ver <= 164 else 0
            elif t == 0x1C:
                block = self._convert_padstack(block, o)
            elif t == 0x36 and self._u16(o + 2) == 0x08:
                items = self._u32(o + 12)
                body = block[28:]
                block = block[:12] + bytes(4) + block[12:28]
                for i in range(items):
                    block += body[32 * i:32 * i + 32] + bytes(32)
            elif t == 0x33 and not block[3] & 0x10:
                # a via without bit 0x10 in the 4th header byte (0x80: no net, no outline): neither the Gerber
                # films nor the drill files of the sample boards contain it. Real vias have 0x10 or 0x90.
                continue
            elif t == 0x01:
                # the sweep direction bit (0x40, clockwise) sits in the 4th byte of a 16.x arc, not in the 3rd
                block = self._splice(block[:2] + block[3:4] + block[3:4] + block[4:], self._GAPS[t])
            elif t in self._GAPS:
                block = self._splice(block, self._GAPS[t])
            elif t == 0x31:
                pass
            else:
                continue
            converted.append((t, len(out), len(block)))
            out += block
        self._d = bytes(out)
        self._ver = 172
        self._blocks = []
        self._index = {}
        self._by_type = {}
        for t, o, n in converted:
            entry = (t, o, n)
            self._blocks.append(entry)
            self._by_type.setdefault(t, []).append(entry)
            self._index.setdefault(self._u32(o + n - 4) if t == 0x2A else self._u32(o + 4), entry)

    def _convert_layer_list(self, block, next_string):
        count = struct.unpack_from('<H', block, 2)[0]
        if self._ver > 164:
            return block
        # 16.4 and older keep the layer name (32 bytes) in the list; 17.2 refers to a string
        out = bytearray(block[:4])
        for i in range(count):
            raw = block[4 + 36 * i:4 + 36 * i + 32]
            self._strings[next_string + i] = raw.split(b'\0')[0].decode('utf-8', 'replace')
            out += struct.pack('<III', next_string + i, struct.unpack_from('<I', block, 4 + 36 * i + 32)[0], 0)
        out += block[-4:]
        return bytes(out)

    def _convert_padstack(self, block, o):
        layers = struct.unpack_from('<H', block, 50)[0]
        head = 88 if self._ver >= 165 else 84
        fixed = 11 if self._ver >= 165 else 10
        head17 = bytearray(192)
        head17[0:16] = block[0:16]
        head17[64:68] = block[16:20]                  # drill size
        head17[76:84] = block[72:80]                  # slot size
        head17[44:46] = block[50:52]                  # layer count
        # 16.x: bit 0 of the flag byte marks a plated hole (non-plated holes carry bit 1 instead); the 17.2
        # code reads bit 5 of its flag byte
        head17[30] = 0x20 if block[41] & 0x01 else 0
        comps = []
        total = fixed + 3 * layers
        for i in range(total):
            e = head + 28 * i
            raw = block[e:e + 28] if i < total - 1 else block[e:e + 24] + bytes(4)
            comp = bytearray(36)
            comp[0:4] = raw[0:4]
            comp[8:16] = raw[4:12]                    # width, height
            comp[20:32] = raw[12:24]                  # offset x, y and the shape pointer
            comps.append(bytes(comp))
        null = bytes(36)
        table = [null] * (_FIXED_COMPS + _PER_LAYER * layers)
        for slot, target in _PAD_MASK_SLOTS16.items():
            table[target] = comps[slot]
        for g in range(layers):
            for k in range(3):
                table[_FIXED_COMPS + _PER_LAYER * g + k] = comps[fixed + 3 * g + k]
        # (the `count` items of the extra table behind the components are left out: a padstack with one is refused
        # by the 17.2 code, which reads `count` from the copied header)
        return bytes(head17) + b''.join(table)

    # ------------------------------------------------------------------ unconnected pads of internal layers
    # Allegro leaves out the pad of a pin or via on an internal layer when no copper of the same net touches it
    # there. Two settings of the board ask for that, and the films of the sample boards follow them exactly:
    # * the film option "suppress unconnected pads": bit 0x10000 of word 11 of the film's layer list block (0x39)
    #   is set when the film plots every pad and clear when it leaves the unconnected ones out; the film's layers
    #   are the 0x3A nodes chained from word 3 of that block (class 0x06 = copper, subclass = copper layer).
    #   BeagleBoard-xM: all internal films clear; BeagleBone: the two plane films clear; CutiePi: all set.
    # * dynamic unused pad suppression of 16.5 and newer (byte 0x669 of the header, set on BeagleBoard-X15 only):
    #   unconnected pads are left out on every internal layer, whatever the film says.
    # The net of a pin, via, track or shape is word 3 of its block (a net assignment block, 0x04). Dynamic
    # suppression keeps the pads of a pin without a net (BeagleBoard-X15: connector and mounting pins); the film
    # option leaves them out (BeagleBoard-xM). A via keeps the pads of the first and last layer of its padstack.

    _FILM_KEEPS_PADS = 0x10000
    _DYNAMIC_SUPPRESSION_AT = 0x669

    def _parse(self):
        self._inner_pads = []
        self._etch_nets = []
        self._suppressed = set()
        self._via_mask_off = set()
        self._mask_items = {}
        self._skip = ()
        self._dynamic = False
        self._net_keys = set()
        self._pad_net = None
        super()._parse()

    # Films also choose the classes they plot: the solder and paste mask films of the BeagleBone and X15 boards
    # plot the PIN class of their mask subclass but not the VIA CLASS (tented vias), those of CutiePi and the xM
    # plot both. A via opening is left out of a mask layer that films plot without the VIA CLASS.
    _MASK_SUBCLASSES = {0xFD: 'SOLDERMASK_TOP', 0xFC: 'SOLDERMASK_BOTTOM', 0xFB: 'PASTEMASK_TOP',
                        0xFA: 'PASTEMASK_BOTTOM'}
    _MASK_CODES = {name: code for code, name in _MASK_SUBCLASSES.items()}
    _C_PIN, _C_VIA = 0x0C, 0x12

    def _read_films(self):
        """From the 16.x film blocks: the copper layers whose films (all of them) leave out unconnected pads and
        the mask layers whose films plot no via openings."""
        index = self._index
        flags = {}
        masks = {}
        for t, o, _n in self._blocks:
            if t != 0x38:
                continue
            entry = index.get(self._u32(o + 12))
            if entry is None or entry[0] != 0x39 or entry[2] < 48:
                continue
            keeps = bool(self._u32(entry[1] + 44) & self._FILM_KEEPS_PADS)
            items = set()
            key = self._u32(entry[1] + 12)
            for _ in range(4096):
                node = index.get(key)
                if node is None or node[0] != 0x3A:
                    break
                items.add((self._u8(node[1] + 2), self._u8(node[1] + 3)))
                key = self._u32(node[1] + 8)
            for cls, sub in items:
                if cls == C_ETCH:
                    flags.setdefault(sub, []).append(keeps)
                elif cls == self._C_PIN and sub in self._MASK_SUBCLASSES:
                    masks.setdefault(self._MASK_SUBCLASSES[sub], set()).update(items)
        self._suppressed = {layer for layer, keeps in flags.items() if not any(keeps)}
        self._mask_items = masks
        self._via_mask_off = {name for name, items in masks.items()
                              if (self._C_VIA, self._MASK_CODES[name]) not in items}

    def _layer_targets(self, cls, sub):
        """A mask layer takes the (class, subclass) pairs that its films plot (Parallella HB1A-AAFM and Buddy: their
        mask films leave out the package geometry soldermask shapes)."""
        return [name for name in super()._layer_targets(cls, sub)
                if name not in self._mask_items or (cls, sub) in self._mask_items[name]]

    def _pad_masks(self, stack, matrix, flip, allowed):
        self._skip = self._via_mask_off if self._pad_net is None else ()
        try:
            super()._pad_masks(stack, matrix, flip, allowed)
        finally:
            self._skip = ()

    def _add(self, name, geo, follow=None):
        if name not in self._skip:
            super()._add(name, geo, follow)

    def _record_pad_component(self, name, solid, comp, matrix):
        if name not in self._skip:
            super()._record_pad_component(name, solid, comp, matrix)

    def _build_pads_and_vias(self):
        last = len(self._etch_names) - 1
        self._dynamic = bool(self._u8(self._DYNAMIC_SUPPRESSION_AT))
        if self._dynamic:
            self._suppressed = set(range(1, last))
        self._suppressed = {layer for layer in self._suppressed if 0 < layer < last}
        # net assignment (0x04) of each placed pad and via: word 3 of the block, 0 for a pin without a net
        self._pin_nets = {(self._u32(o + 28), self._u32(o + 36)): self._u32(o + 12)
                          for _t, o, _n in self._by_type.get(0x32, [])}
        self._via_nets = iter([self._u32(o + 12) for _t, o, _n in self._by_type.get(0x33, [])])
        super()._build_pads_and_vias()

    def _place_pad(self, inst, pad_o, count):
        self._pad_net = self._pin_nets.get((self._u32(inst['o'] + 4), self._u32(pad_o + 4)), 0)
        try:
            super()._place_pad(inst, pad_o, count)
        finally:
            self._pad_net = None

    def _pad_layers(self, stack, span, matrix):
        via = self._pad_net is None
        net = next(self._via_nets, 0) if via else self._pad_net
        if not self._suppressed or (self._dynamic and not net):
            return super()._pad_layers(stack, span, matrix)
        for g, board_layer in enumerate(span):
            comp = stack['comps'][_FIXED_COMPS + _PER_LAYER * g + 2]
            shape = self._comp_shape(comp, 'copper of padstack %s' % stack['name'])
            if shape is None:
                continue
            shape = self._apply(shape, matrix)
            if board_layer in self._suppressed and not (via and g in (0, len(span) - 1)):
                self._inner_pads.append((board_layer, shape, net, comp, matrix))
            else:
                self._add(self._copper_name(board_layer), shape)
                self._record_pad_component(self._copper_name(board_layer), shape, comp, matrix)
        return None

    def _is_net(self, key):
        return key in self._net_keys

    def _chain(self, first, owner, limit=100000):
        chain = super()._chain(first, owner, limit)
        if self._suppressed:
            entry = self._index.get(owner)
            if entry is not None and entry[0] == 0x05:
                o = entry[1]
                net = self._u32(o + 12)
                if self._u8(o + 2) == C_ETCH and self._u8(o + 3) in self._suppressed and self._is_net(net):
                    self._etch_nets.append((self._u8(o + 3), net, list(chain)))
        return chain

    def _shape_polygon(self, o, key):
        result = super()._shape_polygon(o, key)
        if self._suppressed and self._u8(o + 2) == C_ETCH and self._u8(o + 3) in self._suppressed:
            net = self._u32(o + 12)
            if self._is_net(net):
                self._etch_nets.append((self._u8(o + 3), net, result[0]))
        return result

    def _drop_unrouted_inner_layers(self):
        """Put in the pads of the suppressed internal layers that copper of their own net touches."""
        pads = {}
        for board_layer, shape, net, comp, matrix in self._inner_pads:
            pads.setdefault(board_layer, []).append((shape, net, comp, matrix))
        etch = {}
        for board_layer, net, item in self._etch_nets:
            etch.setdefault(board_layer, []).append((net, item))
        for board_layer, entries in pads.items():
            shapes = [entry[0] for entry in entries]
            tree = STRtree(shapes)
            keep = [False] * len(entries)
            for net, item in etch.get(board_layer, []):
                if isinstance(item, list):
                    item = unary_union([self._stroke(points, width) for width, points, _s, _e in item])
                for i in tree.query(item, predicate='intersects'):
                    if entries[i][1] == net:
                        keep[i] = True
            for i, (shape, net, _comp, _matrix) in enumerate(entries):
                if not keep[i] and net:
                    # pads of one net that overlap (a via in a pad) connect each other
                    keep[i] = any(j != i and entries[j][1] == net for j in tree.query(shape, predicate='intersects'))
            name = self._copper_name(board_layer)
            for i, (shape, _net, comp, matrix) in enumerate(entries):
                if keep[i]:
                    self._add(name, shape)
                    self._record_pad_component(name, shape, comp, matrix)
            if not all(keep):
                self._warn('nfp', 'Unconnected pads of internal layers were left out (as the board asks)')
        self._inner_pads = []
        self._etch_nets = []
        super()._drop_unrouted_inner_layers()

    def _outline(self):
        """The faces of the outline lines that hold drill holes, when there are such faces.

        The outline subclass also carries drawing items: the Parallella boards draw a fabrication drawing frame on
        it (its face has the board inside its hole, so the outermost faces are the frame and not the board) and the
        BeagleBone Black draws text with closed glyphs far from the board."""
        outline = super()._outline()
        points = [point for tools in self.drill_groups.values() for tool in tools.values() for point in tool['drills']]
        lines = self._follows.get('OUTLINE', [])
        if not points or not lines or outline.is_empty:
            return outline
        tree = STRtree(points)
        holding = [face for face in polygonize(unary_union(lines)) if len(tree.query(face, predicate='contains'))]
        return unary_union(holding) if holding else outline

    def _build_tracks(self):
        """Tracks of a footprint definition (owner word 3 is a 0x2B block: library copy, local coordinates) are in no
        film; their vias are the dummy vias left out in _convert."""
        tracks = self._by_type.get(0x05, [])
        self._by_type[0x05] = [entry for entry in tracks if not self._is_def(self._u32(entry[1] + 12))]
        try:
            super()._build_tracks()
        finally:
            self._by_type[0x05] = tracks

    def _build_rects(self):
        """Rectangles: solid on copper, solder mask and paste mask layers (as the Gerber films show), outlines elsewhere."""
        for t, o, _n in self._by_type.get(0x24, []) + self._by_type.get(0x0E, []):
            cls, sub = self._u8(o + 2), self._u8(o + 3)
            targets = self._layer_targets(cls, sub)
            parent, base, rot_at = (self._u32(o + 12), o + 24, o + 52) if t == 0x24 else (self._u32(o + 12), o + 36,
                                                                                         o + 64)
            if self._is_def(parent) or not targets:
                continue
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
                if cls == C_ETCH:
                    self._routed.add(name)
                if cls == C_ETCH or name.startswith(('SOLDERMASK', 'PASTEMASK')):
                    solid = Polygon(ring[:-1])
                    self._add(name, solid, [line])
                    self._primitives.flash(name, solid, solid.centroid,
                                           'rect' if abs(rot - round(rot / 90) * 90) <= 1e-8 else 'block',
                                           abs(x2 - x1), abs(y2 - y1), rot)
                else:
                    self._add(name, self._stroke(ring, self.HAIRLINE), [line])
                    self._primitives.stroke(name, self._stroke(ring, self.HAIRLINE), line, self.HAIRLINE)

    def _padstack(self, key):
        cached = key in self._padstacks
        stack = super()._padstack(key)
        if not cached:
            # the shape a "shape symbol" component refers to (word 7 of the converted component)
            _t, o, _n = self._need(key, 0x1C)
            base = o + 192
            for i, comp in enumerate(stack['comps']):
                comp['ptr'] = self._u32(base + 36 * i + 28)
        return stack

    def _comp_shape(self, comp, what):
        if comp['type'] in (0x1B, 0x1C):
            raise AllegroBrdParseError('Rounded and chamfered rectangle pads of Allegro 16.x boards are not '
                                       'supported (%s)' % what)
        if comp['type'] == 0x16:
            # a pad drawn as a shape: the filled outline (a 0x28 shape), around the pad origin
            entry = self._blk(comp.get('ptr', 0), 0x28)
            if entry is None:
                raise AllegroBrdParseError('The shape of a shape symbol pad is missing (%s)' % what)
            polygon, _chain = self._shape_polygon(entry[1], comp['ptr'])
            if comp['ox'] or comp['oy']:
                polygon = affine_transform(polygon, (1, 0, 0, 1, comp['ox'], comp['oy']))
            return polygon
        return super()._comp_shape(comp, what)


def is_16x(data):
    """True for the data of an Allegro 16.x board (family 0x0013 of the magic number)."""
    return len(data) >= 4 and _U32.unpack_from(data, 0)[0] >> 16 == 0x0013


def parse_into(owner, data):
    """Read 16.x board data for `owner`, an AllegroBrdParser: the result lands in its public attributes.

    The base reader calls this from parse_bytes when is_16x(data) holds; errors are AllegroBrdParseError.
    """
    reader = AllegroBrd16Parser(app=owner.app, circle_steps=owner.circle_steps)
    try:
        reader.parse_bytes(data)
    finally:
        owner.warnings = list(reader.warnings)
        owner.version = reader.version
    for name in ('layers', 'drill_groups', 'board_outline', 'default_layers', 'copper_layers'):
        setattr(owner, name, getattr(reader, name))
    return owner
