# ##########################################################
# FlatCAM Evo: native Proteus 8 saved PCB geometry reader       #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read LYT artwork, not neighboring CAM exports or a reconstructed netlist.

The supported wire schemas use technology version 1, package versions 847/907.
All positions are signed 1/100000 mm; angles are tenths of a degree. Unknown
manufacturing semantics are diagnosed; unidentified numeric layers are imported
as Mechanical N (GMN) and aren't default layers.
"""

import math
import re
import struct
from typing import NoReturn

import numpy as np
from shapely import LineString, MultiPolygon, Point, Polygon, STRtree, box, make_valid
from shapely.affinity import affine_transform
from shapely.ops import unary_union

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.board_primitives import BoardPrimitives


class ProteusParseError(ValueError):
    """Invalid container, unsupported wire schema, or damaged board data."""


class _Reader:
    """Bounded subreaders keep declared extents authoritative, including strings."""

    def __init__(self, data, member, base=0, string_format='B'):
        self.data = data
        self.member = member
        self.base = base
        self.pos = 0
        self.string_format = string_format

    @property
    def remaining(self):
        return len(self.data) - self.pos

    def fail(self, message) -> NoReturn:
        raise ProteusParseError('%s at byte %d: %s' % (self.member, self.base + self.pos, message))

    def take(self, size):
        if size < 0 or size > self.remaining:
            self.fail('truncated data or invalid declared extent (%d bytes)' % size)
        start = self.pos
        self.pos += size
        return self.data[start:self.pos]

    def unpack(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))

    def number(self, fmt='I'):
        return self.unpack(fmt)[0]

    def count(self, minimum=1):
        value = self.number()
        if value > 1000000 or value * minimum > self.remaining:
            self.fail('invalid element count %d' % value)
        return value

    def text(self, data):
        try:
            return data.decode('cp1252')
        except UnicodeDecodeError as error:
            self.fail('invalid Windows-1252 string: %s' % error)

    def pstring(self):
        return self.text(self.take(self.number(self.string_format)))

    def cstring(self):
        end = self.data.find(b'\0', self.pos)
        if end < 0:
            self.fail('unterminated string')
        return self.text(self.take(end - self.pos + 1)[:-1])

    def line(self):
        end = self.data.find(b'\n', self.pos)
        if end < 0:
            self.fail('unterminated style line')
        return self.text(self.take(end - self.pos + 1)).strip()

    def subreader(self, size):
        base = self.base + self.pos
        return _Reader(self.take(size), self.member, base, self.string_format)

    def block(self):
        return self.subreader(self.number() - 4)

    def expect(self, value):
        if self.take(len(value)) != value:
            self.fail('invalid signature or section marker %r' % value)

    def finish(self):
        if self.remaining:
            self.fail('unexpected bytes inside declared record')


_LENGTH = re.compile(r'^([-+]?(?:\d+(?:\.\d*)?|\.\d+))(mm|th|in)?$')


class Proteus8Parser:
    SIGNATURE = b'PCB LAYOUT FILE\x1a'
    TECHNOLOGY = b'LAYOUT TECHNOLOGY DATA\x1a'
    SCALE = 100000.0
    STRING_FORMAT = 'B'
    TEXT_FORMAT = 'B'
    PACKAGE_VERSION = 847
    GRAPHIC_VERSION = 2
    LIBRARY_MARKER = b'\xfe\xff'
    NET_RULE_BYTES = 35
    TITLE_FROM_ARTWORK = True
    _font_parser = None  # shared system-font table, see _text_geometry

    def __init__(self, app=None):
        self.app = app
        self._options = getattr(app, 'options', None)
        self._primitives = BoardPrimitives(self._options)
        self.layers = {}
        self.drill_groups = {}
        self.warnings = []
        self.default_layers = []
        self.default_drill_groups = []
        self.name = ''
        self.member_name = ''
        self._solids = {}
        self._follows = {}
        self._drills = {}
        self._packages = {}
        self._symbols = {}
        self._layer_names = {}
        self._layer_display_names = {}
        self._layer_extensions = {}
        self._copper = []
        self._unidentified = []
        self._pad_styles = []
        self._style_names = {}
        self._trace_styles = []
        self._trace_names = {}

    def _warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def parse_bytes(self, data, member_name='board.LYT'):
        # A decoder instance represents one board. The facade constructs one per member.
        self.member_name = member_name
        r = _Reader(data, member_name, string_format=self.STRING_FORMAT)
        r.expect(self.SIGNATURE)
        r.take(116)  # v8 fixed layout header: saved viewport and layer visibility fields
        self._technology(r)
        self._libraries(r)
        r.take(7)  # v8 drawing-state header, preceding the typed placed-object list
        self._objects(r)
        self._vias(r)
        self._routes(r)
        self._finish()
        if not self.name:
            self.name = member_name.rsplit('/', 1)[-1].rsplit('.', 1)[0]
            self._warn('Native board title is absent; using member name %s' % self.name)
        return self

    def _technology(self, r):
        r.expect(self.TECHNOLOGY)
        r.number('B')
        if r.number() != 1:
            r.fail('unsupported Proteus technology schema version')
        # Version-1 fixed wire fields: layer settings, fixed-size font presets,
        # routing grids and DRC defaults. Variable tables below are count/extent framed.
        r.take(1088)
        r.pstring()  # default substrate
        r.take(24)
        for _ in range(r.count(12)):
            kind = r.number('B')
            r.pstring()  # material
            r.number()  # thickness
            layer = r.number('B')
            name = r.pstring()
            r.take(8)
            if kind == 1:
                if layer > 15 or layer in self._copper:
                    r.fail('invalid copper stack layer')
                self._copper.append(layer)
                self._remember_layer(layer, name or 'Copper %d' % layer)
            elif kind == 0 and layer in (38, 39):
                # These identities are explicit in both native material stacks.
                label = self._remember_layer(layer, name or ('Top Resist' if layer == 38 else 'Bottom Resist'))
                self._layer_extensions[label] = 'GTS' if layer == 38 else 'GBS'
        if not self._copper:
            r.fail('board has no declared copper stack')
        inner = 0
        for layer in self._copper:
            if layer == 0:
                extension = 'GTL'
            elif layer == 15:
                extension = 'GBL'
            else:
                inner += 1
                extension = 'G%d' % inner  # declared stack order, not the raw numeric layer ID
            self._layer_extensions[self._layer_names[layer]] = extension
        for _ in range(r.count(3)):
            r.pstring()
            r.take(2)  # via span endpoints
        for _ in range(2):  # named graphic styles, then named text styles
            count = r.number('H')
            if count > r.remaining // 6:
                r.fail('invalid named-style count')
            for _ in range(count):
                r.pstring()
                if r.number('B') != 1:
                    r.fail('unsupported named-style schema')
                r.block()
        r.expect(b';PS\n')
        self._pad_styles = self._styles(r)
        self._style_names = {style[0][0]: style for style in self._pad_styles}
        r.expect(b';TS\n')
        for fields, points in self._styles(r):
            if len(fields) != 2 or points:
                r.fail('invalid trace style')
            width = self._length(fields[1])
            if width <= 0:
                r.fail('non-positive trace width')
            self._trace_names[fields[0]] = width
            self._trace_styles.append(width)
        for _ in range(r.count(111)):
            r.pstring()
            r.pstring()
            r.pstring()
            self._netclass_options(r)
        for _ in range(r.count(36)):
            r.pstring()
            r.take(self.NET_RULE_BYTES)
        for layer in (36, 37, 38, 39, 40, 41, 59):
            self._layer(layer)
        self._warn('Technology DRC options are not reconstructed; saved artwork is used without rerunning '
                   'design-rule checks')

    def _netclass_options(self, r):
        r.take(108)  # net-class wire options, not artwork

    def _library_identity(self, r):
        r.take(4)  # timestamp

    @staticmethod
    def _styles(r):
        styles = []
        while True:
            line = r.line()
            if line == '*':
                return styles
            if not line or line.startswith(';'):
                continue
            if line.startswith('+'):
                if not styles:
                    r.fail('style continuation without a style')
                styles[-1][1].append(line[1:].strip())
            else:
                fields = [field.strip() for field in line.split(',')]
                if len(fields) < 2:
                    r.fail('invalid style definition')
                styles.append((fields, []))

    @staticmethod
    def _length(text):
        match = _LENGTH.fullmatch(text)
        if not match:
            raise ProteusParseError('Invalid Proteus dimension %r' % text)
        value = float(match.group(1)) * {None: 1.0, 'mm': 1.0, 'th': 0.0254, 'in': 25.4}[match.group(2)]
        if not math.isfinite(value) or abs(value) > 100000:
            raise ProteusParseError('Out-of-range Proteus dimension %r' % text)
        return value

    def _libraries(self, r):
        r.expect(self.LIBRARY_MARKER)
        while True:
            name = r.pstring()
            if not name:
                break
            self._library_identity(r)
            total = r.block()
            structured_size = total.number()
            body = total.subreader(structured_size - 8)
            if body.unpack('HH') != (1, self.PACKAGE_VERSION):
                body.fail('unsupported package file version (expected %d)' % self.PACKAGE_VERSION)
            count = body.count(41)
            body.take(12)
            streams = [self._graphic_stream(body) for _ in range(18)]
            pads = []
            for _ in range(count):
                body.take(32)
                label = body.take(8).split(b'\0', 1)[0]
                pads.append((label, body.cstring()))
            body.block()  # footprint-local styles; placed pads use the global table
            body.finish()
            total.take(total.number())  # package metadata length excludes its own word
            total.finish()
            if name in self._packages:
                r.fail('duplicate package %s' % name)
            self._packages[name] = (pads, streams)
        r.expect(self.LIBRARY_MARKER)
        while True:
            name = r.pstring()
            if not name:
                break
            self._library_identity(r)
            if name in self._symbols:
                r.fail('duplicate graphic symbol %s' % name)
            self._symbols[name] = self._graphic_stream(r)

    @staticmethod
    def _graphic_stream(r):
        stream = r.block()
        if not stream.remaining:
            return []
        count = stream.count(4)
        if stream.number() != 1:
            stream.fail('unsupported graphic-stream version')
        records = []
        for _ in range(count):
            record = stream.block()
            records.append((record.data, record.base))
        stream.finish()
        return records

    @staticmethod
    def _matrix(x, y, angle, reflections):
        radians = math.radians(angle / 10.0)
        c, s = math.cos(radians), math.sin(radians)
        sx, sy = (-1 if reflections & 1 else 1), (-1 if reflections & 2 else 1)
        return (c * sx, -s * sy, s * sx, c * sy, x, y)

    @staticmethod
    def _polygons(geometry):
        if geometry.is_empty:
            return []
        if geometry.geom_type == 'Polygon':
            return [geometry]
        if hasattr(geometry, 'geoms'):
            return [polygon for child in geometry.geoms for polygon in Proteus8Parser._polygons(child)]
        return []

    def _valid(self, geometry):
        if not geometry.is_valid:
            self._warn('Native touching/self-intersecting polygon rings were made valid without coarse rounding')
            geometry = make_valid(geometry)
        return geometry

    def _remember_layer(self, layer, name):
        if layer in self._layer_names:
            raise ProteusParseError('Duplicate native layer identity %d' % layer)
        label = name
        while label in self._layer_names.values():
            label = '%s (layer %s)' % (label, layer)
        self._layer_names[layer] = label
        self._layer_display_names[label] = name
        return label

    def _layer(self, layer):
        if layer in self._layer_names:
            return self._layer_names[layer]
        if isinstance(layer, str):
            return self._remember_layer(layer, layer)
        manufacturing = {36: ('Top Silk Screen', 'GTO'), 37: ('Bottom Silk Screen', 'GBO'),
                         38: ('Top Resist', 'GTS'), 39: ('Bottom Resist', 'GBS'),
                         40: ('Top SMT Paste', 'GTP'), 41: ('Bottom SMT Paste', 'GBP'),
                         59: ('Board Outline', 'GKO')}
        if layer in manufacturing:
            name, extension = manufacturing[layer]
            label = self._remember_layer(layer, name)
            self._layer_extensions[label] = extension
            return label
        # Numbered in _finish: layers are discovered lazily while parsing.
        label = self._remember_layer(layer, 'Layer %d (unverified)' % layer)
        self._unidentified.append(layer)
        return label

    def _add(self, layer, geometry, follow=None):
        name = self._layer(layer)
        polygons = self._polygons(self._valid(geometry))
        self._solids.setdefault(name, []).extend(polygons)
        if follow is not None and not follow.is_empty:
            self._follows.setdefault(name, []).append(follow)

    def _segs(self, radius):
        # Gerber "Circle Steps" preference (adaptive by default); positions here are millimetres.
        return circle_quad_segs(gerber_circle_steps(self._options, radius, 'MM'))

    def _stroke(self, points, width):
        if width <= 0:
            return Polygon()
        if len(set(points)) == 1:
            return Point(points[0]).buffer(width / 2, quad_segs=self._segs(width / 2))
        return LineString(points).buffer(width / 2, quad_segs=self._segs(width / 2))

    def _text_attribute(self, r):
        r.expect(b'\xff')
        text = r.text(r.take(r.number(self.TEXT_FORMAT)))
        data = r.take(34)
        font = r.cstring()
        r.cstring()
        r.take(4)
        x, y, angle, reflection = struct.unpack_from('<iihH', data)
        height, width = struct.unpack_from('<ii', data, 18)
        return {'text': text, 'x': x / self.SCALE, 'y': y / self.SCALE,
                'angle': angle, 'reflection': reflection, 'height': height / self.SCALE,
                'width': width / self.SCALE, 'font': font, 'visible': bool(data[30] & 1)}

    def _text_geometry(self, text, width, height, font, strokes=None):
        if not text.strip() or width <= 0 or height <= 0:
            return Polygon()
        geometry = None
        follows = []
        if font not in ('Vector Font', 'Default Font', ''):
            from appParsers.ParseFont import ParseFont
            # Scanning the system fonts takes seconds: do it once per process, not once per board.
            if Proteus8Parser._font_parser is None:
                fonts = ParseFont(self.app)
                fonts.get_fonts_by_types()
                Proteus8Parser._font_parser = fonts
            if font in Proteus8Parser._font_parser.regular_f:
                # Render at one inch/em, then scale down: small hinted glyphs lose detail.
                rendered = Proteus8Parser._font_parser.font_to_geometry(text, font, 'regular', 72, units='MM')
                if not isinstance(rendered, str):
                    geometry = unary_union(rendered)
                    geometry = affine_transform(geometry, (height / 25.4, 0, 0, height / 25.4, 0, 0))
        if geometry is None:
            from appParsers.ParseKiCadPcb import KiCadPcbParser
            if not hasattr(self, '_text_engine'):
                self._text_engine = KiCadPcbParser(app=self.app)
            geometry, follows = self._text_engine._text_geometry(text, width, height, height / 10, [], False)
            self._warn('Native vector or unavailable font text uses the existing approximate PCB stroke font; '
                       'verify glyph shapes and spacing before fabrication')
        if geometry.is_empty:
            return geometry
        # Saved text anchors are independent of the component's own placement.
        minx, miny, maxx, maxy = geometry.bounds
        if strokes is not None:
            for line in follows:
                solid = line.buffer(height / 20, quad_segs=self._text_engine._segs(height / 20))
                shift = (1, 0, 0, 1, -(minx + maxx) / 2, -(miny + maxy) / 2)
                strokes.append((affine_transform(solid, shift), affine_transform(line, shift), height / 10))
        self._warn('Native text anchor alignment is centered; verify text spacing and alignment before fabrication')
        return affine_transform(geometry, (1, 0, 0, 1, -(minx + maxx) / 2, -(miny + maxy) / 2))

    def _attribute_geometry(self, attribute, layer):
        if not attribute['visible']:
            return
        strokes = []
        shape = self._text_geometry(attribute['text'], attribute['width'], attribute['height'], attribute['font'], strokes)
        matrix = self._matrix(attribute['x'], attribute['y'], attribute['angle'], attribute['reflection'])
        self._add(layer, affine_transform(shape, matrix))
        for solid, line, width in strokes:
            self._primitives.stroke(self._layer(layer), affine_transform(solid, matrix),
                                    affine_transform(line, matrix), width)

    def _objects(self, r):
        while True:
            flags, kind = r.unpack('BB')
            if flags:
                self._warn('Placed-object flags 0x%02x are not fully interpreted' % flags)
            if kind == 255:
                return
            if kind == 0:
                self._component(r)
            elif kind == 1:
                self._region(r)
            elif kind == 2:
                self._standalone_pad(r)
            elif kind == 3:
                self._graphic(r)
            elif kind == 5:
                self._bitmap(r)
            elif kind == 6:
                layer = r.number()
                r.take(4 + 16 + 2 + 8 + 24)
                r.cstring()
                r.take(3)
                self._text_attribute(r)
                self._warn('Dimension annotations on native layer %d are not rendered' % layer)
            else:
                r.fail('unsupported unframed placed-object class %d' % kind)

    def _bitmap(self, r):
        layer = r.number()
        flags, x, y, rotation, width, height = r.unpack('6i')  # x, y: bottom-left corner
        dib = r.subreader(r.number())
        if dib.remaining < 40:
            dib.fail('bitmap DIB header is truncated')
        size, w, h, _, bpp, compression, _, _, _, used, _ = dib.unpack('IiiHHIIiiII')
        if size < 40 or w <= 0 or h == 0 or width <= 0 or height <= 0:
            dib.fail('invalid bitmap DIB header')
        dib.take(size - 40)
        if flags or rotation or bpp != 1 or compression:
            reason = 'flags/rotation' if flags or rotation else '%d-bpp compression %d' % (bpp, compression)
            self._warn('Bitmap artwork on native layer %d is not rendered (%s unsupported)' % (layer, reason))
            return
        dib.take(4 * (used or 2))  # palette; ink is the set bit, whatever its colour
        stride = (w + 31) // 32 * 4
        rows = abs(h)
        pixels = np.unpackbits(np.frombuffer(dib.take(stride * rows), np.uint8).reshape(rows, stride),
                               axis=1)[:, :w]
        pw, ph = width / self.SCALE / w, height / self.SCALE / rows
        x0, y0 = x / self.SCALE, y / self.SCALE
        boxes = []
        for k in range(rows):
            row = pixels[k]
            if not row.any():
                continue
            edges = np.flatnonzero(np.diff(np.concatenate(([0], row, [0]))))
            line = k if h > 0 else rows - 1 - k  # bottom-up unless the height is negative
            for start, end in zip(edges[::2], edges[1::2]):
                boxes.append(box(x0 + start * pw, y0 + line * ph, x0 + end * pw, y0 + (line + 1) * ph))
        if boxes:
            self._add(layer, unary_union(boxes))
        self._warn('Bitmap artwork on native layer %d is drawn from its set pixels; verify the ink polarity '
                   'before fabrication' % layer)

    def _standalone_pad(self, r):
        r.fail('unsupported unframed standalone pad class for this wire schema')

    def _component(self, r):
        x, y, angle, reflection, identity = r.unpack('iihHI')
        name = r.pstring()
        attributes = [self._text_attribute(r), self._text_attribute(r)]
        reference = attributes[0]['text']
        if reflection & ~3:
            self._warn('Component %s reflection flags 0x%x are not fully interpreted' % (reference, reflection))
        if name not in self._packages:
            r.fail('component %s refers to unknown package %s' % (reference, name))
        pads, streams = self._packages[name]
        count = r.count(38)
        if count != len(pads):
            r.fail('placed/library pad count mismatch for %s' % reference)
        for _ in range(count):
            self._pad(r)
        r.expect(b'\x66')
        matrix = self._matrix(x / self.SCALE, y / self.SCALE, angle, reflection)
        # A single reflection mounts a part on the bottom; two reflections are a
        # top-side rotation. This also handles through-hole parts with pads on both sides.
        silk = 37 if bool(reflection & 1) != bool(reflection & 2) else 36
        for attribute in attributes:
            self._attribute_geometry(attribute, silk)
        for slot, records in enumerate(streams):
            if records:
                layer = silk
                if slot != 0:
                    layer = 'Package graphic slot %d (unverified)' % slot
                    self._warn('Package graphic slot %d has unverified layer mapping; retained as non-default preview' %
                               slot)
                for data, base in records:
                    self._primitive(_Reader(data, r.member, base, self.STRING_FORMAT), layer, matrix)

    def _pad(self, r):
        span, style_index, flags, x, y, angle, reflection = r.unpack('QHBiihH')
        r.take(7)
        r.number('i')
        for _ in range(r.count(1)):
            r.pstring()
        if style_index >= len(self._pad_styles):
            r.fail('invalid pad-style ordinal %d' % style_index)
        if reflection & ~3:
            self._warn('Pad reflection flags 0x%x are not fully interpreted' % reflection)
        layer = span & 0xffff
        supported_span = span in (0, 15, 0x800000000000ffff)
        if not supported_span:
            self._warn('Pad layer/span 0x%x is not fully supported; layer assignment is a preview' % span)
        layers = self._copper if layer == 0xffff else [layer]
        fields, extra = self._pad_styles[style_index]
        geometry, drill, plated = self._pad_shape(fields, extra, r, layers)
        matrix = self._matrix(x / self.SCALE, y / self.SCALE, angle, reflection)
        if isinstance(geometry, dict):
            for number, shape in geometry.items():
                if number > 15:  # stack mask/paste entries, already grown by the stack guard
                    if supported_span:
                        self._add(number, affine_transform(shape, matrix))
                        entry = number - 22
                        subfields, _ = self._style_names[extra[entry]]
                        guard = self._length(fields[4]) if number in (38, 39) and fields[4] else 0.0
                        self._flash_style(number, affine_transform(shape, matrix), subfields, matrix, guard)
                    continue
                target = number if supported_span else 'Pad span 0x%x (layer %d; unverified)' % (span, number)
                self._add(target, affine_transform(shape, matrix))
                subfields, _ = self._style_names[extra[number]]
                self._flash_style(target, affine_transform(shape, matrix), subfields, matrix)
        elif geometry is not None:
            shape = affine_transform(geometry, matrix)
            for number in layers:
                target = number if supported_span else 'Pad span 0x%x (layer %d; unverified)' % (span, number)
                self._add(target, shape)
                self._flash_style(target, shape, fields, matrix)
            if supported_span:
                guard = self._length(fields[5]) if len(fields) > 5 and fields[5] else 0.0
                opening = affine_transform(geometry.buffer(guard, quad_segs=self._segs(guard)), matrix)
                for number in layers:
                    if number in (0, 15):
                        self._add(38 if number == 0 else 39, opening)
                        self._flash_style(38 if number == 0 else 39, opening, fields, matrix, guard)
                if span in (0, 15) and fields[1] in ('RSMPAD', 'CSMPAD'):
                    self._add(40 if span == 0 else 41, shape)
                    self._flash_style(40 if span == 0 else 41, shape, fields, matrix)
                self._warn('Per-pad mask suppression and paste reduction/suppression rules are not fully '
                           'verified; ordinary pad guard gaps and unchanged eligible SMD paste are used')
        if drill > 0:
            group = 'PTH' if plated else 'NPTH'  # a drilled pad is a real hole whatever its span
            if not supported_span:
                self._warn('Pad with drill on unverified span 0x%x: the hole is kept as a real %s drill' %
                           (span, group))
            key = round(drill, 6)
            tool = self._drills.setdefault(group, {}).setdefault(key, {'tooldia': drill, 'drills': [], 'slots': []})
            tool['drills'].append(Point(x / self.SCALE, y / self.SCALE))
        if supported_span:
            return [number for number in geometry if number <= 15] if isinstance(geometry, dict) else layers
        return []

    def _flash_style(self, layer, solid, fields, matrix, grow=0.0):
        if not self._primitives.enabled or solid.is_empty:
            return
        kind = fields[1]
        if kind == 'PSMPAD':
            left, bottom, right, top = solid.bounds
            self._primitives.flash(self._layer(layer), solid, (matrix[4], matrix[5]), 'block',
                                   right - left, top - bottom)
            return
        if kind not in ('CPAD', 'CSMPAD', 'CVIA', 'SPAD', 'RSMPAD', 'DPAD'):
            return
        width, height = self._length(fields[2]), self._length(fields[3])
        aperture = 'block'
        if kind in ('CPAD', 'CSMPAD', 'CVIA') and width == height:
            aperture = 'circle'
        elif kind in ('SPAD', 'RSMPAD', 'DPAD'):
            radius = self._length(fields[8])
            if kind == 'DPAD' and not radius:
                radius = min(width, height) / 2
            if radius == min(width, height) / 2:
                aperture = 'oval'
            elif radius == 0 and grow == 0:
                aperture = 'rect'
        angle = math.degrees(math.atan2(matrix[2], matrix[0]))
        if aperture in ('rect', 'oval') and abs(angle - round(angle / 90) * 90) > 1e-8:
            aperture = 'block'
        self._primitives.flash(self._layer(layer), solid, (matrix[4], matrix[5]), aperture,
                               width + 2 * grow, height + 2 * grow, angle)

    def _pad_shape(self, fields, extra, r: _Reader, layers, stack=False):
        name, kind = fields[:2]
        if kind == 'STACK':
            if stack or len(fields) < 4 or fields[2] != 'HOLE' or len(extra) != 20:
                r.fail('unsupported or corrupt stacked pad style %s' % name)
            geometry = {}
            guard = self._length(fields[4]) if len(fields) > 4 and fields[4] else 0.0
            # Entries 0..15 copper, 16/17 resist, 18/19 paste (top, bottom).
            entries = [(layer, layer) for layer in layers if layer <= 15]
            for side, entry in ((0, 16), (15, 17), (0, 18), (15, 19)):
                if side in layers:
                    entries.append((22 + entry, entry))  # 16..19 -> layers 38..41
            for number, entry in entries:
                target = extra[entry]
                if target == '<NONE>':
                    continue
                if target not in self._style_names:
                    r.fail('stack %s references missing style %s' % (name, target))
                subfields, points = self._style_names[target]
                shape, _, _ = self._pad_shape(subfields, points, r, [number], stack=True)
                if shape is None:
                    continue
                if number in (38, 39):
                    shape = shape.buffer(guard, quad_segs=self._segs(guard))
                geometry[number] = shape
            self._warn('Stacked pad mask/paste guard semantics are unverified; resist is the entry grown by the '
                       'stack guard, paste the entry unchanged')
            return geometry, self._length(fields[3]), True
        if kind == 'PSMPAD':
            try:
                count = int(fields[2])
                points = [tuple(self._length(value.strip()) for value in point.split(',')) for point in extra]
            except (ValueError, IndexError) as error:
                r.fail('invalid polygon pad %s: %s' % (name, error))
            if count != len(points) or count < 3 or any(len(point) != 2 for point in points):
                r.fail('invalid polygon pad vertex count for %s' % name)
            self._warn('Custom polygon pad paste and per-pad mask suppression are not verified; copper and a '
                       'guard-grown mask opening are used')
            return self._valid(Polygon(points)), 0.0, True
        if len(fields) < 10:
            r.fail('incomplete pad style %s' % name)
        width, height, drill = [self._length(value) for value in fields[2:5]]
        if width <= 0 or height <= 0 or drill < 0:
            r.fail('invalid pad dimensions for %s' % name)
        try:
            options = int(fields[7])
        except ValueError:
            r.fail('invalid pad flags for %s' % name)
        if options & 4:
            self._warn('Pad style flag 4 is interpreted as non-plated drill artwork; verify plating before fabrication')
        if options & ~4:
            self._warn('Pad style %s flags 0x%x have unverified shape/offset semantics' % (name, options))
        if kind in ('CPAD', 'CSMPAD', 'CVIA'):
            geometry = affine_transform(Point(0, 0).buffer(1, quad_segs=self._segs(max(width, height) / 2)),
                                        (width / 2, 0, 0, height / 2, 0, 0))
        elif kind in ('SPAD', 'RSMPAD', 'DPAD'):
            radius = self._length(fields[8])
            if kind == 'DPAD' and not radius:
                radius = min(width, height) / 2
            if radius < 0 or radius > min(width, height) / 2 + 1e-5:
                r.fail('invalid pad corner radius for %s' % name)
            if radius > 0:
                inner = box(-width / 2 + radius, -height / 2 + radius,
                            width / 2 - radius, height / 2 - radius)
                if inner.area == 0:
                    points = [(-width / 2 + radius, -height / 2 + radius),
                              (width / 2 - radius, height / 2 - radius)]
                    geometry = self._stroke(points, 2 * radius)
                else:
                    geometry = inner.buffer(radius, quad_segs=self._segs(radius))
            else:
                geometry = box(-width / 2, -height / 2, width / 2, height / 2)
        else:
            self._warn('Unsupported pad shape %s (%s): copper omitted; declared drill retained' % (kind, name))
            geometry = None
        return geometry, drill, not bool(options & 4)

    def _graphic(self, r):
        layer, flags, x, y, rotation = r.unpack('IIiii')
        if flags:
            self._warn('Graphic transform flags 0x%x on layer %d are not fully interpreted' % (flags, layer))
        self._primitive(r.block(), layer, self._matrix(x / self.SCALE, y / self.SCALE, rotation, 0))

    def _primitive(self, r, layer, matrix, depth=0):
        if depth > 8:
            r.fail('recursive graphic symbol')
        r.number()  # native in-memory object-size hint, NOT a serialized extent
        kind, version = r.unpack('BB')
        if version != self.GRAPHIC_VERSION:
            self._warn('Unsupported graphic version %d on %s; artwork omitted' % (version, layer))
            return
        if kind in (6, 8):
            if kind == 8:
                self._warn('Package origin/reference markers are not rendered')
                return
            # Kind 6 is a named graphic instance, not an ordinary stroked primitive.
            r.take(18)
            name = r.cstring()
            r.take(11)
            r.finish()
            if name not in self._symbols:
                r.fail('unknown graphic symbol %s' % name)
            for data, base in self._symbols[name]:
                self._primitive(_Reader(data, r.member, base, self.STRING_FORMAT), layer, matrix, depth + 1)
            return
        style_flags = r.number()
        r.number('B')
        width = r.number('i') / self.SCALE
        r.take(4)
        hollow = r.number('B')
        r.take(10)
        r.cstring()
        shape = None
        follow = None
        text_strokes = []
        if kind == 0:
            values = r.unpack('iiii')
            points = [(values[0] / self.SCALE, values[1] / self.SCALE),
                      (values[2] / self.SCALE, values[3] / self.SCALE)]
            follow = LineString(points)
            shape = self._stroke(points, width)
        elif kind == 1:
            x, y, rotation, width_rect, height = r.unpack('iiiii')
            shape = box(0, 0, width_rect / self.SCALE, height / self.SCALE)
            shape = affine_transform(shape, self._matrix(x / self.SCALE, y / self.SCALE, rotation, 0))
        elif kind == 2:
            x, y, radius = r.unpack('iii')
            if radius <= 0:
                r.fail('non-positive graphic radius')
            shape = Point(x / self.SCALE, y / self.SCALE).buffer(radius / self.SCALE,
                                                                 quad_segs=self._segs(radius / self.SCALE))
            if layer == 59 and hollow:
                r.finish()
                # Native board-edge circles are padless cutouts, not plated pad artwork.
                # Use the declared radius, not the stroked outline's outer diameter.
                drill = 2 * radius / self.SCALE
                center = affine_transform(Point(x / self.SCALE, y / self.SCALE), matrix)
                tool = self._drills.setdefault('NPTH', {}).setdefault(
                    round(drill, 6), {'tooldia': drill, 'drills': [], 'slots': []})
                if not any(center.equals_exact(point, 1e-8) for point in tool['drills']):
                    tool['drills'].append(center)
        elif kind == 3:
            values = r.unpack('8i')
            controls = [(values[i] / self.SCALE, values[i + 1] / self.SCALE) for i in range(0, 8, 2)]
            from appParsers.ParseFont import ParseFont
            points = [controls[0]]
            ParseFont._flatten_cubic(points, *controls, segments=8, tolerance=0.001)
            follow = LineString(points)
            shape = self._stroke(points, width)
        elif kind == 4:
            x, y, rotation, options, height, width_text = r.unpack('iiiiii')
            font = r.text(r.take(36).split(b'\0', 1)[0])
            text = r.cstring()
            if self.TITLE_FROM_ARTWORK and layer == 36 and not self.name and text and not text.startswith('@'):
                self.name = text
            shape = self._text_geometry(text, width_text / self.SCALE, height / self.SCALE, font, text_strokes)
            text_matrix = self._matrix(x / self.SCALE, y / self.SCALE,
                                       (rotation & 0xffff) - (0x10000 if rotation & 0x8000 else 0),
                                       (rotation >> 16) & 3)
            shape = affine_transform(shape, text_matrix)
            text_strokes = [(affine_transform(solid, text_matrix), affine_transform(line, text_matrix), stroke)
                            for solid, line, stroke in text_strokes]
        elif kind == 7:
            points, metadata = self._path_points(r)
            if any(value in (2, 3) for value in metadata):
                self._warn('Curved editable polygon on %s is unsupported; artwork omitted' % layer)
            else:
                shape = self._valid(Polygon(points))
        else:
            self._warn('Unsupported graphic kind %d on %s; artwork omitted' % (kind, layer))
            return
        r.finish()
        if shape is not None:
            if kind not in (0, 3, 4) and hollow:
                follow = shape.boundary
                shape = follow.buffer(max(width, 0) / 2, quad_segs=self._segs(max(width, 0) / 2))
            shape = affine_transform(shape, matrix)
            follow = affine_transform(follow, matrix) if follow is not None else None
            self._add(layer, shape, follow)
            for solid, line, stroke in text_strokes:
                self._primitives.stroke(self._layer(layer), affine_transform(solid, matrix),
                                        affine_transform(line, matrix), stroke)
            if follow is not None:
                self._primitives.stroke(self._layer(layer), shape, follow, width)
            elif kind == 2:
                center = affine_transform(Point(x / self.SCALE, y / self.SCALE), matrix)
                diameter = 2 * radius / self.SCALE
                self._primitives.flash(self._layer(layer), shape, center, 'circle', diameter, diameter)
            elif kind == 1:
                local = self._matrix(x / self.SCALE, y / self.SCALE, rotation, 0)
                center = affine_transform(affine_transform(Point(width_rect / self.SCALE / 2,
                                                                  height / self.SCALE / 2), local), matrix)
                angle = math.degrees(math.atan2(matrix[2], matrix[0]))
                angle += (1 if matrix[0] * matrix[3] - matrix[1] * matrix[2] > 0 else -1) * rotation / 10
                self._primitives.flash(self._layer(layer), shape, center,
                                       'rect' if abs(angle - round(angle / 90) * 90) <= 1e-8 else 'block',
                                       width_rect / self.SCALE, height / self.SCALE, angle)

    def _path_points(self, r):
        count = r.count(10)
        if count < 3:
            r.fail('polygon has fewer than three vertices')
        r.take(18)  # saved bounding box and path flags
        points = [tuple(value / self.SCALE for value in r.unpack('ii')) for _ in range(count)]
        return points, r.take(2 * count)

    def _ring(self, r):
        count = r.count(8)
        if count < 3:
            r.fail('saved fill ring has fewer than three vertices')
        return [tuple(value / self.SCALE for value in r.unpack('ii')) for _ in range(count)]

    def _region(self, r):
        layer = r.number('B')
        path = r.block()
        points, metadata = self._path_points(path)
        path.finish()
        net, trace_style, relief_style = r.pstring(), r.pstring(), r.pstring()
        r.take(26)  # saved region options and fill-cache header
        self._warn('Region option flags/cache freshness are not fully verified; '
                   'saved contours are used without repouring')
        count = r.count(8)
        if not count:
            self._warn('Region on layer %d has no saved fill; editable envelope is not substituted' % layer)
        target = layer
        if not net and layer in self._copper:
            target = 'Unassigned region on layer %d (unverified)' % layer
            self._warn('Region with empty net on copper layer %d has unresolved additive/keepout semantics; '
                       'retained on a non-default preview layer' % layer)
        for _ in range(count):
            outer = self._ring(r)
            holes = [self._ring(r) for _ in range(r.count(4))]
            self._add(target, Polygon(outer, holes))
        r.take(4 * count)  # per-polygon association IDs, not lengths
        thermal_count = r.count(20)
        relief_width = self._trace_names.get(relief_style)
        if thermal_count and relief_width is None:
            r.fail('unknown thermal style %s' % relief_style)
        envelope = self._valid(Polygon(points))
        for _ in range(thermal_count):
            r.take(8)  # centre; segments are already absolute
            for _ in range(r.count(16)):
                coordinates = r.unpack('iiii')
                if any(coordinates):
                    segment = [(coordinates[0] / self.SCALE, coordinates[1] / self.SCALE),
                               (coordinates[2] / self.SCALE, coordinates[3] / self.SCALE)]
                    stroke = self._stroke(segment, relief_width)
                    self._add(target, stroke.intersection(envelope))
            r.take(8)
        if thermal_count:
            self._warn('Saved thermal segments use the declared relief width with round caps; '
                       'native cap/clipping options are not fully verified')
        r.take(16 * r.count(16))

    def _vias(self, r):
        while True:
            layer = r.number()
            if not layer:
                return
            if layer != 68:
                self._warn('Standalone pad entry layer/type %d is not fully verified' % layer)
            self._pad(r)

    def _routes(self, r):
        # 64 physical layer lists, including empty ones; do not search for XY or net markers.
        for layer_list in range(64):
            while True:
                segments = r.count(8)
                if not segments:
                    break
                layer, style = r.unpack('BH')
                r.take(20)  # endpoint drill clearances and route option bytes
                if style >= len(self._trace_styles):
                    r.fail('invalid trace-style ordinal %d' % style)
                points = [tuple(value / self.SCALE for value in r.unpack('ii')) for _ in range(segments + 1)]
                if r.number('i') != -1:
                    r.fail('invalid route terminator')
                r.number('B')
                r.pstring()
                follow = LineString(points)
                solid = self._stroke(points, self._trace_styles[style])
                self._add(layer, solid, follow)
                self._primitives.stroke(self._layer(layer), solid, follow, self._trace_styles[style])
                if layer != layer_list:
                    self._warn('Route layer %d differs from list %d; explicit route layer retained' %
                               (layer, layer_list))
        if r.remaining < 8 or r.remaining % 4:
            r.fail('truncated trailing route sections')
        tail = r.take(r.remaining)
        if tail[-8:] != bytes(8):
            r.fail('invalid trailing section terminators')
        if any(tail):
            self._warn('Nonzero route tail (%d bytes) contains unsupported saved data; '
                       'additional geometry may be missing' % len(tail))
        self._warn('Route endpoint trimming/cap options are not fully verified; '
                   'declared-width round-capped centreline strokes are used')

    def _name_mechanical(self):
        number = 0
        for layer in sorted(self._unidentified):
            old = self._layer_names[layer]
            if not self._solids.get(old):
                continue
            number += 1
            label = name = 'Mechanical %d' % number
            while label in self._layer_names.values():
                label = '%s (layer %s)' % (label, layer)
            self._layer_names[layer] = label
            self._layer_display_names.pop(old, None)
            self._layer_display_names[label] = name
            self._layer_extensions[label] = 'GM%d' % number
            self._solids[label] = self._solids.pop(old)
            if old in self._follows:
                self._follows[label] = self._follows.pop(old)
            if old in self._primitives.layers:
                self._primitives.layers[label] = self._primitives.layers.pop(old)
            self._warn('Native layer %d has no verified Proteus name; imported as Mechanical %d (GM%d), '
                       'not a default layer' % (layer, number, number))

    def _finish(self):
        self._name_mechanical()
        for group, diameters in self._drills.items():
            self.drill_groups[group] = {number: diameters[key]
                                        for number, key in enumerate(sorted(diameters), 1)}
        self.default_drill_groups = [group for group in self.drill_groups
                                     if group in ('PTH', 'NPTH')]
        for name, shapes in self._solids.items():
            polygons = self._polygons(self._valid(self._union(shapes)))
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            if not polygons:
                continue
            self.layers[self._layer_key(name)] = {
                'name': self._layer_display_names.get(name, name),
                'solid_geometry': polygons,
                'follow_geometry': self._follows.get(name, []),
                'tools': {0: {'type': 'REG', 'size': 0.0,
                              'geometry': [{'solid': polygon} for polygon in polygons]}}}
            self._primitives.finish(name, self.layers[self._layer_key(name)])
        self.default_layers = [self._layer_key(self._layer_names[layer])
                               for layer in self._copper + [36, 37, 38, 39, 40, 41, 59]
                               if layer in self._layer_names
                               if self._layer_key(self._layer_names[layer]) in self.layers]

    @staticmethod
    def _union(shapes):
        """Union only the shapes that touch: an R-tree groups them, isolated shapes pass unchanged."""
        shapes = [shape for shape in shapes if not shape.is_empty]
        if len(shapes) < 2:
            return unary_union(shapes)
        root = list(range(len(shapes)))

        def find(i):
            while root[i] != i:
                root[i] = root[root[i]]
                i = root[i]
            return i

        # Bounding boxes are enough to group; the union of each group is exact.
        first, second = STRtree(shapes).query(shapes)
        for i, j in zip(first.tolist(), second.tolist()):
            if i < j:
                root[find(j)] = find(i)
        groups = {}
        for i in range(len(shapes)):
            groups.setdefault(find(i), []).append(shapes[i])
        parts = [members[0] if len(members) == 1 else unary_union(members) for members in groups.values()]
        return MultiPolygon([polygon for part in parts for polygon in Proteus8Parser._polygons(part)])

    def _layer_key(self, name):
        extension = self._layer_extensions.get(name)
        return '%s.%s' % (name, extension) if extension else name


class Proteus9Parser(Proteus8Parser):
    """907 wire differences verified against the supplied release-900 project.

    Geometry records share the v8 implementation; this isn't a claim that all
    Proteus 9 features are decoded. Keep its profile separate from v8 dispatch.
    """

    STRING_FORMAT = 'H'
    TEXT_FORMAT = 'I'
    PACKAGE_VERSION = 907
    GRAPHIC_VERSION = 3
    LIBRARY_MARKER = b'\xfb\xff'
    NET_RULE_BYTES = 39
    TITLE_FROM_ARTWORK = False

    def parse_bytes(self, data, member_name='board.LYT'):
        if len(data) >= 19 and 800 <= struct.unpack_from('<H', data, 17)[0] < 900:
            board = Proteus8Parser(app=self.app).parse_bytes(data, member_name)
            board.warnings.insert(0, 'Proteus 9 fallback: embedded LYT declares a Proteus 8 release; '
                                     'using the Proteus 8 decoder')
            return board
        board = super().parse_bytes(data, member_name)
        self._warn('Proteus 9 file-version 907 support is verified against the supplied release-900 sample only; '
                   'unverified geometry remains diagnosed, not silently treated as complete')
        return board

    def _netclass_options(self, r):
        r.take(106)
        r.pstring()  # added named rule/style reference
        r.take(7)

    def _library_identity(self, r):
        r.take(4 + 6)  # timestamp and added identity fields
        r.pstring()  # optional library GUID
        flags = r.number('B')
        if flags:
            self._warn('Proteus 9 library identity flags 0x%x are not fully interpreted' % flags)

    def _standalone_pad(self, r):
        self._pad(r)
        options = r.number()
        if options:
            self._warn('Proteus 9 standalone pad options 0x%x are not fully interpreted' % options)
