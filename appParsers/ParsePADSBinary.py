# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# PADS native SDB record reader                           #
# MIT Licence                                             #
# ##########################################################

"""Read PADS native SDB 0x2026/0x2027 without an external CAD application.

Format evidence: KiCad d29d059fcde12633ef6821638cd54e6d874dc18a,
pcbnew/pcb_io/pads/{pads_sdb.cpp,pads_binary_parser.cpp,pads_binary.ksy}.
The reference is GPL-2.0-or-later; this reader implements the on-disk field
layouts independently. No upstream implementation or generated code is bundled.
Only 0x2026/0x2027 have local fixture coverage. Decoded records are normalized
into the existing ASCII geometry pipeline. Drill plating is the stored padstack
bit; pours and fonts use its disclosed reconstruction policy, not original CAM.
"""

from collections import deque
import math
import struct
from typing import NoReturn

from appParsers.ParsePADS import PADSParseError


class PADSBinaryReader:
    """Bounds-checked native records and their in-memory geometry adapter."""

    UNIT = 1 / 1500000
    FOOTER = b'{2FE18320-6448-11d1-A412-000000000000}'

    def __init__(self, data):
        self.data = bytes(data)
        self.warnings: list[str] = []
        self.limit = len(self.data) - 42
        if len(self.data) < 52 or self.data[:2] != b'\x00\xff':
            self._fail('missing or truncated header')
        self.version = self._read(2, 'H')[0]
        if self.version not in (0x2026, 0x2027):
            self._fail('unsupported version 0x%04x' % self.version)
        if self.data[-42:-4] != self.FOOTER:
            self._fail('missing footer; file may be truncated')
        self.container_offset = struct.unpack_from('<I', self.data, len(self.data) - 4)[0]
        if not 6 <= self.container_offset <= self.limit:
            self._fail('invalid container back-pointer')
        count = self._read(26)[0]
        if count != 75:
            self._fail('unsupported controller directory count %d' % count)
        self._range(10, count * 16)
        self.sections: list[dict[str, int]] = [
            dict(zip(('count', 'size'), self._read(10 + i * 16, 'II'))) for i in range(count)]
        self._frame()
        self.parameters = self._parameters()
        setup =10 + (count - 1) * 16 + self.sections[2]['count'] * 48
        self._range(setup, 100)
        self.layer_count = self._read(setup + 16)[0]
        if not 1 <= self.layer_count <= 32:
            self._fail('invalid maximum copper layer')
        self.origin = self._read(setup + 60, 'ii')
        self.padstacks = self._padstacks()
        self.decals = self._decals()
        self.placements = self._placements()
        self.layers = self._layers()
        self.nets = self._nets()
        self.holes = self._holes()

    @staticmethod
    def _fail(message) -> NoReturn:
        raise PADSParseError('PADS binary: ' + message)

    def _range(self, offset, size):
        if offset < 0 or size < 0 or offset > self.limit or size > self.limit - offset:
            self._fail('invalid or truncated extent at byte %d (length %d)' % (offset, size))

    def _read(self, offset, fmt='I'):
        self._range(offset, struct.calcsize('<' + fmt))
        return struct.unpack_from('<' + fmt, self.data, offset)

    def _string(self, offset, size):
        self._range(offset, size)
        raw = self.data[offset:offset + size].split(b'\x00', 1)[0]
        try:
            value = raw.decode('cp1252')
        except UnicodeError as error:
            raise PADSParseError('PADS binary: invalid string at byte %d' % offset) from error
        if any(ord(char) < 32 for char in value):
            self._fail('control character in name at byte %d' % offset)
        return value

    def _fixed(self, tag, stride):
        section = self.sections[tag]
        if section['size'] != section['count'] * stride:
            self._fail('invalid section %d record size' % tag)
        return range(section['count'])

    def _frame(self):
        cursor = 6 + len(self.sections) * 16

        def place(tag, size=None, count=None):
            nonlocal cursor
            section = self.sections[tag]
            size = section['size'] if size is None else size
            self._range(cursor, size)
            section.update(offset=cursor, extent=size,
                           records=section['count'] if count is None else count)
            cursor += size

        for tag in range(2, 28):
            place(tag)
        for tag, stride in ((4, 64), (5, 24), (8, 72), (10, 112), (11, 20),
                            (12, 12), (13, 20), (14, 112), (15, 36), (17, 224),
                            (22, 112), (23, 424), (24, 68), (26, 12), (27, 4)):
            self._fixed(tag, stride)
        tags = (65, 66, 45, 46, 47, 48, 41, 42, 74)
        pages = self.sections[26]
        page_count = sum(self.sections[tag]['size'] for tag in tags)
        if page_count > pages['count']:
            self._fail('invalid page descriptor count')
        descriptor = pages['offset'] + (pages['count'] - page_count) * 12
        for tag in tags:
            records = sum(self._read(descriptor + i * 12 + 8)[0]
                          for i in range(self.sections[tag]['size']))
            self.sections[tag]['records'] = records
            descriptor += self.sections[tag]['size'] * 12
        self._fixed(29, 4)
        place(29)
        for tag, stride in ((41, 188), (42, 80), (45, 124), (46, 40), (47, 24), (48, 864)):
            count = self.sections[tag]['records']
            place(tag, count * stride, count)
        place(49)
        rules = self.sections[46]
        live = sum(self._read(rules['offset'] + i * 40)[0] < 0x80000000 and
                   self._read(rules['offset'] + i * 40 + 4)[0] != 0 for i in range(rules['records']))
        self._range(cursor, live * 4)
        cursor += live * 4
        for tag in (51, 50, *range(52, 65)):
            place(tag)
        for tag, stride in ((65, 28), (66, 280)):
            count = self.sections[tag]['records']
            place(tag, count * stride, count)
        for tag in (67, 68):
            place(tag)
        place(69, self.sections[69]['size'] + 12)
        place(70, 4)
        if self.sections[71]['size'] < 4:
            self._fail('invalid display preference extent')
        place(71, self.sections[71]['size'] - 4)
        for tag in (72, 73):
            place(tag)
        count = self.sections[74]['records']
        place(74, count * 276, count)
        if cursor + 15 > self.container_offset or self.data[cursor + 4:cursor + 13] != b'\x08PowerSYS':
            self._fail('invalid post-layer database framing')

    def _parameters(self):
        """Global pour thermal and teardrop settings of the board-parameter block (section 3)."""
        base = self.sections[3]['offset'] + self.sections[1]['size'] - 12
        self._range(base, 1396 + 4)
        if self._read(base + 988, 'i')[0] != 8:
            self._fail('invalid board-parameter block')
        # +1396 is VIAPSHAPE (via-pattern spacing), not a clearance: the rules live in section 41 (_rules).
        return dict(thermal_line=self._read(base + 992, 'i')[0], thermal_smd=self._read(base + 1164, 'i')[0],
                    thermal_flags=self._read(base + 1260)[0], starved_clear=self._read(base + 1340, 'i')[0],
                    starved_spokes=self._read(base + 1344, 'i')[0], teardrop=self._read(base + 844)[0])

    def _padstacks(self):
        section = self.sections[4]
        base = section['offset'] - 28
        self._range(base, section['size'])
        layer_base = base + section['size'] + 24
        self._range(layer_base, self.sections[5]['size'])
        result = []
        for index in range(section['count']):
            offset = base + index * 64
            if self._read(offset + 56, 'B')[0] != 0xfe:
                result.append(None)
                continue
            width, drill, length = self._read(offset + 28, 'iii')
            start = self._read(offset + 52)[0]
            shape, count, first, last = self._read(offset + 57, 'BBBB')
            if min(width, drill, length) < 0:
                self._fail('negative padstack dimensions')
            if count and (start >= self.sections[5]['count'] or count > self.sections[5]['count'] - start):
                self._fail('invalid padstack layer range')
            layers = []
            for layer in range(start, start + count):
                geometry = layer_base + layer * 24
                # v2027's selector/shape belong to the successor, including the retained tail.
                metadata = geometry + (24 if self.version == 0x2027 else 0)
                selector, layer_shape = self._read(metadata, 'BB')
                a, b, spoke, _, angle = self._read(geometry + 4, 'iiIii')
                if min(a, b) < 0:
                    self._fail('negative pad layer dimensions')
                layers.append(dict(selector=selector, shape=layer_shape, width=a * self.UNIT,
                                   length=b * self.UNIT, spoke=(spoke & 0xffffff) * self.UNIT,
                                   spokes=spoke >> 24, angle=angle / 1800000))
            # The next record's first word carries this stack's flags (the last one reads the section-5 header):
            # bit 1 = non-plated, bit 3 = slot data follows.
            carrier = self._read(offset + 64)[0]
            slot_length, slot_angle = 0, 0
            if drill and index + 1 < section['count'] and carrier & 8:
                slot_length, slot_angle = self._read(offset + 72, 'ii')
                if slot_length < drill:
                    self._fail('invalid slot length')
            result.append(dict(width=width * self.UNIT, drill=drill * self.UNIT,
                               length=length * self.UNIT, shape=shape,
                               angle=self._read(offset + 48, 'i')[0] / 1800000,
                               corner=self._read(offset + 44, 'i')[0] * self.UNIT,
                               span=(first, last), layers=layers, plated=not carrier & 2,
                               slot_length=slot_length * self.UNIT, slot_angle=slot_angle / 1800000))
        return result

    def _decals(self):
        section = self.sections[14]
        terminals = self.sections[15]
        pair_base = terminals['offset'] + terminals['size']
        pair_limit = self.sections[16]['size'] // 8
        if self.sections[16]['size'] % 8:
            self._fail('invalid padstack-pair extent')
        result = []
        for index in range(section['count']):
            base = section['offset'] + index * 112
            name = self._string(base, 41)
            if self._read(base + 64, 'H')[0] != 0xfffe or not name:
                self._fail('invalid decal descriptor')
            start, count = self._read(base + 68, 'II')
            stack_start = self._read(base + 44)[0]
            stack_count = self._read(base + 88)[0]
            if start > terminals['count'] or count > terminals['count'] - start:
                self._fail('invalid decal terminal range')
            if stack_start > pair_limit or stack_count > pair_limit - stack_start:
                self._fail('invalid decal padstack-pair range')
            stacks = {}
            for pair in range(stack_start, stack_start + stack_count):
                ordinal, stack = self._read(pair_base + pair * 8, 'II')
                if ordinal > count or stack >= len(self.padstacks) or self.padstacks[stack] is None:
                    self._fail('invalid decal padstack reference')
                if ordinal in stacks:
                    self._fail('duplicate decal padstack ordinal')
                stacks[ordinal] = stack
            pins = []
            for pin in range(start, start + count):
                offset = terminals['offset'] + pin * 36
                x, y = self._read(offset, 'ii')
                pins.append(dict(position=(x * self.UNIT, y * self.UNIT),
                                 name=self._string(offset + 20, 4)))
            result.append(dict(name=name, terminals=pins, stacks=stacks))
        if not result or result[0]['name'] != 'JMPVIA_AAAAA':
            self._fail('invalid decal-table anchor')
        return result

    def _placements(self):
        section = self.sections[22]
        types = self.sections[17]
        result, names = [], set()
        for index in range(section['count']):
            base = section['offset'] - 44 + index * 112
            successor = base + 112
            name = self._string(base + 44, 16)
            if not name or name in names:
                self._fail('invalid or duplicate placement reference')
            names.add(name)
            part_type = self._read(successor + 4)[0]
            if part_type >= types['count']:
                self._fail('invalid placement parttype reference')
            alternate = self._read(successor + 17, 'B')[0]
            type_base = types['offset'] - 44 + part_type * 224
            if alternate >= (224 - 96) // 8:
                self._fail('invalid placement alternate decal')
            decal, repeat = self._read(type_base + 96 + alternate * 8, 'ii')
            if decal < 0 or decal != repeat:
                self._fail('invalid parttype decal pair')
            if self._read(successor + 16, 'B')[0] & 0x10:
                decal = self._read(successor + 8)[0]
            if not 0 <= decal < len(self.decals):
                self._fail('invalid placement decal reference')
            x, y, angle = self._read(base + 60, 'iii')
            result.append(dict(reference=name, decal=decal, part_type=part_type,
                               position=((x - self.origin[0]) * self.UNIT,
                                         (y - self.origin[1]) * self.UNIT),
                               angle=angle / 1800000,
                               mirrored=bool(self._read(base + 72, 'B')[0] & 1)))
        return result

    def _layers(self):
        self._fixed(69, 152)
        section = self.sections[69]
        result = []
        for index in range(section['count']):
            base = section['offset'] + 12 + index * 152
            kind = self._read(base - 4, 'i')[0] if index else 0
            if not 0 <= kind <= 6:
                self._fail('unknown layer function %d' % kind)
            result.append(dict(number=index, name=self._string(base, 24), kind=kind,
                               routing_direction=self._read(base + 32, 'i')[0]))
        return result

    def _nets(self):
        section = self.sections[23]
        return [self._string(section['offset'] - 44 + index * 424 + 76, 48)
                for index in range(section['count'])]

    def _holes(self):
        result = []
        for part in self.placements:
            decal = self.decals[part['decal']]
            for ordinal, pin in enumerate(decal['terminals'], 1):
                stack_index = decal['stacks'].get(ordinal, decal['stacks'].get(0, 0))
                if stack_index >= len(self.padstacks) or self.padstacks[stack_index] is None:
                    self._fail('missing terminal padstack')
                stack = self.padstacks[stack_index]
                if not stack['drill']:
                    continue
                angle = math.radians(part['angle'])
                x, y = pin['position']
                x, y = x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)
                if part['mirrored']:
                    x = -x
                result.append(dict(reference=part['reference'], pin=pin['name'],
                                   position=(x + part['position'][0], y + part['position'][1]),
                                   diameter=stack['drill'], plated=stack['plated'],
                                   slot_length=stack['slot_length'],
                                   slot_angle=(180 - part['angle'] - stack['slot_angle']
                                               if part['mirrored'] else part['angle'] + stack['slot_angle']),
                                   padstack=stack_index))
        section = self.sections[60]
        self._fixed(60, 64)
        seen = {}
        for index in range(section['count']):
            offset = section['offset'] + index * 64
            kind = self._read(offset + 27, 'B')[0]
            if kind != 0x0e:
                continue
            marker, flags = self._read(offset + 31, 'BB')
            if marker != 0x17 or not flags & 2:
                self._fail('invalid via record')
            x, y = self._read(offset, 'ii')
            decal_index = self._read(offset + 24, 'B')[0]
            if decal_index >= len(self.decals):
                self._fail('invalid via decal reference')
            stacks = self.decals[decal_index]['stacks']
            stack_index = stacks.get(1, stacks.get(0))
            if stack_index is None:
                self._fail('missing via padstack')
            stack = self.padstacks[stack_index]
            if not stack['drill']:
                self._fail('via has no drill diameter')
            if (x, y) in seen:
                if seen[x, y] != stack_index:
                    self._fail('conflicting co-located via padstacks')
                continue
            seen[x, y] = stack_index
            result.append(dict(reference=None, pin=None,
                               position=((x - self.origin[0]) * self.UNIT,
                                         (y - self.origin[1]) * self.UNIT),
                               diameter=stack['drill'], plated=stack['plated'],
                               slot_length=stack['slot_length'], slot_angle=stack['slot_angle'],
                               padstack=stack_index))
        return result

    def _ring(self, tag, rotation, stride, index, field, fmt='I'):
        section = self.sections[tag]
        if index < 0 or index >= section['count'] or field < 0 or field + struct.calcsize('<' + fmt) > stride:
            self._fail('invalid circular record reference in section %d' % tag)
        raw = bytes(self.data[section['offset'] + (rotation + index * stride + field + byte) % section['size']]
                    for byte in range(struct.calcsize('<' + fmt)))
        return struct.unpack('<' + fmt, raw)

    def _point(self, x, y):
        return (x - self.origin[0]) * self.UNIT, (y - self.origin[1]) * self.UNIT

    def _edges(self):
        """Section-24 pin pairs ((part, pin), (part, pin)) in file order; each is one routed connection."""
        base = self.sections[24]['offset'] - 36
        result = []
        for index in self._fixed(24, 68):
            offset = base + index * 68
            a, b = self._read(offset + 60, 'II')
            pa, pb = self._read(offset + 68, 'II')
            for part, pin in ((a, pa), (b, pb)):
                if part >= len(self.placements) or not 1 <= pin <= len(self.decals[self.placements[part]['decal']]['terminals']):
                    self._fail('invalid net terminal reference')
            result.append(((a, pa), (b, pb)))
        return result

    def _pin_label(self, part, pin):
        placement = self.placements[part]
        return '%s.%s' % (placement['reference'], self.decals[placement['decal']]['terminals'][pin - 1]['name'] or str(pin))

    def _rules(self):
        """The global clearance record of section 41: 39 named distances in BASIC units."""
        names = ('TRACK_TO_TRACK VIA_TO_TRACK VIA_TO_VIA PAD_TO_TRACK PAD_TO_VIA PAD_TO_PAD SMD_TO_TRACK SMD_TO_VIA '
                 'SMD_TO_PAD SMD_TO_SMD COPPER_TO_TRACK COPPER_TO_VIA COPPER_TO_PAD COPPER_TO_SMD COPPER_TO_COPPER '
                 'TEXT_TO_TRACK TEXT_TO_VIA TEXT_TO_PAD TEXT_TO_SMD OUTLINE_TO_TRACK OUTLINE_TO_VIA OUTLINE_TO_PAD '
                 'OUTLINE_TO_SMD OUTLINE_TO_COPPER DRILL_TO_TRACK DRILL_TO_VIA DRILL_TO_PAD DRILL_TO_SMD '
                 'DRILL_TO_COPPER SAME_NET_SMD_TO_VIA SAME_NET_SMD_TO_CRN SAME_NET_VIA_TO_VIA SAME_NET_PAD_TO_CRN '
                 'MIN_TRACK_WIDTH REC_TRACK_WIDTH MAX_TRACK_WIDTH DRILL_TO_DRILL BODY_TO_BODY').split()
        section = self.sections[41]
        globals_ = []
        for index in range(section['records']):
            offset = section['offset'] + index * 188
            selector = self._read(offset)[0]
            if selector:
                self._fail('layer-specific clearance rules are unproven')
            globals_.append(offset)
        if len(globals_) != 1:
            self._fail('expected exactly one global clearance record, found %d' % len(globals_))
        values = list(self._read(globals_[0] + 12, '38i')) + [self._read(globals_[0] + 184, 'i')[0]]
        return list(zip(names + ['SAME_NET_TRACK_TO_CRN'], values))

    def _connections(self):
        """Resolve terminal ownership from the connection graph and its net anchors."""
        parent = {}

        def root(pin):
            parent.setdefault(pin, pin)
            trail = pin
            while parent[pin] != pin:
                pin = parent[pin]
            while parent[trail] != trail:
                previous = parent[trail]
                parent[trail] = pin
                trail = previous
            return pin

        for first, second in self._edges():
            parent[root(first)] = root(second)
        owners = {}
        section = self.sections[23]
        for index, name in enumerate(self.nets):
            part, pin = self._read(section['offset'] - 44 + index * 424 + 64, 'II')
            if not pin or not name or name == '___Unassigned_Obstacles_':
                continue
            if part >= len(self.placements) or pin > len(self.decals[self.placements[part]['decal']]['terminals']):
                self._fail('invalid net anchor')
            key = root((part, pin))
            previous = owners.get(key)
            if previous and previous != name and not previous.startswith('$$$') and not name.startswith('$$$'):
                self._fail('conflicting terminal nets')
            if not previous or previous.startswith('$$$'):
                owners[key] = name
        return {pin: owners[root(pin)] for pin in parent if root(pin) in owners}

    def _junction_nets(self):
        section = self.sections[49]
        cursor, end = section['offset'], section['offset'] + section['size']

        def take():
            nonlocal cursor
            if cursor + 4 > end:
                self._fail('truncated net relationships')
            value = self._read(cursor)[0]
            cursor += 4
            return value

        result, routes = {}, {}
        for net in (name for name in self.nets if name and name != '___Unassigned_Obstacles_'):
            for direction in range(2):
                count = take()
                if count > (end - cursor) // 8:
                    self._fail('invalid net relationship count')
                for _ in range(count):
                    owner, members = take(), take()
                    group = []
                    tag, limit = ((0x3c, self.sections[60]['count']) if direction == 0 else
                                  (0x18, self.sections[24]['count']))
                    if owner >> 24 != tag or owner & 0xffffff >= limit or members > (end - cursor) // 4:
                        self._fail('invalid relationship owner')
                    if direction == 0:
                        ordinal = owner & 0xffffff
                        if ordinal in result and result[ordinal] != net:
                            self._fail('conflicting junction nets')
                        result[ordinal] = net
                    for _ in range(members):
                        member = take()
                        tag, limit = ((0x18, self.sections[24]['count']) if direction == 0 else
                                      (0x3c, self.sections[60]['count']))
                        if member >> 24 != tag or member & 0xffffff >= limit:
                            self._fail('invalid relationship member')
                        group.append(member & 0xffffff)
                    if direction == 1:
                        if owner & 0xffffff in routes:
                            self._fail('duplicate connection relationship')
                        # connection ordinal -> (net, junctions its copper passes, both pin junctions included)
                        routes[owner & 0xffffff] = (net, group)
        if cursor != end:
            self._fail('invalid relationship extent')
        return result, routes

    def _tracks(self, junction_nets):
        self._fixed(61, 12)
        self._fixed(62, 48)
        self._fixed(63, 2)
        self._fixed(64, 12)
        pages = self._read(self.sections[25]['offset'] + 180, '4H')
        start, count = sum(pages[:3]), pages[3]
        if not count or start + count > self.sections[26]['count']:
            self._fail('invalid route allocator pages')
        handles = []
        for page in range(count):
            offset = self.sections[26]['offset'] + (start + page) * 12
            size = (self._read(offset + 20)[0] if page + 1 < count else
                    self.sections[61]['count'] - len(handles))
            if size < 0 or size > self.sections[61]['count'] - len(handles):
                self._fail('invalid route allocator page size')
            base = self._read(offset)[0]
            handles.extend(base + i * 56 for i in range(size))
        ordinals = {handle: index for index, handle in enumerate(handles)}
        if len(ordinals) != self.sections[61]['count']:
            self._fail('invalid or duplicate route handles')
        links = {handle: [] for handle in handles}
        for handle, ordinal in ordinals.items():
            for parent in self._read(self.sections[61]['offset'] + ordinal * 12, 'II'):
                if parent in links:
                    links[parent].append(handle)
        seeds = {}
        for ordinal, net in junction_nets.items():
            handle = self._read(self.sections[60]['offset'] + ordinal * 64 + 8)[0]
            if handle:
                seeds.setdefault(handle, set()).add(net)
        permutation = list(self._read(self.sections[63]['offset'], '%dH' % self.sections[63]['count']))
        if sorted(permutation) != list(range(len(permutation))) or len(permutation) != self.sections[27]['count']:
            self._fail('invalid route layer permutation')
        objects, cursor = [], 0
        for index, layer in enumerate(permutation):
            count = self._read(self.sections[27]['offset'] + index * 4)[0]
            if count > self.sections[29]['count'] - cursor:
                self._fail('invalid layer route count')
            for _ in range(count):
                handle = self._read(self.sections[29]['offset'] + cursor * 4)[0]
                cursor += 1
                if not handle:
                    continue
                if handle not in ordinals:
                    self._fail('unknown route handle')
                kind = self._read(self.sections[61]['offset'] + ordinals[handle] * 12 + 8)[0]
                if kind & 0x800000:
                    objects.append((layer + 1, handle))
        count = self.sections[62]['count']
        if cursor != self.sections[29]['count'] or len(objects) != count:
            self._fail('invalid route object mapping')
        result, cursor = [], 0
        for sequence, (layer, handle) in enumerate(objects):
            index = (sequence - 1) % count
            width = self._ring(62, 32, 48, index, 20, 'i')[0] * 4 * self.UNIT
            style, cells = self._ring(62, 32, 48, index, 32, 'II')
            if width < 0 or cells > self.sections[64]['count'] - cursor:
                self._fail('invalid route width or cell range')
            if layer > self.layer_count or self.layers[layer]['routing_direction'] not in range(5):
                self._fail('invalid route layer')
            points = []
            for cell in range(cursor, cursor + cells):
                first, fixed, last = self._read(self.sections[64]['offset'] + cell * 12, 'iii')
                pair = ((first, fixed), (last, fixed)) if self.layers[layer]['routing_direction'] == 1 else (
                    (fixed, first), (fixed, last))
                for point in pair:
                    point = self._point(*point)
                    if not points or point != points[-1]:
                        points.append(point)
            cursor += cells
            if width == 0:
                continue  # Zero-width route objects are airwires, never copper.
            if style & 0x1100:
                self._fail('jumper/special routed copper is unsupported')
            visited, frontier, nets = {handle}, [handle], set()
            while frontier and not nets:
                following = []
                for node in frontier:
                    nets.update(seeds.get(node, ()))
                    for child in links[node]:
                        if child not in visited:
                            visited.add(child)
                            following.append(child)
                frontier = following
            if len(nets) != 1:
                self._fail('missing or conflicting routed copper net')
            if len(points) > 1:
                result.append((layer, nets.pop(), width, points, style))
        if cursor != self.sections[64]['count']:
            self._fail('unused route cells')
        return result

    def _junctions(self):
        """Section-60 junctions as (point, type, part or via decal, pin number); type 0x16 pin, 0x0e via."""
        result = []
        for index in range(self.sections[60]['count']):
            offset = self.sections[60]['offset'] + index * 64
            result.append((self._point(*self._read(offset, 'ii')), self._read(offset + 27, 'B')[0],
                           self._read(offset + 24, 'B')[0], self._read(offset + 28, 'H')[0]))
        return result

    def _walks(self, tracks, connections, routes, teardrop):
        """Route rows of every connection that has copper, as the pin-pair paths of the ASCII format.

        Section 24 gives the pin pair, section 49 (direction 1) the junctions its copper passes. The objects whose
        both ends are such junctions form a graph: a breadth-first main path joins the pins and every other
        incident object is walked out and back as a spur. An object shared by several connections repeats in
        each of them, as in the ASCII export. Copper that no walk reaches is an error, never dropped.
        """
        junctions = self._junctions()
        kinds, pins = {}, {}
        for point, kind, part, pin in junctions:
            kinds.setdefault(point, set()).add(kind)
            if kind == 0x16:
                pins[(part, pin)] = point
        covered, result = set(), []
        for index, (first, second) in enumerate(self._edges()):
            net, members = routes.get(index, (None, ()))
            reach = {junctions[member][0] for member in members}
            adjacent = {}
            for number, track in enumerate(tracks):
                points = track[3]
                if points[0] in reach and points[-1] in reach:
                    adjacent.setdefault(points[0], []).append((number, points[-1]))
                    adjacent.setdefault(points[-1], []).append((number, points[0]))
            if not adjacent:
                continue  # No copper: an airwire.
            start, goal = pins.get(first), pins.get(second)
            if start is None or goal is None or start == goal:
                self._fail('connection %d has copper but no pin junctions' % index)
            parent, queue = {start: None}, deque([start])
            while queue:
                vertex = queue.popleft()
                for number, other in adjacent.get(vertex, ()):
                    if other not in parent:
                        parent[other] = (vertex, number)
                        queue.append(other)
            if goal not in parent:
                self._fail('connection %d: pin %s is unreachable through its copper'
                           % (index, self._pin_label(*second)))
            main, vertex = [], goal
            while parent[vertex]:
                before, number = parent[vertex]
                main.append((before, number, vertex))
                vertex = before
            main.reverse()
            on_main = {start} | {vertex for _, _, vertex in main}
            used = {number for _, number, _ in main}
            sequence = []

            def spur(origin):
                stack = [(iter(adjacent.get(origin, ())), origin, None)]
                while stack:
                    items, at, back = stack[-1]
                    for number, other in items:
                        if number in used:
                            continue
                        used.add(number)
                        sequence.append((number, at, other))
                        if other in on_main:
                            sequence.append((number, other, at))
                        else:
                            stack.append((iter(adjacent.get(other, ())), other, (number, other, at)))
                            break
                    else:
                        stack.pop()
                        if back:
                            sequence.append(back)
            for before, number, vertex in main:
                spur(before)
                sequence.append((number, before, vertex))
            if connections.get(first) != net or connections.get(second) != net or any(
                    tracks[number][1] != net for number, _, _ in sequence):
                self._fail('connection %d: route and pin nets disagree' % index)
            covered.update(number for number, _, _ in sequence)
            rows = []
            for number, before, vertex in sequence:
                points = tracks[number][3]
                points = points if points[0] == before else points[::-1]
                rows.extend((point, tracks[number][0], tracks[number][2], number, before, vertex, i == 0)
                            for i, point in enumerate(points[:-1]))
            number, before, vertex = sequence[-1]
            rows.append((vertex, 65, tracks[number][2], number, before, vertex, True))

            def suppressed(row, point, ending):
                # Style 0x200 / 0x400 suppress the teardrop at the object's first / last point.
                points, style = tracks[row[3]][3], tracks[row[3]][4]
                return (point != (row[5] if ending else row[4]) or
                        (points[0] == point and style & 0x200) or
                        (points[-1] == point and points[0] != point and style & 0x400))
            text = []
            for at, row in enumerate(rows):
                point = row[0]
                thermal = row[6] and kinds.get(point, set()) & {0x16, 0x0e}
                flags = 0x100 if thermal else 0
                no_p = at == 0 or suppressed(rows[at - 1], point, True)
                no_n = at == len(rows) - 1 or suppressed(row, point, False)
                flags |= (0x400 if no_p else 0) | (0x200 if no_n else 0)
                line = '%.12g %.12g %d %.12g %d' % (*point, row[1], row[2], flags)
                if thermal:
                    line += ' THERMAL'
                drops = [side + ' %d %d' % teardrop for side, skip in (('P', no_p), ('N', no_n)) if teardrop and not skip]
                text.append(line + (' TEARDROP ' + ' '.join(drops) if drops else ''))
            result.append((net, self._pin_label(*first), self._pin_label(*second), text))
        if covered != set(range(len(tracks))):
            self._fail('%d routed copper objects belong to no pin-pair path' % (len(tracks) - len(covered)))
        return result

    def _drawing_rows(self):
        """Read owner-relative drawing/decal geometry, including circular array tails."""
        result = {}
        kinds = {0: 'OPEN', 1: 'CLOSED', 2: 'CIRCLE', 3: 'COPOPN', 4: 'COPCLS',
                 5: 'COPCUT', 6: 'COPCIR', 7: 'COPCCO', 10: 'KPTCLS', 11: 'KPTCIR', 19: 'TAG'}
        for owner in range(self.sections[10]['count']):
            name = bytes(self._ring(10, 68, 112, owner, 44, '40B')).split(b'\0')[0].decode('cp1252')
            lag = (owner + 1) % self.sections[10]['count']
            piece, vertex, arc = self._ring(10, 68, 112, lag, 8, 'iii')
            count, kind = self._ring(10, 68, 112, lag, 24, 'II')
            kind &= 0xffff
            if piece < 0 or count > self.sections[11]['count'] - piece or vertex < 0:
                self._fail('invalid drawing piece range')
            rows = []
            for index in range(piece, piece + count):
                rotation = self.sections[11]['size'] - 12
                width, corners = self._ring(11, rotation, 20, index, 12, 'ii')
                shape, layer = self._ring(11, rotation, 20, (index + 1) % self.sections[11]['count'], 0, 'BB')
                if shape not in kinds or corners < 0 or corners > self.sections[12]['count'] - vertex or width < 0:
                    self._fail('unsupported drawing shape or invalid vertex range')
                if shape == 19:
                    if corners or layer not in (0, 1):
                        self._fail('invalid drawing group tag')
                elif corners < 2:
                    self._fail('drawing has fewer than two vertices')
                rows.append('%s %d %.12g 0 %d' % (kinds[shape], corners, width * self.UNIT, layer))
                vertices = [self._read(self.sections[12]['offset'] + i * 12, 'iii')
                            for i in range(vertex, vertex + corners)]
                vertex += corners
                for ordinal, (x, y, attr) in enumerate(vertices):
                    row = '%.12g %.12g' % (x * self.UNIT, y * self.UNIT)
                    if attr >= 0:
                        if arc < 0 or arc + attr >= self.sections[13]['count'] or ordinal + 1 >= len(vertices):
                            self._fail('invalid drawing arc reference')
                        xmin, ymin, xmax, ymax = self._read(self.sections[13]['offset'] + (arc + attr) * 20, 'iiii')
                        cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
                        nx, ny = vertices[ordinal + 1][:2]
                        sweep = (math.degrees(math.atan2(ny - cy, nx - cx) - math.atan2(y - cy, x - cx)) + 180) % 360 - 180
                        row += ' 0 %.12g %.12g %.12g %.12g %.12g' % (
                            sweep, xmin * self.UNIT, ymin * self.UNIT, xmax * self.UNIT, ymax * self.UNIT)
                    rows.append(row)
            origin = self._ring(10, 68, 112, owner, 88, 'ii')
            if name in result:
                self._fail('duplicate drawing owner')
            result[name] = kind, self._point(*origin), count, rows
        return result

    def _stack_rows(self, index):
        stack = self.padstacks[index]
        if stack['span'] not in ((0, 0), (1, self.layer_count)):
            self._fail('native blind/buried drill spans are not supported')

        def shape(width, length, code):
            names = {0: 'OF', 1: 'RF', 2: 'R', 3: 'S'}
            if code not in names:
                self._fail('unsupported signal pad shape %d' % code)
            value = '%.12g %s' % (width, names[code])
            if code in (0, 1):
                value += ' %.12g %.12g 0' % (stack['angle'], length or width)
            if code in (1, 3):
                value += ' %.12g' % stack['corner']
            return value

        default = shape(stack['width'], stack['length'], stack['shape'])
        layers, alternates = {-2: default, -1: default, 0: default}, []
        for layer in stack['layers']:
            selector, code = layer['selector'], layer['shape']
            target = -1 if selector == 255 else (0 if selector == 0 else selector + (self.version != 0x2027))
            if target not in layers and not 1 <= target <= 250:
                self._fail('invalid pad layer selector')
            if code in (6, 7, 8, 9):
                # Thermal/antipad alternatives aren't signal copper apertures; the reader keeps them as rows.
                if self.version != 0x2027 or code == 9:
                    self._fail('unproven native thermal/antipad pad row layout (shape %d, version 0x%04x)' % (
                        code, self.version))
                if code == 8:
                    alternates.append((target, '%.12g RA' % layer['width']))
                    continue
                if layer['spokes'] != 4:
                    self._fail('unproven native thermal spoke count')
                alternates.append((target, '%.12g %s %.12g %.12g %.12g %d' % (
                    layer['width'], 'RT' if code == 6 else 'ST', layer['angle'], layer['length'],
                    layer['spoke'], layer['spokes'])))
                continue
            layers[target] = shape(layer['width'], layer['length'], code)
        layers[-2] += ' %.12g %s' % (stack['drill'], 'P' if stack['plated'] else 'N')
        if stack['slot_length']:
            layers[-2] += ' %.12g %.12g 0' % (stack['slot_angle'], stack['slot_length'])
        rows = ['%d %s' % item for item in layers.items()]
        for target, row in alternates:
            if target not in layers:
                self._fail('native thermal/antipad row on a layer without a signal pad')
            rows.append('%d %s' % (target, row))
        return rows

    HJUST = {0: 'LEFT', 1: 'CENTER'}
    VJUST = {0: 'DOWN', 1: 'CENTER', 2: 'UP'}
    ATTRIBUTES = {2: 'Ref.Des.', 3: 'Part Type'}

    def _text_record(self, index):
        """Decode one section-8 text record; the association word links chains and names the owner."""
        section = self.sections[8]
        if not 0 <= index < section['count']:
            self._fail('invalid text record reference')
        geometry = section['offset'] + index * 72
        height, width, x, y, angle, mirrored = self._read(geometry, 'iiiiii')
        marker, string, association = self._read(geometry + 40, 'III')
        presentation, font = self._read(geometry + 60, 'II')
        record = dict(height=height, width=width, x=x, y=y, angle=angle, mirrored=bool(mirrored),
                      string=string, association=association, font=font, layer=presentation & 255,
                      kind=(presentation >> 16) & 255, hjust=(presentation >> 24) & 15,
                      vjust=presentation >> 28)
        if marker & 0xffff != 0xfffe:
            self._fail('invalid text record marker')
        if height <= 0 or width <= 0:
            self._fail('invalid text dimensions')
        # Only the justification codes and flag bits of the fixtures are decoded, never guessed.
        if record['hjust'] not in self.HJUST or record['vjust'] not in self.VJUST or record['kind'] & ~0x2f:
            self._fail('unsupported text justification or flags')
        return record

    def _text_fields(self, record, point):
        return '%.12g %.12g %.12g %d %.12g %.12g %s %s %s' % (
            *point, record['angle'] / 1800000, record['layer'], record['height'] * self.UNIT,
            record['width'] * self.UNIT, 'M' if record['mirrored'] else 'N',
            self.HJUST[record['hjust']], self.VJUST[record['vjust']])

    def _font_line(self, word):
        fonts = self.sections[73]
        if fonts['size'] != fonts['count'] * 52 or word >> 24 != 73 or word & 0xffffff >= fonts['count']:
            self._fail('invalid text font reference')
        return 'Regular ' + self._string(fonts['offset'] + (word & 0xffffff) * 52 + 8, 32)

    def _label_chain(self, head, tag, owner):
        """Attribute labels of a decal (tag 0x0e) or a placement (tag 0x16) as three-line records."""
        rows, seen, index = [], set(), head
        while True:
            if index in seen:
                self._fail('cyclic label chain')
            seen.add(index)
            record = self._text_record(index)
            attribute = self.ATTRIBUTES.get(record['kind'] & 7)
            if attribute is None:
                self._fail('unsupported label attribute %d' % (record['kind'] & 7))
            rows.extend(['%s %s%s' % ('VALUE' if record['kind'] & 0x20 else 'NONE',
                                      self._text_fields(record, (record['x'] * self.UNIT, record['y'] * self.UNIT)),
                                      ' ORTHO' if record['kind'] & 8 else ''),
                         self._font_line(record['font']), attribute])
            link = record['association']
            if link >> 24 == 8:
                index = link & 0xffffff
            elif link == (tag << 24) | owner:
                return rows
            else:
                self._fail('label chain ends at a different owner')

    def _owner_labels(self, section, stride, shift, head_offset, tag):
        result = []
        for owner in range(self.sections[section]['count']):
            head = self._read(self.sections[section]['offset'] - shift + owner * stride + head_offset, 'i')[0]
            result.append(self._label_chain(head, tag, owner) if head >= 0 else [])
        return result

    def _decal_texts(self):
        """Free text of decals: chains that end at a drawing-owner row, whose head only the links give."""
        base = self.sections[8]['offset']
        previous, ends = {}, {}
        for index in range(self.sections[8]['count']):
            link = self._read(base + index * 72 + 48)[0]
            if link >> 24 == 8:
                previous.setdefault(link & 0xffffff, []).append(index)
            elif link >> 24 == 0x0a:
                ends.setdefault(link & 0xffffff, []).append(index)
        names = {}
        for index, decal in enumerate(self.decals):
            row = self._read(self.sections[14]['offset'] + index * 112 + 76)[0]
            names.setdefault(row, []).append(index)
        result, skipped = {}, 0
        for row, indexes in ends.items():
            chain = [indexes[0]]
            while len(previous.get(chain[-1], ())) == 1 and previous[chain[-1]][0] not in chain:
                chain.append(previous[chain[-1]][0])
            owners = names.get(row, [])
            named = (row < self.sections[10]['count'] and len(owners) == 1 and
                     self._string(self.sections[10]['offset'] + row * 112, 41) == self.decals[owners[0]]['name'])
            if len(indexes) != 1 or previous.get(chain[-1]) or not named or owners[0] in result:
                skipped += len(chain) + len(indexes) - 1
                continue
            rows = []
            for index in reversed(chain):
                record = self._text_record(index)
                if record['kind'] != 0x20:
                    self._fail('unsupported decal text record')
                rows.extend([self._text_fields(record, (record['x'] * self.UNIT, record['y'] * self.UNIT)) + ' 0',
                             self._font_line(record['font']), self._text_string(record)])
            result[owners[0]] = rows
        if skipped:
            self.warnings.append('%d native PADS decal text records were not imported: '
                                 'their owner decal could not be resolved.' % skipped)
        return result

    def _text_string(self, record):
        pool = self.sections[9]
        if record['string'] >= pool['size']:
            self._fail('invalid free text string reference')
        return self._string(pool['offset'] + record['string'], pool['size'] - record['string'])

    def _part_type_name(self, index):
        name = self._string(self.sections[17]['offset'] - 44 + index * 224 + 44, 41)
        if not name or any(char.isspace() or char in '*@' for char in name) or name.startswith('__'):
            self._fail('part type name cannot be written as a PADS identifier: %r' % name)
        return name

    def as_ascii(self):
        """Normalize decoded records into the existing geometry reader's ASCII dialect.

        This is an in-memory adapter, not a companion-file fallback or external conversion.
        """
        connections = self._connections()
        junctions, routes = self._junction_nets()
        tracks = self._tracks(junctions)
        drawings = self._drawing_rows()
        tokens = [part['reference'] for part in self.placements] + [net for net in self.nets if net]
        tokens.extend(pin['name'] for decal in self.decals for pin in decal['terminals'] if pin['name'])
        if any(any(char.isspace() for char in token) or '*' in token for token in tokens):
            self._fail('whitespace or section delimiters in identifiers are unsupported')
        rules = self._rules()
        settings = self.parameters
        rows = ['!PADS-POWERPCB-V9.5-METRIC!', '*PCB*', 'MAXIMUMLAYER %d' % self.layer_count,
                'TEARDROP %d' % settings['teardrop'],
                'THERLINEWID %.12g' % (settings['thermal_line'] * self.UNIT),
                'THERSMDWID %.12g' % (settings['thermal_smd'] * self.UNIT),
                'THERFLAGS %d' % settings['thermal_flags'], 'STMINCLEAR %d' % settings['starved_clear'],
                'STMINSPOKES %d' % settings['starved_spokes'], '*MISC*']
        # Default teardrops: the global word packs width (10 bits) and length (7 bits); both are 90 in every
        # fixture, so which one is which is unproven and a differing pair is rejected, never guessed.
        word = settings['teardrop']
        teardrop = (word & 0x3ff, (word >> 10) & 0x7f) if word else None
        if teardrop and teardrop[0] != teardrop[1] and tracks:
            self._fail('unproven teardrop width/length order (%d, %d)' % teardrop)
        for layer in self.layers:
            rows.extend(['LAYER %d' % layer['number'], '{', 'LAYER_NAME ' + layer['name'], '}'])
        # The one global clearance rule set, in mm (the header unit): the parser keeps the largest COPPER_TO_*
        # and OUTLINE_TO_COPPER values.
        rows.extend(['RULES_SECTION PARENT', '{', 'DESIGN RULES', '{', 'RULE_SET (1)', '{', 'FOR :', '{', 'DEFAULT :',
                     '}', 'AGAINST :', '{', 'DEFAULT :', '}', 'LAYER 0', 'CLEARANCE_RULE :', '{'])
        rows.extend('%s %.12g' % (name, value * self.UNIT) for name, value in rules)
        rows.extend(['}', '}', '}', '}'])
        decal_labels = self._owner_labels(14, 112, 0, 80, 0x0e)
        part_labels = self._owner_labels(22, 112, 44, 96, 0x16)
        decal_texts = self._decal_texts()
        rows.append('*PARTDECAL*')
        for index, decal in enumerate(self.decals):
            kind, origin, count, graphics = drawings.get(decal['name'], (2, (0, 0), 0, []))
            texts, labels = decal_texts.get(index, []), decal_labels[index]
            rows.append('D%d M 0 0 %d %d %d %d %d' % (index, count, len(decal['terminals']), len(decal['stacks']),
                                                      len(texts) // 3, len(labels) // 3))
            rows.extend(graphics + texts + labels)
            for ordinal, pin in enumerate(decal['terminals'], 1):
                rows.append('T%.12g %.12g 0 0 %s' % (*pin['position'], pin['name'] or str(ordinal)))
            for ordinal, stack in decal['stacks'].items():
                stack_rows = self._stack_rows(stack)
                rows.append('PAD %d %d' % (ordinal, len(stack_rows)))
                rows.extend(stack_rows)
        rows.append('*PARTTYPE*')
        rows.extend('__T%d D%d 0 0 0 0 0' % (index, index) for index in range(len(self.decals)))
        names = {}
        for part in self.placements:
            part['type_name'] = self._part_type_name(part['part_type'])
            names.setdefault(part['type_name'], part['decal'])
        rows.extend('%s D%d 0 0 0 0 0' % item for item in names.items())
        rows.append('*PART*')
        for index, part in enumerate(self.placements):
            labels = part_labels[index]
            # The part type is the label value; "@decal" keeps the placed (alternate or overridden) decal.
            rows.append('%s %s@D%d %.12g %.12g %.12g U %s 0 -1 0 -1 %d' % (
                part['reference'], part['type_name'], part['decal'], *part['position'], part['angle'],
                'M' if part['mirrored'] else 'N', len(labels) // 3))
            rows.extend(labels)
        rows.append('*LINES*')
        for name, (kind, origin, count, graphics) in drawings.items():
            if kind == 2:
                continue
            item = {0: 'LINES', 1: 'BOARD', 3: 'COPPER', 8: 'COPCUT', 10: 'KEEPOUT'}.get(kind)
            if item is None:
                self._fail('unsupported drawing owner kind %d' % kind)
            rows.append('%s %s %.12g %.12g %d 0' % (name, item, *origin, count))
            rows.extend(graphics)
        rows.append('*ROUTE*')
        for (part, pin), net in connections.items():
            name = self._pin_label(part, pin)
            rows.extend(['*SIGNAL* ' + net, '%s %s' % (name, name)])
        for net, first, second, text in self._walks(tracks, connections, routes, teardrop):
            rows.extend(['*SIGNAL* ' + net, '%s %s' % (first, second)] + text)
        # Via pads/drills are emitted once, using their decoded decal rather than route-cell widths.
        via_parts, seen = [], set()
        for index in range(self.sections[60]['count']):
            offset = self.sections[60]['offset'] + index * 64
            if self._read(offset + 27, 'B')[0] != 0x0e:
                continue
            point = self._point(*self._read(offset, 'ii'))
            decal = self._read(offset + 24, 'B')[0]
            if point in seen:
                continue
            seen.add(point)
            net = junctions.get(index)
            if net is None:
                net_index = self._read(offset + 28, 'H')[0]
                if net_index >= len(self.nets):
                    self._fail('invalid via net index')
                net = self.nets[net_index]
            reference = '__via%d' % len(via_parts)
            via_parts.append('%s __T%d %.12g %.12g 0 U N 0 -1 0 -1' % (reference, decal, *point))
            pin = self.decals[decal]['terminals'][0]['name'] or '1'
            rows.extend(['*SIGNAL* ' + net, '%s.%s %s.%s' % (reference, pin, reference, pin)])
        if via_parts:
            at = rows.index('*LINES*')
            rows[at:at] = via_parts
        rows.append('*TEXT*')
        for index in range(self.sections[8]['count']):
            geometry = self.sections[8]['offset'] + index * 72
            marker, string, owner = self._read(geometry + 40, 'III')
            if marker & 0xffff != 0xfffe or owner or (self._read(geometry + 60)[0] >> 16) & 255 != 0x20:
                continue
            record = self._text_record(index)
            text = self._text_string(record)
            if text:
                rows.extend([self._text_fields(record, self._point(record['x'], record['y'])),
                             self._font_line(record['font']), text])
        rows.append('*POUR*')
        for index in self._fixed(52, 88):
            offset = self.sections[52]['offset'] + index * 88
            piece, vertex, arc, net = self._read(offset, 'IIIi')
            if self._read(offset + 87, 'B')[0] != 0x32 or not 0 <= net < len(self.nets):
                self._fail('unsupported pour owner or missing net')
            x, y = self._point(*self._read(offset + 24, 'ii'))
            count = self._read(offset + 64)[0]
            if count > self.sections[53]['count'] - piece:
                self._fail('invalid pour piece range')
            rows.append('POR%d POUROUT %.12g %.12g %d 0 0 %s' % (index, x, y, count, self.nets[net]))
            for ordinal in range(piece, piece + count):
                po = self.sections[53]['offset'] + ordinal * 16
                corners, arcs, width = self._read(po, 'IIi')
                kind, layer = self._read(po + 12, 'BB')
                if arcs:
                    self._fail('native pour arcs require saved artwork')
                if corners > self.sections[54]['count'] - vertex or kind not in (0x32, 0x33):
                    self._fail('unsupported pour piece or invalid vertex range')
                rows.append('%s %d 0 %.12g %d' % ('CIRCLE' if kind == 0x33 else 'POLY', corners, width * self.UNIT, layer))
                rows.extend('%.12g %.12g' % tuple(value * self.UNIT for value in self._read(
                    self.sections[54]['offset'] + i * 8, 'ii')) for i in range(vertex, vertex + corners))
                vertex += corners
        rows.append('*END*')
        return '\n'.join(rows)
