# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native Altium PCB6 geometry reader                       #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read saved PCB6 manufacturing geometry without an external CAD application.

Format reference: https://dev-docs.kicad.org/en/import-formats/altium/
Coordinates remain Cartesian (Y up). Saved pours, not polygon definitions,
are the authority for copper. No repour or inferred drills are performed.
"""

import io
import math
import re
import struct
from typing import Any
import zlib

from shapely import LineString, MultiLineString, Point, Polygon, box, get_coordinates
from shapely.affinity import rotate, scale, translate
from shapely.ops import unary_union

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.board_primitives import BoardPrimitives


class PcbDocParseError(ValueError):
    """Malformed data or material geometry that cannot be represented safely."""


class _Reader:
    def __init__(self, data, name):
        self.data = data
        self.name = name
        self.pos = 0

    def take(self, count):
        if count < 0 or count > len(self.data) - self.pos:
            raise PcbDocParseError('%s: truncated record at byte %d' % (self.name, self.pos))
        data = self.data[self.pos:self.pos + count]
        self.pos += count
        return data

    def unpack(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))

    def block(self):
        return self.take(self.unpack('I')[0])

    def remaining(self):
        return len(self.data) - self.pos


class PcbDocParser:
    UNIT = 2.54e-6
    BINARY = {'Arcs6': (1, 1), 'Pads6': (2, 6), 'Vias6': (3, 1),
              'Tracks6': (4, 1), 'Texts6': (5, 2), 'Fills6': (6, 1),
              'Regions6': (11, 1), 'ShapeBasedRegions6': (11, 1), 'BoardRegions': (11, 1)}
    PROPERTIES = ('Board6', 'Polygons6', 'Rules6', 'Nets6', 'Components6',
                  'ExtendedPrimitiveInformation', 'Dimensions6')

    # Original reconstruction, not copied from KiCad or GPL font projects.
    # The supplied Altium GTO establishes a 6-cell cap height and 2-cell gap.
    # H/R/T/V/1..5/. paths and variable advances were measured from its strokes.
    # Other uppercase/numeric glyphs extrapolate the same beveled grid; rendering
    # them records an explicit provenance warning, not an exact-font claim.
    STROKE_REFERENCE_GLYPHS = frozenset('HRTV12345.')
    STROKE_GLYPHS = {
        'H': (((0, 6), (0, 0), (0, 3), (4, 3), (4, 6), (4, 0)),),
        'R': (((0, 0), (0, 6), (3, 6), (4, 5), (4, 3), (3, 2), (0, 2)),
              ((2, 2), (4, 0))),
        'T': (((0, 6), (4, 6), (2, 6), (2, 0)),),
        'V': (((0, 6), (0, 2), (2, 0), (4, 2), (4, 6)),),
        '1': (((0, 0), (2, 0), (1, 0), (1, 6), (0, 5)),),
        '2': (((4, 0), (0, 0), (4, 4), (4, 5), (3, 6), (1, 6), (0, 5)),),
        '3': (((0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (2, 3),
               (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)),),
        '4': (((3, 0), (3, 6), (0, 3), (4, 3)),),
        '5': (((4, 6), (0, 6), (0, 3), (2, 4), (3, 4), (4, 3), (4, 1),
               (3, 0), (1, 0), (0, 1)),),
        '.': (((0, 0), (0, 1), (1, 1), (1, 0), (0, 0)),),
        'A': (((0, 0), (0, 5), (1, 6), (3, 6), (4, 5), (4, 0)), ((0, 3), (4, 3))),
        'B': (((0, 0), (0, 6)), ((0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)),
              ((0, 3), (3, 3), (4, 2), (4, 1), (3, 0), (0, 0))),
        'C': (((4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)),),
        'D': (((0, 0), (0, 6), (3, 6), (4, 5), (4, 1), (3, 0), (0, 0)),),
        'E': (((4, 6), (0, 6), (0, 0), (4, 0)), ((0, 3), (3, 3))),
        'F': (((0, 0), (0, 6), (4, 6)), ((0, 3), (3, 3))),
        'G': (((4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0),
               (4, 1), (4, 3), (2, 3)),),
        'I': (((0, 0), (2, 0)), ((1, 0), (1, 6)), ((0, 6), (2, 6))),
        'J': (((0, 1), (1, 0), (3, 0), (4, 1), (4, 6)),),
        'K': (((0, 0), (0, 6)), ((4, 6), (0, 3), (4, 0))),
        'L': (((0, 6), (0, 0), (4, 0)),),
        'M': (((0, 0), (0, 6), (2, 3), (4, 6), (4, 0)),),
        'N': (((0, 0), (0, 6), (4, 0), (4, 6)),),
        'O': (((1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)),),
        'P': (((0, 0), (0, 6), (3, 6), (4, 5), (4, 3), (3, 2), (0, 2)),),
        'Q': (((1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)),
              ((2, 2), (4, 0))),
        'S': (((4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3),
               (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)),),
        'U': (((0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)),),
        'W': (((0, 6), (0, 2), (1, 0), (2, 2), (3, 0), (4, 2), (4, 6)),),
        'X': (((0, 0), (4, 6)), ((0, 6), (4, 0))),
        'Y': (((0, 6), (2, 3), (4, 6)), ((2, 3), (2, 0))),
        'Z': (((0, 6), (4, 6), (0, 0), (4, 0)),),
        '0': (((1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)),),
        '6': (((4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1),
               (4, 2), (3, 3), (0, 3)),),
        '7': (((0, 6), (4, 6), (1, 0)),),
        '8': (((1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3)),
              ((1, 3), (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3), (1, 3))),
        '9': (((0, 1), (1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5),
               (0, 4), (1, 3), (4, 3)),),
        '-': (((0, 3), (4, 3)),),
        '+': (((0, 3), (4, 3)), ((2, 1), (2, 5))),
        '_': (((0, 0), (4, 0)),),
        '/': (((0, 0), (4, 6)),),
        '\\': (((0, 6), (4, 0)),),
        '=': (((0, 2), (4, 2)), ((0, 4), (4, 4))),
        '*': (((2, 1), (2, 5)), ((0, 2), (4, 4)), ((0, 4), (4, 2))),
        ':': (((0, 1), (0, 2), (1, 2), (1, 1), (0, 1)),
              ((0, 4), (0, 5), (1, 5), (1, 4), (0, 4))),
        ',': (((0, 0), (0, 1), (1, 1), (1, 0), (0, 0)), ((1, 0), (0, -1))),
        '(': (((2, 6), (1, 5), (0, 4), (0, 2), (1, 1), (2, 0)),),
        ')': (((0, 6), (1, 5), (2, 4), (2, 2), (1, 1), (0, 0)),),
        '[': (((2, 6), (0, 6), (0, 0), (2, 0)),),
        ']': (((0, 6), (2, 6), (2, 0), (0, 0)),)
    }

    def __init__(self, app=None, circle_steps=None):
        # circle_steps: a fixed number of segments for every circle; None: the Gerber "Circle Steps" preference
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        # the installed fonts, scanned once for the texts with a font that is not embedded
        self._installed_fonts = None
        self._font_metrics = {}
        self._reset()

    def _reset(self):
        self._primitives = BoardPrimitives(self._options)
        self.layers = {}
        self.drill_groups = {}
        self.warnings = []
        self.record_counts = {}
        self.origin = (0.0, 0.0)
        self.board_outline = Polygon()
        self._outline_chord_error = 0.0
        self.copper_layers = []
        self._names = {}
        self._board = {}
        self._rules = []
        self._nets = []
        self._components = []
        self._polygons = []
        self._pads = []
        self._cutouts = []
        self._pour_solids = {}
        self._pour_cutouts = {}
        self._splits = {}
        self._fonts = {}
        self._wide_strings = {}
        self._overrides = {}
        self._stroke_extrapolated = set()

    def parse_file(self, filename):
        import olefile

        self._reset()
        try:
            with olefile.OleFileIO(str(filename), raise_defects=olefile.DEFECT_INCORRECT) as container:
                streams = {}
                counts = {}
                for name in (*self.PROPERTIES, *self.BINARY, 'WideStrings6', 'EmbeddedFonts6'):
                    if not container.exists([name, 'Data']):
                        continue
                    streams[name] = container.openstream([name, 'Data']).read()
                    if name != 'WideStrings6' or container.exists([name, 'Header']):
                        if not container.exists([name, 'Header']):
                            raise PcbDocParseError('%s: missing Header' % name)
                        header = container.openstream([name, 'Header']).read()
                        if len(header) != 4:
                            raise PcbDocParseError('%s: invalid Header length' % name)
                        counts[name] = struct.unpack('<I', header)[0]
                # These contain material/board definitions, not merely editor metadata.
                for name in ('EmbeddedBoards6',):
                    if container.exists([name, 'Header']):
                        header = container.openstream([name, 'Header']).read()
                        if len(header) != 4:
                            raise PcbDocParseError('%s: invalid Header length' % name)
                        if struct.unpack('<I', header)[0]:
                            raise PcbDocParseError('%s: unsupported populated geometry stream' % name)
                return self._parse_streams(streams, counts)
        except PcbDocParseError:
            self.layers = {}
            self.drill_groups = {}
            raise
        except Exception as error:
            self.layers = {}
            self.drill_groups = {}
            raise PcbDocParseError('Cannot read PCB6 container: %s' % error) from error

    @staticmethod
    def _props(data):
        try:
            text = data.rstrip(b'\0').decode('latin1')
            return {key.upper(): value for item in text.split('|') if '=' in item
                    for key, value in [item.split('=', 1)]}
        except (UnicodeError, ValueError) as error:
            raise PcbDocParseError('Invalid property data') from error

    @staticmethod
    def _unit(value):
        match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(mil|mm|in|inch)?\s*', str(value))
        if not match:
            raise PcbDocParseError('Invalid Altium dimension: %s' % value)
        number = float(match.group(1))
        if not math.isfinite(number):
            raise PcbDocParseError('Non-finite dimension')
        return number * {'mil': 0.0254, 'mm': 1.0, 'in': 25.4, 'inch': 25.4, None: 0.0254}[match.group(2)]

    @staticmethod
    def _at(data, fmt, offset) -> Any:
        if offset + struct.calcsize('<' + fmt) > len(data):
            raise PcbDocParseError('Truncated primitive payload at byte %d' % offset)
        values = struct.unpack_from('<' + fmt, data, offset)
        if any(isinstance(value, float) and not math.isfinite(value) for value in values):
            raise PcbDocParseError('Non-finite primitive coordinate or angle')
        return values[0] if len(values) == 1 else values

    def _xy(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise PcbDocParseError('Non-finite vertex')
        return x * self.UNIT - self.origin[0], y * self.UNIT - self.origin[1]

    @staticmethod
    def _layer_id(name):
        names = {'TOP': 1, 'TOPLAYER': 1, 'BOTTOM': 32, 'BOTTOMLAYER': 32,
                 'TOPOVERLAY': 33, 'BOTTOMOVERLAY': 34,
                 'TOPPASTE': 35, 'BOTTOMPASTE': 36, 'TOPSOLDER': 37, 'BOTTOMSOLDER': 38,
                 'DRILLGUIDE': 55, 'KEEPOUT': 56, 'KEEPOUTLAYER': 56, 'DRILLDRAWING': 73, 'MULTILAYER': 74}
        name = name.upper().replace(' ', '').replace('-', '')
        name = name.replace('MIDLAYER', 'MID').replace('INTERNALPLANE', 'PLANE')
        if name in names:
            return names[name]
        for prefix, base, limit in (('MID', 1, 30), ('PLANE', 38, 16), ('MECHANICAL', 56, 16)):
            if name.startswith(prefix) and name[len(prefix):].isdigit():
                number = int(name[len(prefix):])
                if 1 <= number <= limit:
                    return base + number
                if prefix == 'MECHANICAL' and number > 16:
                    return 0x01020000 + number
        if name.isdigit():
            return int(name)
        raise PcbDocParseError('Unsupported Altium layer: %s' % name)

    def _layer(self, data, v7offset=None):
        layer = self._at(data, 'B', 0)
        if v7offset is not None and len(data) >= v7offset + 4:
            v7 = self._at(data, 'I', v7offset)
            if 0x01020010 < v7 < 0x01030000:
                layer = v7
        if layer == 0 or (layer > 74 and not 0x01020010 < layer < 0x01030000):
            raise PcbDocParseError('Unsupported material layer ID %d' % layer)
        return layer

    def _parse_streams(self, streams, counts=None):
        """Decode stream bytes; kept separate from OLE for bounded-record tests."""
        self._reset()
        counts = counts or {}
        props = {}
        records = {}
        for name in self.PROPERTIES:
            reader = _Reader(streams.get(name, b''), name)
            result = []
            while reader.remaining():
                if name in ('Rules6', 'Dimensions6'):
                    reader.take(2)
                value = self._props(reader.block())
                if not value:
                    raise PcbDocParseError('%s: empty properties' % name)
                result.append(value)
            props[name] = result
            self.record_counts[name] = len(result)
        for name, (tag, subrecords) in self.BINARY.items():
            reader = _Reader(streams.get(name, b''), name)
            result = []
            while reader.remaining():
                if reader.unpack('B')[0] != tag:
                    raise PcbDocParseError('%s: invalid record tag' % name)
                result.append([reader.block() for _ in range(subrecords)])
            records[name] = result
            self.record_counts[name] = len(result)
        self._read_wide_strings(streams.get('WideStrings6', b''))
        self.record_counts['WideStrings6'] = len(self._wide_strings)
        for name, expected in counts.items():
            if name == 'EmbeddedFonts6':
                continue
            if self.record_counts.get(name, 0) != expected:
                raise PcbDocParseError('%s: Header count %d does not match %d records' %
                                       (name, expected, self.record_counts.get(name, 0)))
        if len(props['Board6']) != 1:
            raise PcbDocParseError('PCB6 requires exactly one Board6 record')
        self._board = props['Board6'][0]
        self.origin = (self._unit(self._board.get('ORIGINX', '0mil')),
                       self._unit(self._board.get('ORIGINY', '0mil')))
        self._read_board()
        if records['BoardRegions']:
            if len(records['BoardRegions']) != 1:
                raise PcbDocParseError('Multiple rigid/flex board stack regions are not supported')
            data = records['BoardRegions'][0][0]
            reader = _Reader(data, 'BoardRegions')
            reader.take(18)
            values = self._props(reader.block())
            if int(values.get('BENDINGLINECOUNT', 0)):
                raise PcbDocParseError('Bent board stack regions are not supported')
            region = self._region(data)[5]
            # Altium expands its stack-region boundary by a few raw units. The arcs of the two outlines have
            # different chords: the stack region uses its ARCRESOLUTION, the Board6 outline uses _steps().
            tolerance = 10 * self.UNIT
            if self._outline_chord_error:
                tolerance += self._outline_chord_error + self._unit(values.get('ARCRESOLUTION', '0.5mil'))
            if not region.buffer(tolerance).covers(self.board_outline) or \
                    not self.board_outline.buffer(tolerance).covers(region):
                raise PcbDocParseError('Board stack region differs from the Board6 outline')
        self._rules = sorted((rule for rule in props['Rules6'] if rule.get('ENABLED', 'TRUE') == 'TRUE'),
                             key=lambda rule: int(rule.get('PRIORITY', 1)))
        self._nets = [net.get('NAME', '') for net in props['Nets6']]
        self._components = props['Components6']
        self._polygons = props['Polygons6']
        for value in props['ExtendedPrimitiveInformation']:
            if value.get('TYPE') != 'Mask':
                raise PcbDocParseError('Unsupported extended primitive type: %s' % value.get('TYPE'))
            kind = value.get('PRIMITIVEOBJECTID', '')
            index = int(value.get('PRIMITIVEINDEX', -1))
            streams_by_kind = {'Track': 'Tracks6', 'Arc': 'Arcs6', 'Fill': 'Fills6', 'Pad': 'Pads6', 'Via': 'Vias6'}
            if kind not in streams_by_kind or not 0 <= index < len(records[streams_by_kind[kind]]):
                raise PcbDocParseError('Unsupported/invalid extended mask primitive reference: %s %d' % (kind, index))
            if kind == 'Via' and value.get('PASTEMASKEXPANSIONMODE', 'None') != 'None':
                raise PcbDocParseError('Via paste-mask overrides are not supported')
            self._overrides[(kind, index)] = value
        self._read_fonts(streams.get('EmbeddedFonts6', b''), counts.get('EmbeddedFonts6'))
        saved = []
        for name in ('Regions6', 'ShapeBasedRegions6'):
            for parts in records[name]:
                item = self._region(parts[0], name == 'ShapeBasedRegions6')
                # Saved plain and extended streams often describe the SAME pour.
                # Prefer the saved tessellation; use extended data only when absent.
                key = item[:5]
                if name == 'ShapeBasedRegions6' and any(old[:5] == key and
                        old[5].hausdorff_distance(item[5]) <= self.UNIT * 2 for old in saved):
                    continue
                if name == 'ShapeBasedRegions6' and item[3] != 65535 and any(
                        old[0] == item[0] and old[3:5] == item[3:5] for old in saved):
                    continue
                saved.append(item)
        for layer, kind, keepout, parent, subindex, geo in saved:
            if kind == 5:
                self._cutouts.append(geo)
            elif kind == 1:
                if 39 <= layer <= 54:
                    self._add(layer, geo)
                else:
                    self._pour_cutouts.setdefault(layer, []).append(geo)
            elif kind == 0:
                if keepout and not 39 <= layer <= 54:
                    self._add(56, geo)
                elif parent != 65535 and not 39 <= layer <= 54:
                    self._pour_solids.setdefault(layer, []).append(geo)
                else:
                    self._add(layer, geo)
            else:
                raise PcbDocParseError('Unsupported material region kind %d' % kind)
        if self._cutouts:
            self.board_outline = self.board_outline.difference(unary_union(self._cutouts))
        for name in ('Tracks6', 'Arcs6', 'Fills6'):
            for index, parts in enumerate(records[name]):
                self._primitive(name, parts[0], index)
        for index, parts in enumerate(records['Pads6']):
            self._pad(parts, index)
        for index, parts in enumerate(records['Vias6']):
            self._via(parts[0], index)
        for parts in records['Texts6']:
            self._text(parts)
        for value in props['Dimensions6']:
            self._dimension(value)
        for index, value in enumerate(self._polygons):
            if value.get('HATCHSTYLE') == 'None' or value.get('SHELVED', 'FALSE') == 'TRUE':
                continue
            layer = self._layer_id(value.get('LAYER', 'TOP'))
            if 39 <= layer <= 54:
                # A split plane is a net domain of the plane; its split lines are negative tracks of the layer.
                if value.get('POLYGONTYPE') != 'Split Plane':
                    raise PcbDocParseError('Unsupported polygon type on a plane layer: %s' %
                                           value.get('POLYGONTYPE'))
                net = int(value.get('NET', -1))
                if not 0 <= net < len(self._nets):
                    raise PcbDocParseError('Split plane %d has no valid net' % index)
                self._splits.setdefault(layer, []).append((self._nets[net], self._property_outline(value)[0]))
                continue
            found = any(item[3] == index for item in saved) or any(
                self._at(parts[0], 'H', 5) == index for name in ('Tracks6', 'Arcs6') for parts in records[name])
            if not found:
                raise PcbDocParseError('Polygon %d has no saved pour; native repouring is not supported' % index)
        for layer, solids in self._pour_solids.items():
            geo = unary_union(solids)
            if layer in self._pour_cutouts:
                geo = geo.difference(unary_union(self._pour_cutouts[layer]))
            self._add(layer, geo)
        self._finish()
        return self

    def _read_board(self):
        for key, name in self._board.items():
            match = re.fullmatch(r'LAYER(\d+)NAME', key)
            if match:
                self._names[int(match.group(1))] = name
            match = re.fullmatch(r'LAYERV7_(\d+)NAME', key)
            if match:
                prefix = key[:-4]
                layer = int(self._board.get(prefix + 'LAYERID', 0))
                if layer:
                    self._names[layer] = name
        current = 1
        while current:
            if current in self.copper_layers:
                raise PcbDocParseError('Cyclic copper stackup')
            if not (1 <= current <= 32 or 39 <= current <= 54):
                raise PcbDocParseError('Invalid copper stackup layer %d' % current)
            self.copper_layers.append(current)
            current = int(self._board.get('LAYER%dNEXT' % current, 32 if current == 1 else 0))
        if self.copper_layers[-1] != 32:
            raise PcbDocParseError('Copper stackup does not end at Bottom Layer')
        self.board_outline, self._outline_chord_error = self._property_outline(self._board)

    def _property_outline(self, props):
        """The polygon of the VXn/VYn vertices of a property record and the largest chord error of its arcs."""
        vertices = []
        chord_error = 0.0
        index = 0
        while 'VX%d' % index in props:
            points = self._property_vertex(props, index)
            # an arc goes from its start angle to its end angle, but the outline can go through it the other way
            if len(points) > 1 and vertices and \
                    math.dist(vertices[-1], points[-1]) < math.dist(vertices[-1], points[0]):
                points.reverse()
            if len(points) > 1:
                radius = self._unit(props['R%d' % index])
                chord_error = max(chord_error, radius * (1 - math.cos(math.pi / self._steps(radius))))
            vertices.extend(points)
            index += 1
        return self._polygon(vertices), chord_error

    def _plane_net(self, layer):
        """The net of the whole plane layer: '' for no net, None when the board does not tell."""
        name = self._board.get('LAYER%dNETNAME' % layer, self._board.get('PLANE%dNETNAME' % (layer - 38)))
        if name is None:
            return None
        name = name.strip()
        return '' if name == '(No Net)' else name

    def _property_vertex(self, props, index):
        suffix = str(index)
        position = (self._unit(props['VX' + suffix]) - self.origin[0],
                    self._unit(props['VY' + suffix]) - self.origin[1])
        if int(props.get('KIND' + suffix, 0)):
            center = (self._unit(props['CX' + suffix]) - self.origin[0],
                      self._unit(props['CY' + suffix]) - self.origin[1])
            return self._arc(center, self._unit(props['R' + suffix]),
                             float(props['SA' + suffix]), float(props['EA' + suffix]))
        return [position]

    def _steps(self, radius):
        # Segments of a whole circle of this radius (millimetres): the fixed override or the Gerber preference.
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _segs(self, radius):
        return circle_quad_segs(self._steps(radius))

    def _arc(self, center, radius, start, end):
        if radius <= 0 or not all(math.isfinite(v) for v in (*center, radius, start, end)):
            raise PcbDocParseError('Invalid arc geometry')
        sweep = (end - start) % 360
        if abs(sweep) < 1e-10:
            sweep = 360
        count = max(1, math.ceil(self._steps(radius) * sweep / 360))
        return [(center[0] + radius * math.cos(math.radians(start + sweep * i / count)),
                 center[1] + radius * math.sin(math.radians(start + sweep * i / count)))
                for i in range(count + 1)]

    @staticmethod
    def _polygon(shell, holes=()):
        if len(shell) < 3 or any(len(hole) < 3 for hole in holes):
            raise PcbDocParseError('Region has fewer than three vertices')
        try:
            geo = Polygon(shell, holes)
            if not geo.is_empty and not geo.is_valid:
                # Saved integer coordinates can make loops of 1 or 2 raw units: remove those vertices.
                shell = PcbDocParser._without_noise(shell)
                holes = [PcbDocParser._without_noise(hole) for hole in holes]
                if len(shell) >= 3 and all(len(hole) >= 3 for hole in holes):
                    geo = Polygon(shell, holes)
        except (ValueError, TypeError) as error:
            raise PcbDocParseError('Invalid region vertices') from error
        if geo.is_empty or not geo.is_valid:
            raise PcbDocParseError('Invalid region topology (self-intersection or invalid hole)')
        return geo

    @staticmethod
    def _without_noise(points):
        result = []
        for point in points:
            if not result or math.dist(result[-1], point) > 3 * PcbDocParser.UNIT:
                result.append(point)
        return result

    @staticmethod
    def _polygons_of(geo):
        if geo.is_empty:
            return []
        if isinstance(geo, Polygon):
            return [geo]
        if geo.geom_type in ('MultiPolygon', 'GeometryCollection'):
            return [polygon for child in geo.geoms for polygon in PcbDocParser._polygons_of(child)]
        raise PcbDocParseError('Non-polygon material geometry: %s' % geo.geom_type)

    def _add(self, layer, geo, follow=None):
        if layer == 74:
            for copper in self.copper_layers:
                self._add(copper, geo, follow)
            return
        if not geo.is_valid:
            raise PcbDocParseError('Invalid geometry on layer %d' % layer)
        polygons = self._polygons_of(geo)
        if not polygons:
            return
        result = self.layers.setdefault(layer, {'name': self._names.get(layer, {
            1: 'Top Layer', 32: 'Bottom Layer', 33: 'Top Overlay', 34: 'Bottom Overlay',
            35: 'Top Paste', 36: 'Bottom Paste', 37: 'Top Solder', 38: 'Bottom Solder',
            56: 'Keep-Out Layer'}.get(layer, 'Altium Layer %d' % layer)),
            'solid_geometry': [], 'follow_geometry': [], 'tools': {}})
        result['solid_geometry'].extend(polygons)
        if follow is not None:
            result['follow_geometry'].append(follow)

    def _region(self, data, extended=False):
        reader = _Reader(data, 'Region')
        header = reader.take(18)
        layer = self._layer(header)
        holes_count = self._at(header, 'H', 14)
        props = self._props(reader.block())
        if not props:
            raise PcbDocParseError('Region has empty properties')
        if props.get('V7_LAYER'):
            candidate = self._layer_id(props['V7_LAYER'])
            if candidate > 74:
                layer = candidate
        count = reader.unpack('I')[0] + int(extended)
        vertices = []
        if count > reader.remaining() // (37 if extended else 16):
            raise PcbDocParseError('Truncated region vertices')
        for _ in range(count):
            if extended:
                rounded, x, y, cx, cy, radius, start, end = reader.unpack('Biiiiidd')
                if rounded:
                    points = self._arc(self._xy(cx, cy), radius * self.UNIT, start, end)
                    # the region can go through the arc from its end angle to its start angle
                    if vertices and math.dist(vertices[-1], points[-1]) < math.dist(vertices[-1], points[0]):
                        points.reverse()
                    vertices.extend(points)
                else:
                    vertices.append(self._xy(x, y))
            else:
                vertices.append(self._xy(*reader.unpack('dd')))
        holes = []
        for _ in range(holes_count):
            count = reader.unpack('I')[0]
            if count > reader.remaining() // 16:
                raise PcbDocParseError('Truncated region hole')
            holes.append([self._xy(*reader.unpack('dd')) for _ in range(count)])
        kind = int(props.get('KIND', 0))
        if props.get('ISBOARDCUTOUT', 'FALSE') == 'TRUE':
            if kind != 0:
                raise PcbDocParseError('Invalid board cutout region kind')
            kind = 5
        return (layer, kind, header[2] == 2 or layer == 56, self._at(header, 'H', 5),
                int(props.get('SUBPOLYINDEX', 65535)), self._polygon(vertices, holes))

    def _primitive(self, name, data, index):
        minimum = {'Tracks6': 35, 'Arcs6': 47, 'Fills6': 37}[name]
        if len(data) < minimum:
            raise PcbDocParseError('%s: short primitive' % name)
        layer = self._layer(data, {'Tracks6': 41, 'Arcs6': 52, 'Fills6': 42}[name])
        if data[1] & 2:
            return  # Polygon definition outline, NOT manufactured copper.
        if data[2] == 2 and not 39 <= layer <= 54:
            layer = 56
        if name == 'Tracks6':
            x1, y1, x2, y2 = self._at(data, 'iiii', 13)
            follow = LineString([self._xy(x1, y1), self._xy(x2, y2)])
            width = self._at(data, 'i', 29) * self.UNIT
            if width <= 0:
                raise PcbDocParseError('Non-positive track width')
            geo = follow.buffer(width / 2, quad_segs=self._segs(width / 2))
        elif name == 'Arcs6':
            center = self._xy(*self._at(data, 'ii', 13))
            radius = self._at(data, 'i', 21) * self.UNIT
            start, end = self._at(data, 'dd', 25)
            follow = LineString(self._arc(center, radius, start, end))
            width = self._at(data, 'i', 41) * self.UNIT
            if width <= 0:
                raise PcbDocParseError('Non-positive arc width')
            geo = follow.buffer(width / 2, quad_segs=self._segs(width / 2))
        else:
            first = self._xy(*self._at(data, 'ii', 13))
            last = self._xy(*self._at(data, 'ii', 21))
            geo = box(min(first[0], last[0]), min(first[1], last[1]),
                      max(first[0], last[0]), max(first[1], last[1]))
            geo = rotate(geo, self._at(data, 'd', 29), origin='center')
            follow = LineString(geo.exterior.coords)
        parent = self._at(data, 'H', 5)
        if parent != 65535 and layer <= 32:
            self._pour_solids.setdefault(layer, []).append(geo)
            result = self.layers.setdefault(layer, {'name': self._names.get(layer, 'Altium Layer %d' % layer),
                'solid_geometry': [], 'follow_geometry': [], 'tools': {}})
            result['follow_geometry'].append(follow)
        else:
            self._add(layer, geo, follow)
        if name in ('Tracks6', 'Arcs6') and not 39 <= layer <= 54:
            self._primitives.stroke(layer, geo, follow, width)
        elif name == 'Fills6' and not 39 <= layer <= 54:
            angle = self._at(data, 'd', 29)
            self._primitives.flash(layer, geo, geo.centroid,
                                   'rect' if abs(angle - round(angle / 90) * 90) <= 1e-8 else 'block',
                                   abs(last[0] - first[0]), abs(last[1] - first[1]),
                                   angle)
        override = self._overrides.get(({'Tracks6': 'Track', 'Arcs6': 'Arc', 'Fills6': 'Fill'}[name], index))
        if override and layer in (1, 32):
            context = self._context(data, 'Track' if name == 'Tracks6' else 'Arc' if name == 'Arcs6' else 'Fill')
            for prefix, target, rule in (('PASTE', 35 if layer == 1 else 36, 'PasteMaskExpansion'),
                                          ('SOLDER', 37 if layer == 1 else 38, 'SolderMaskExpansion')):
                mode, manual = self._mask_override(override, prefix, 0, 0)
                expansion = self._expansion(mode, manual, rule, context)
                if expansion is not None:
                    opening = geo.buffer(expansion, quad_segs=self._segs(expansion))
                    self._add(target, opening)
                    if name in ('Tracks6', 'Arcs6'):
                        self._primitives.stroke(target, opening, follow, width + 2 * expansion)
                    else:
                        self._primitives.flash(target, opening, geo.centroid, 'block',
                                               abs(last[0] - first[0]) + 2 * expansion,
                                               abs(last[1] - first[1]) + 2 * expansion)

    def _shape(self, width, height, shape, alt=0, corner=0):
        if width < 0 or height < 0:
            raise PcbDocParseError('Negative pad size')
        if width == 0 or height == 0:
            return Polygon()
        if alt not in (0, 1, 2, 3, 9):
            raise PcbDocParseError('Unsupported alternate pad shape %d' % alt)
        if alt == 9:
            if not 0 <= corner <= 100:
                raise PcbDocParseError('Invalid rounded pad corner ratio')
            radius = min(width, height) * corner / 200
            if radius == 0:
                return box(-width / 2, -height / 2, width / 2, height / 2)
            core = box(-width / 2 + radius, -height / 2 + radius,
                       width / 2 - radius, height / 2 - radius)
            if core.area == 0:
                core = LineString([(-width / 2 + radius, -height / 2 + radius),
                                   (width / 2 - radius, height / 2 - radius)])
            return core.buffer(radius, quad_segs=self._segs(radius))
        if shape == 1:
            radius = min(width, height) / 2
            ends = [(-max(0, width - height) / 2, -max(0, height - width) / 2),
                    (max(0, width - height) / 2, max(0, height - width) / 2)]
            return LineString(ends).buffer(radius, quad_segs=self._segs(radius))
        if shape == 2:
            return box(-width / 2, -height / 2, width / 2, height / 2)
        if shape == 3:
            cut = min(width, height) / (2 + math.sqrt(2))
            x, y = width / 2, height / 2
            return Polygon([(-x + cut, -y), (x - cut, -y), (x, -y + cut), (x, y - cut),
                            (x - cut, y), (-x + cut, y), (-x, y - cut), (-x, -y + cut)])
        raise PcbDocParseError('Unsupported pad shape %d' % shape)

    def _span(self, first, last):
        try:
            start = self.copper_layers.index(first)
            end = self.copper_layers.index(last)
        except ValueError as error:
            raise PcbDocParseError('Drill/pad span uses a layer outside the copper stackup') from error
        if end < start:
            raise PcbDocParseError('Reversed drill/pad layer span')
        return self.copper_layers[start:end + 1]

    def _drill(self, position, diameter, plated, span, slot=0, angle=0):
        if diameter < 0:
            raise PcbDocParseError('Negative hole diameter')
        if diameter == 0:
            if slot:
                raise PcbDocParseError('Slot has no tool diameter')
            return Polygon()
        group = '%s_%d-%d' % ('PTH' if plated else 'NPTH', span[0], span[-1])
        tools = self.drill_groups.setdefault(group, {})
        tool_id = next((key for key, tool in tools.items() if tool['tooldia'] == diameter), len(tools) + 1)
        tool = tools.setdefault(tool_id, {'tooldia': diameter, 'drills': [], 'slots': []})
        if slot:
            if slot < diameter:
                raise PcbDocParseError('Slot length is shorter than the tool diameter')
            half = (slot - diameter) / 2
            dx = half * math.cos(math.radians(angle))
            dy = half * math.sin(math.radians(angle))
            first = Point(position[0] - dx, position[1] - dy)
            last = Point(position[0] + dx, position[1] + dy)
            tool['slots'].append((first, last))
            return LineString([first, last]).buffer(diameter / 2, quad_segs=self._segs(diameter / 2))
        tool['drills'].append(Point(position))
        return Point(position).buffer(diameter / 2, quad_segs=self._segs(diameter / 2))

    def _pad(self, parts, index):
        if not parts[0] or parts[0][0] + 1 != len(parts[0]):
            raise PcbDocParseError('Invalid pad name subrecord')
        data, stack = parts[4:6]
        if len(data) != 110 and len(data) < 114:
            raise PcbDocParseError('Invalid pad main subrecord length')
        if stack and len(stack) < 596:
            raise PcbDocParseError('Truncated pad stack subrecord')
        layer = self._layer(data, 163)
        position = self._xy(*self._at(data, 'ii', 13))
        angle = self._at(data, 'd', 52)
        hole_rotation = self._at(data, 'd', 106) if len(data) >= 114 else 0
        if len(data) == 110 and self._at(data, 'I', 106):
            raise PcbDocParseError('Unsupported legacy pad hole extension')
        mode = data[62]
        if mode not in (0, 1, 2) or (mode == 2 and not stack):
            raise PcbDocParseError('Unsupported/missing pad stack mode %d' % mode)
        span = self.copper_layers
        if layer == 74 and len(data) >= 120 and data[114] and data[117]:
            marker = self._at(data, 'I', 114)
            if marker != 0x0103000f:
                span = self._span(data[117], data[114])
        applicable = span if layer == 74 else [layer]
        context = self._context(data, 'Pad')
        context['multilayer'] = layer == 74
        shapes, centres, sizes = {}, {}, {}
        for copper in applicable:
            outer = 0 if copper == 1 or mode == 0 or layer != 74 else 2 if copper == 32 else 1
            width, height = self._at(data, 'ii', 21 + outer * 8)
            shape = data[49 + outer]
            if mode == 2 and 3 <= copper <= 31:
                # The main middle size carries Mid1; arrays carry Mid2..Mid30.
                width = self._at(stack, 'i', (copper - 3) * 4)
                height = self._at(stack, 'i', 116 + (copper - 3) * 4)
                shape = stack[232 + copper - 3]
            stack_index = 0 if mode == 0 or layer != 74 else 31 if copper == 32 else copper - 1
            if not 0 <= stack_index < 32:
                stack_index = None
            alt, corner = (0, 0)
            if stack and stack_index is not None:
                alt, corner = stack[532 + stack_index], stack[564 + stack_index]
            geo = self._shape(width * self.UNIT, height * self.UNIT, shape, alt, corner)
            offset = (0.0, 0.0)
            if stack and stack_index is not None and self._at(data, 'i', 45):
                offset = (self._at(stack, 'i', 275 + stack_index * 4) * self.UNIT,
                          self._at(stack, 'i', 403 + stack_index * 4) * self.UNIT)
                # Hole offsets describe copper's displacement relative to the drill.
                geo = translate(geo, xoff=offset[0], yoff=offset[1])
            geo = translate(rotate(geo, angle, origin=(0, 0)), xoff=position[0], yoff=position[1])
            shapes[copper] = geo
            centres[copper] = translate(rotate(Point(offset), angle, origin=(0, 0)), *position)
            sizes[copper] = (width * self.UNIT, height * self.UNIT)
            if not 39 <= copper <= 54:
                self._add(copper, geo)
                centre = centres[copper]
                native_shape = ('rect' if corner == 0 else 'oval' if corner == 100 else 'custom') if alt == 9 \
                    else 'circle' if shape == 1 and width == height else {1: 'oval', 2: 'rect'}.get(shape, 'custom')
                if native_shape == 'custom' or (native_shape != 'circle' and
                        abs(angle - round(angle / 90) * 90) > 1e-8):
                    native_shape = 'block'
                self._primitives.flash(copper, geo, centre, native_shape,
                                       width * self.UNIT, height * self.UNIT, angle)
        diameter = self._at(data, 'i', 45) * self.UNIT
        hole_shape = stack[262] if stack else 0
        if hole_shape not in (0, 2) and diameter:
            raise PcbDocParseError('Unsupported drill shape %d (square holes are not Excellon drills)' % hole_shape)
        slot = self._at(stack, 'i', 263) * self.UNIT if hole_shape == 2 else 0
        slot_rotation = self._at(stack, 'd', 267) if hole_shape == 2 else 0
        if hole_shape == 2 and hole_rotation and slot_rotation and \
                abs(math.remainder(hole_rotation - slot_rotation, 360)) > 1e-8:
            raise PcbDocParseError('Conflicting pad hole and slot rotations')
        slot_angle = angle + (hole_rotation or slot_rotation)
        drill = self._drill(position, diameter, bool(data[60]), span, slot, slot_angle)
        self._pads.append({'shapes': shapes, 'hole': drill, 'net': self._at(data, 'H', 3), 'context': context})
        for copper, paste, solder, tent in ((1, 35, 37, 0x20), (32, 36, 38, 0x40)):
            if copper not in shapes:
                continue
            for target, mode_offset, expansion_offset, rule in (
                    (paste, 101, 86, 'PasteMaskExpansion'), (solder, 102, 90, 'SolderMaskExpansion')):
                if target == solder and data[1] & tent:
                    continue
                # Altium makes paste only for the pads of the top and the bottom layer, not for multilayer pads
                if target == paste and layer == 74:
                    continue
                prefix = 'SOLDER' if target == solder else 'PASTE'
                mask_mode, manual = self._mask_override(self._overrides.get(('Pad', index)), prefix,
                                                       data[mode_offset], self._at(data, 'i', expansion_offset) * self.UNIT)
                expansion = self._expansion(mask_mode, manual, rule, dict(context, layer=copper))
                if expansion is not None:
                    opening = shapes[copper].buffer(expansion, quad_segs=self._segs(expansion))
                    self._add(target, opening)
                    width, height = sizes[copper]
                    self._primitives.flash(target, opening, centres[copper], 'block',
                                           width + 2 * expansion, height + 2 * expansion)

    def _context(self, data, kind):
        net = self._at(data, 'H', 3)
        if net != 65535 and net >= len(self._nets):
            raise PcbDocParseError('Invalid %s net reference %d' % (kind, net))
        component = self._at(data, 'H', 7)
        return {'kind': kind, 'net': self._nets[net] if net < len(self._nets) else '',
                'component': self._components[component].get('SOURCEDESIGNATOR', '')
                if component < len(self._components) else '',
                'layer': data[29] if kind == 'Via' else self._layer(data), 'multilayer': False}

    def _mask_override(self, override, prefix, mode, manual):
        if not override or prefix + 'MASKEXPANSIONMODE' not in override:
            return mode, manual
        value = override[prefix + 'MASKEXPANSIONMODE']
        modes = {'None': 0, 'Rule': 1, 'Manual': 2}
        if value not in modes:
            raise PcbDocParseError('Unsupported extended mask mode: %s' % value)
        if value == 'Manual':
            key = prefix + 'MASKEXPANSION_MANUAL'
            if key not in override:
                raise PcbDocParseError('Missing extended manual mask expansion')
            manual = self._unit(override[key])
        return modes[value], manual

    def _scope(self, expression, context):
        expression = expression.strip()
        while expression.startswith('(') and expression.endswith(')'):
            depth = 0
            closed = False
            for index, char in enumerate(expression):
                depth += (char == '(') - (char == ')')
                if depth == 0 and index != len(expression) - 1:
                    closed = True
                    break
            if closed:
                break
            expression = expression[1:-1].strip()
        # Split only top-level Boolean operators; quoted names may contain spaces.
        depth = 0
        quoted = False
        operators = []
        for match in re.finditer(r"'|\(|\)|\bAND\b|\bOR\b", expression, re.IGNORECASE):
            token = match.group().upper()
            if token == "'":
                quoted = not quoted
            elif not quoted:
                if token == '(':
                    depth += 1
                elif token == ')':
                    depth -= 1
                elif depth == 0:
                    operators.append(match)
        if operators:
            match = next((operator for operator in operators if operator.group().upper() == 'OR'), operators[0])
            left = self._scope(expression[:match.start()], context)
            right = self._scope(expression[match.end():], context)
            return left and right if match.group().upper() == 'AND' else left or right
        if expression.upper().startswith('NOT '):
            return not self._scope(expression[4:], context)
        simple = {'ALL': True, 'TRUE': True, 'FALSE': False,
                  'ISPAD': context['kind'] == 'Pad', 'ISVIA': context['kind'] == 'Via',
                  'ISTRACK': context['kind'] == 'Track', 'ISARC': context['kind'] == 'Arc',
                  'ISFILL': context['kind'] == 'Fill', 'ISSMDPAD': context['kind'] == 'Pad' and not context['multilayer'],
                  'ISTHPAD': context['kind'] == 'Pad' and context['multilayer']}
        if expression.upper() in simple:
            return simple[expression.upper()]
        match = re.fullmatch(r"(InNet|InComponent|OnLayer)\s*\(\s*'([^']*)'\s*\)", expression, re.IGNORECASE)
        if match:
            function, value = match.groups()
            if function.lower() == 'onlayer':
                return context['layer'] == self._layer_id(value)
            return context['net' if function.lower() == 'innet' else 'component'].upper() == value.upper()
        raise PcbDocParseError('Unsupported manufacturing rule scope: %s' % expression)

    def _rule(self, kind, context):
        for rule in self._rules:
            if rule.get('RULEKIND') != kind:
                continue
            if self._scope(rule.get('SCOPE1EXPRESSION', 'All'), context):
                return rule
        return None

    def _expansion(self, mode, manual, kind, context):
        if mode == 0:
            return None
        if mode == 2:
            return manual
        if mode != 1:
            raise PcbDocParseError('Unsupported mask/paste mode %d' % mode)
        rule = self._rule(kind, context)
        if not rule:
            raise PcbDocParseError('Missing applicable %s rule' % kind)
        if 'EXPANSION' not in rule:
            raise PcbDocParseError('Missing %s expansion value' % kind)
        return self._unit(rule['EXPANSION'])

    def _via(self, data, index):
        if len(data) < 31:
            raise PcbDocParseError('Truncated via')
        span = self._span(data[29], data[30])
        position = self._xy(*self._at(data, 'ii', 13))
        diameter = self._at(data, 'i', 21) * self.UNIT
        hole = self._at(data, 'i', 25) * self.UNIT
        mode = data[74] if len(data) > 74 else 0
        if mode not in (0, 1, 2) or (len(data) > 74 and len(data) < 203):
            raise PcbDocParseError('Invalid extended via pad stack')
        shapes, sizes = {}, {}
        for layer in span:
            size = diameter
            if mode:
                offset = 0 if layer == 1 else 31 if layer == 32 else 1 if mode == 1 else layer - 1
                if 0 <= offset < 32:
                    size = self._at(data, 'i', 75 + offset * 4) * self.UNIT
            sizes[layer] = size
            shapes[layer] = self._shape(size, size, 1)
            shapes[layer] = translate(shapes[layer], xoff=position[0], yoff=position[1])
            if not 39 <= layer <= 54:
                self._add(layer, shapes[layer])
                self._primitives.flash(layer, shapes[layer], position, 'circle', size, size)
        drill = self._drill(position, hole, True, span)
        context = self._context(data, 'Via')
        self._pads.append({'shapes': shapes, 'hole': drill, 'net': self._at(data, 'H', 3), 'context': context})
        for layer, target, flag in ((1, 37, 0x20), (32, 38, 0x40)):
            if layer not in span or data[1] & flag:
                continue
            manual = len(data) > 74 and data[66] & 2
            mode = 2 if manual else 1
            expansion = self._at(data, 'i', 54) * self.UNIT if manual else 0
            if manual and layer == 32 and len(data) >= 246 and not data[241] & 1:
                expansion = self._at(data, 'i', 242) * self.UNIT
            mode, expansion = self._mask_override(self._overrides.get(('Via', index)), 'SOLDER', mode, expansion)
            expansion = self._expansion(mode, expansion, 'SolderMaskExpansion', dict(context, layer=layer))
            if expansion is None:
                continue
            # "from the hole edge" is an option of the manual expansion; with the rule, the mask starts at the pad
            from_hole = mode == 2 and len(data) > 74 and data[67] & 1
            if mode == 2 and from_hole and not shapes[layer].is_empty and \
                    hole + 2 * expansion <= shapes[layer].bounds[2] - shapes[layer].bounds[0]:
                continue
            base = drill if from_hole else shapes[layer]
            opening = base.buffer(expansion, quad_segs=self._segs(expansion))
            self._add(target, opening)
            size = (hole if from_hole else sizes[layer]) + 2 * expansion
            self._primitives.flash(target, opening, position, 'block', size, size)

    def _read_wide_strings(self, data):
        reader = _Reader(data, 'WideStrings6')
        while reader.remaining():
            index = reader.unpack('I')[0]
            if index in self._wide_strings:
                raise PcbDocParseError('Duplicate WideStrings6 index %d' % index)
            text = reader.block()
            try:
                self._wide_strings[index] = text.decode('utf-16le').rstrip('\0')
            except UnicodeError as error:
                raise PcbDocParseError('Malformed WideStrings6 entry') from error

    def _read_fonts(self, data, expected=None):
        reader = _Reader(data, 'EmbeddedFonts6')
        count = 0
        while reader.remaining():
            try:
                family = reader.block().decode('utf-16le').rstrip('\0')
                reader.block()  # Full font name.
                style = reader.block().decode('utf-16le').rstrip('\0')
                reader.take(3)
                font = zlib.decompress(reader.block())
                self._fonts[(family, style)] = font
            except (UnicodeError, zlib.error) as error:
                raise PcbDocParseError('Malformed embedded font') from error
            count += 1
        if expected is not None and count != expected:
            raise PcbDocParseError('EmbeddedFonts6 Header count mismatch')
        self.record_counts['EmbeddedFonts6'] = count

    def _text(self, parts):
        data, legacy = parts
        if len(data) < 40 or not legacy or legacy[0] + 1 != len(legacy):
            raise PcbDocParseError('Malformed text record')
        layer = self._layer(data, 226)
        component = self._at(data, 'H', 7)
        if component < len(self._components) and len(data) >= 123:
            owner = self._components[component]
            if (data[40] and owner.get('COMMENTON', 'FALSE') == 'FALSE') or \
                    (data[41] and owner.get('NAMEON', 'TRUE') == 'FALSE'):
                return
        text = legacy[1:].decode('latin1')
        if len(data) >= 123:
            text = self._wide_strings.get(self._at(data, 'I', 115), text)
        if not text.strip():
            return
        position = self._xy(*self._at(data, 'ii', 13))
        height = self._at(data, 'i', 21) * self.UNIT
        rotation = self._at(data, 'd', 27)
        font_type = data[43] if len(data) >= 123 else 0
        if len(data) >= 230:
            font_type = data[160]
        if font_type not in (0, 1):
            raise PcbDocParseError('Unsupported barcode text on layer %d' % layer)
        if len(data) >= 240 and (data[231] or (data[230] and not data[110])):
            raise PcbDocParseError('Unsupported text frame/offset border geometry')
        follows = []
        if font_type == 0:
            alphabet = self._at(data, 'H', 25)
            if alphabet != 1:
                raise PcbDocParseError('Unsupported Altium stroke alphabet %d' % alphabet)
            geo, follows = self._stroke_geometry(text, height, self._at(data, 'i', 36) * self.UNIT)
        else:
            font_name = data[46:110].decode('utf-16le').split('\0', 1)[0]
            if not font_name:
                raise PcbDocParseError('TrueType text has no source font name')
            bold = bool(data[44])
            italic = bool(data[45])
            style = 'Bold Italic' if bold and italic else 'Bold' if bold else 'Italic' if italic else 'Regular'
            geo = self._font_geometry(text, font_name, style, height)
        if len(data) >= 252 and data[240] and data[132] != 3:
            raise PcbDocParseError('Unsupported explicit text justification')
        if len(data) >= 123 and data[110]:
            if len(data) >= 137 and data[123]:
                frame_width = self._at(data, 'i', 124) * self.UNIT
                frame_height = self._at(data, 'i', 128) * self.UNIT
                if min(frame_width, frame_height) <= 0:
                    raise PcbDocParseError('Invalid inverted text rectangle')
                frame = box(0, 0, frame_width, frame_height)
            else:
                margin = self._at(data, 'i', 111) * self.UNIT
                if margin < 0 or geo.is_empty:
                    raise PcbDocParseError('Invalid inverted text margin or empty glyphs')
                minx, miny, maxx, maxy = geo.bounds
                frame = box(minx - margin, miny - margin, maxx + margin, maxy + margin)
            geo = frame.difference(geo)
            follows = []  # Glyph strokes are cutouts, not positive follow paths.
        if data[35]:
            geo = scale(geo, xfact=-1, yfact=1, origin=(0, 0))
            follows = [scale(line, xfact=-1, yfact=1, origin=(0, 0)) for line in follows]
        geo = translate(rotate(geo, rotation, origin=(0, 0)), xoff=position[0], yoff=position[1])
        self._add(layer, geo)
        if layer in self.layers:
            for line in follows:
                follow = translate(rotate(line, rotation, origin=(0, 0)),
                                   xoff=position[0], yoff=position[1])
                self.layers[layer]['follow_geometry'].append(follow)
                if not 39 <= layer <= 54:
                    width = self._at(data, 'i', 36) * self.UNIT
                    solid = translate(rotate(line.buffer(width / 2, quad_segs=self._segs(width / 2)),
                                             rotation, origin=(0, 0)), xoff=position[0], yoff=position[1])
                    self._primitives.stroke(layer, solid, follow, width)

    def _stroke_geometry(self, text, height, width):
        """Render original measured/extrapolated line glyphs at the saved stroke width."""
        if not all(math.isfinite(value) and value > 0 for value in (height, width)):
            raise PcbDocParseError('Invalid stroke text height or width')
        cell = height / 6
        pen = 0
        follows = []
        extrapolated = set()
        for char in text:
            if char == ' ':
                pen += height
                extrapolated.add(char)
                continue
            if char not in self.STROKE_GLYPHS:
                raise PcbDocParseError('Unsupported Altium default stroke glyph %r' % char)
            if char not in self.STROKE_REFERENCE_GLYPHS:
                extrapolated.add(char)
            paths = self.STROKE_GLYPHS[char]
            follows.extend(LineString([(pen + x * cell, y * cell) for x, y in path]) for path in paths)
            # Period has a measured 5-cell advance despite its 1-cell outline.
            advance = 5 if char == '.' else max(x for path in paths for x, _ in path) + 2
            pen += advance * cell
        self._stroke_extrapolated.update(extrapolated)
        if self._stroke_extrapolated:
            prefix = 'Extrapolated Altium default stroke glyphs '
            warning = prefix + repr(''.join(sorted(self._stroke_extrapolated))) + \
                ': independently reconstructed on the original Gerber 6-cell grid; exact shapes are unverified.'
            self.warnings[:] = [value for value in self.warnings if not value.startswith(prefix)]
            self.warnings.append(warning)
        # 1 buffer of all strokes gives the same shape as a union of 1 buffer per stroke, 2x faster
        solid = MultiLineString(follows).buffer(width / 2, quad_segs=self._segs(width / 2))
        return solid, follows

    def _dimension(self, props):
        """Render linear dimension annotations from their explicit reference points."""
        layer = self._layer_id(props.get('LAYER_V7', props.get('LAYER', '')))
        if layer <= 32 or 35 <= layer <= 54 or layer == 74:
            raise PcbDocParseError('Dimension annotations on material layers require saved geometry')
        if int(props.get('DIMENSIONKIND', -1)) != 1 or int(props.get('REFERENCES_COUNT', 0)) != 2:
            raise PcbDocParseError('Unsupported dimension kind or reference count')
        angle = float(props.get('ANGLE', 0))
        if not math.isfinite(angle):
            raise PcbDocParseError('Non-finite dimension angle')
        axis = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
        normal = (-axis[1], axis[0])
        anchor = (self._unit(props['X1']) - self.origin[0], self._unit(props['Y1']) - self.origin[1])
        references = [(self._unit(props['REFERENCE%dPOINTX' % i]) - self.origin[0],
                       self._unit(props['REFERENCE%dPOINTY' % i]) - self.origin[1]) for i in range(2)]
        positions = [sum((reference[i] - anchor[i]) * axis[i] for i in range(2)) for reference in references]
        ends = [(anchor[0] + position * axis[0], anchor[1] + position * axis[1]) for position in positions]
        # the dimension line and the arrows have the arrow line width; LINEWIDTH is not the drawn width
        width = self._unit(props.get('ARROWLINEWIDTH', props.get('LINEWIDTH', '10mil')))
        if width <= 0 or abs(positions[0] - positions[1]) <= self.UNIT:
            raise PcbDocParseError('Invalid linear dimension geometry')
        text_gap = self._unit(props.get('TEXTGAP', '10mil'))
        if text_gap < 0:
            raise PcbDocParseError('Invalid dimension text gap')
        # the text first: a text on the dimension line is centred across it and interrupts it
        span = self._dimension_text(props, layer, abs(positions[1] - positions[0]), angle, anchor, axis, normal,
                                    width, text_gap)
        low, high = sorted(positions)
        pieces = [(low, high)]
        if span is not None:
            pieces = [(low, min(high, span[0] - text_gap)), (max(low, span[1] + text_gap), high)]
        for start, stop in pieces:
            if stop - start <= self.UNIT:
                continue
            follow = LineString([(anchor[0] + position * axis[0], anchor[1] + position * axis[1])
                                 for position in (start, stop)])
            solid = follow.buffer(width / 2, quad_segs=self._segs(width / 2))
            self._add(layer, solid, follow)
            self._primitives.stroke(layer, solid, follow, width)
        gap = self._unit(props.get('EXTENSIONPICKGAP', '10mil'))
        extra = self._unit(props.get('EXTENSIONOFFSET', '10mil'))
        extension_width = self._unit(props.get('EXTENSIONLINEWIDTH', props.get('LINEWIDTH', '10mil')))
        arrow_size = self._unit(props.get('ARROWSIZE', '60mil'))
        arrow_length = self._unit(props.get('ARROWLENGTH', '100mil'))
        if min(gap, extra, arrow_size, arrow_length) < 0 or extension_width <= 0:
            raise PcbDocParseError('Invalid dimension annotation size')
        for index, (reference, end) in enumerate(zip(references, ends)):
            distance = math.dist(reference, end)
            if distance > gap:
                direction = tuple((end[i] - reference[i]) / distance for i in range(2))
                extension = LineString([(reference[0] + gap * direction[0], reference[1] + gap * direction[1]),
                                        (end[0] + extra * direction[0], end[1] + extra * direction[1])])
                solid = extension.buffer(extension_width / 2, quad_segs=self._segs(extension_width / 2))
                self._add(layer, solid, extension)
                self._primitives.stroke(layer, solid, extension, extension_width)
            sign = 1 if positions[1 - index] > positions[index] else -1
            if props.get('ARROWPOSITION', 'Inside') == 'Outside':
                sign *= -1
            for side in (-1, 1):
                tip = (end[0] + sign * arrow_length * axis[0] + side * arrow_size / 2 * normal[0],
                       end[1] + sign * arrow_length * axis[1] + side * arrow_size / 2 * normal[1])
                arrow = LineString([end, tip])
                solid = arrow.buffer(width / 2, quad_segs=self._segs(width / 2))
                self._add(layer, solid, arrow)
                self._primitives.stroke(layer, solid, arrow, width)

    def _dimension_text(self, props, layer, length, angle, anchor, axis, normal, width, text_gap):
        """
        Add the value text of a linear dimension. If the text touches the dimension line, the text is centred across
        the line and the return value is the text span along the line (positions from 'anchor'), for the line gap.
        Else the return value is None.
        """
        units = props.get('TEXTDIMENSIONUNIT', 'Millimeters')
        factors = {'Millimeters': 1, 'Centimeters': 10, 'Inches': 25.4, 'Mils': 0.0254}
        if units not in factors:
            raise PcbDocParseError('Unsupported dimension display units %s' % units)
        value = length / factors[units]
        precision = int(props.get('TEXTPRECISION', 2))
        if not 0 <= precision <= 12:
            raise PcbDocParseError('Invalid dimension precision')
        text = props.get('TEXTPREFIX', '') + ('%.*f' % (precision, value)) + props.get('TEXTSUFFIX', '')
        text_height = self._unit(props.get('TEXTHEIGHT', '60mil'))
        text_follows = []
        if props.get('USETTFONTS', 'FALSE') == 'TRUE':
            bold = props.get('BOLD', 'FALSE') == 'TRUE'
            italic = props.get('ITALIC', 'FALSE') == 'TRUE'
            style = 'Bold Italic' if bold and italic else 'Bold' if bold else 'Italic' if italic else 'Regular'
            geo = self._font_geometry(text, props.get('FONTNAME', 'Arial'), style, text_height)
        else:
            if props.get('FONT', 'DEFAULT') != 'DEFAULT':
                raise PcbDocParseError('Unsupported dimension stroke alphabet')
            geo, text_follows = self._stroke_geometry(text, text_height,
                                                     self._unit(props.get('TEXTLINEWIDTH', '6mil')))
        if props.get('TEXT1MIRROR', 'FALSE') == 'TRUE':
            geo = scale(geo, xfact=-1, yfact=1, origin=(0, 0))
            text_follows = [scale(line, xfact=-1, yfact=1, origin=(0, 0)) for line in text_follows]
        text_angle = float(props.get('TEXT1ANGLE', angle))
        offset = [self._unit(props['TEXT1X']) - self.origin[0], self._unit(props['TEXT1Y']) - self.origin[1]]
        geo = rotate(geo, text_angle, origin=(0, 0))
        text_follows = [rotate(line, text_angle, origin=(0, 0)) for line in text_follows]
        span = None
        if not geo.is_empty:
            # the text ink in the frame of the dimension line: along the axis and across it, from the anchor
            points = [(x + offset[0] - anchor[0], y + offset[1] - anchor[1])
                      for x, y in get_coordinates(geo.convex_hull)]
            along = [x * axis[0] + y * axis[1] for x, y in points]
            across = [x * normal[0] + y * normal[1] for x, y in points]
            if min(across) - text_gap - width / 2 < 0 < max(across) + text_gap + width / 2:
                shift = -(min(across) + max(across)) / 2
                offset = [offset[0] + shift * normal[0], offset[1] + shift * normal[1]]
                span = (min(along), max(along))
        self._add(layer, translate(geo, xoff=offset[0], yoff=offset[1]))
        for line in text_follows:
            follow = translate(line, xoff=offset[0], yoff=offset[1])
            self.layers[layer]['follow_geometry'].append(follow)
            text_width = self._unit(props.get('TEXTLINEWIDTH', '6mil'))
            solid = follow.buffer(text_width / 2, quad_segs=self._segs(text_width / 2))
            self._primitives.stroke(layer, solid, follow, text_width)
        return span

    def _font_geometry(self, text, name, style, height):
        """Use native FreeType contours; containment parity retains counters/islands."""
        import freetype
        from freetype.ft_enums.ft_load_flags import FT_LOAD_FLAGS
        from appParsers.ParseFont import ParseFont

        if height <= 0:
            raise PcbDocParseError('Non-positive text height')
        font = self._fonts.get((name, style))
        if font is None:
            if self._installed_fonts is None:
                fonts = ParseFont(self.app)
                fonts.get_fonts_by_types()
                self._installed_fonts = {'Regular': fonts.regular_f, 'Bold': fonts.bold_f,
                                         'Italic': fonts.italic_f, 'Bold Italic': fonts.bold_italic_f}
            mapping = self._installed_fonts[style]
            if name not in mapping:
                raise PcbDocParseError('Source font is not installed or embedded: %s %s' % (name, style))
            font = mapping[name]
        face = freetype.Face(io.BytesIO(font)) if isinstance(font, bytes) \
            else freetype.Face(font, index=getattr(font, 'face_index', 0))
        # The text height is the full height of the font, the ascender to the descender (checked against the
        # Gerber files of CircuitMaker): the cap height is that part of it.
        full = face.ascender - face.descender
        face.load_glyph(face.get_char_index('H'), FT_LOAD_FLAGS['FT_LOAD_NO_SCALE'])
        if full > 0:
            height *= face.glyph.metrics.height / full
        face.set_char_size(64 * 100)
        load_flags = FT_LOAD_FLAGS['FT_LOAD_NO_HINTING'] | FT_LOAD_FLAGS['FT_LOAD_NO_BITMAP']
        cap = face.get_char_index('H')
        face.load_glyph(cap, load_flags)
        cap_height = face.glyph.metrics.height
        if cap_height <= 0:
            raise PcbDocParseError('Font has no usable cap height')
        factor = height / cap_height
        pen_x = 0
        pen_y = 0
        geometry = []
        # no kerning: the Gerber files of CircuitMaker place each glyph at the advance of the one before it
        for char in text.replace('\r\n', '\n'):
            if char == '\n':
                pen_x = 0
                pen_y -= height * 1.5 / factor
                continue
            glyph = face.get_char_index(char)
            if not glyph:
                raise PcbDocParseError('Font %s has no glyph for %r' % (name, char))
            face.load_glyph(glyph, load_flags)
            outline = face.glyph.outline
            contours = []
            start = 0
            for end in outline.contours:
                tags = outline.tags[start:end + 1]
                if any(tag & 2 for tag in tags):
                    raise PcbDocParseError('Cubic font outlines require saved geometry')
                vertices = ParseFont._interpolate_contour(outline.points[start:end + 1], tags,
                                                          segments=max(8, self._steps(height / 2) // 8))
                contours.append(self._polygon(vertices))
                start = end + 1
            result = Polygon()
            # Outer contours before holes before nested positive islands.
            for contour in sorted(contours, key=lambda polygon: polygon.area, reverse=True):
                depth = sum(other.area > contour.area and other.covers(contour) for other in contours)
                result = result.union(contour) if depth % 2 == 0 else result.difference(contour)
            geometry.append(translate(result, xoff=pen_x, yoff=pen_y))
            pen_x += face.glyph.advance.x
        geo = scale(unary_union(geometry), xfact=factor, yfact=factor, origin=(0, 0))
        # the ink of the first glyph starts at the text position, not its left side bearing; the baseline is at it
        return geo if geo.is_empty else translate(geo, xoff=-geo.bounds[0])

    def _plane_cuts(self, layer):
        """
        The clearances and the thermal reliefs of a plane layer, checked against the Gerber files of CircuitMaker:
        only a hole reaches a plane, so a pad without a hole makes no cut and the clearance starts at the hole edge.
        Each split plane is a net domain; outside them the net of the whole plane applies.
        """
        plane_name = self._plane_net(layer)
        # the smallest split plane first: the net domain that holds a pad
        splits = sorted(self._splits.get(layer, []), key=lambda split: split[1].area)
        cuts = []
        for pad in self._pads:
            hole = pad['hole']
            if layer not in pad['shapes'] or hole.is_empty:
                continue
            context = dict(pad['context'], layer=layer)
            rule = self._rule('PlaneClearance', context)
            if not rule or 'CLEARANCE' not in rule:
                raise PcbDocParseError('Missing applicable PlaneClearance rule/value')
            clearance = self._unit(rule['CLEARANCE'])
            if clearance < 0:
                raise PcbDocParseError('Negative plane clearance')
            antipad = hole.buffer(clearance, quad_segs=self._segs(clearance))
            center = hole.centroid
            domain = next((net for net, area in splits if area.covers(center)), plane_name)
            if context['net'] and domain is None:
                raise PcbDocParseError('Unavailable plane %d net assignment for connected pad/via' % layer)
            connect = None
            if domain and context['net'] == domain:
                # A connection is always between the same net: a rule only for different nets does not apply.
                connect = next((value for value in self._rules if value.get('RULEKIND') == 'PlaneConnect'
                                and value.get('NETSCOPE', 'AnyNet') != 'DifferentNets'
                                and self._scope(value.get('SCOPE1EXPRESSION', 'All'), context)), None)
            if connect is None:
                cuts.append(antipad)
                continue
            # Altium saves the PlaneConnect rule with PLANECONNECTSTYLE, RELIEFEXPANSION and RELIEFAIRGAP
            style = connect.get('PLANECONNECTSTYLE', connect.get('CONNECTSTYLE'))
            if style == 'Direct':
                continue
            if style == 'NoConnect':
                cuts.append(antipad)
            elif style == 'Relief':
                # Not checked against Gerber files: the example boards have no relief connection to a plane.
                expansion = self._unit(connect.get('RELIEFEXPANSION', '20mil'))
                gap = self._unit(connect.get('RELIEFAIRGAP', connect.get('AIRGAPWIDTH', '10mil')))
                width = self._unit(connect.get('RELIEFCONDUCTORWIDTH', '10mil'))
                entries = int(connect.get('RELIEFENTRIES', 4))
                if entries not in (2, 4) or width <= 0 or gap <= 0 or expansion <= 0:
                    raise PcbDocParseError('Unsupported plane thermal relief')
                outer = hole.buffer(expansion, quad_segs=self._segs(expansion))
                inner = hole.buffer(max(expansion - gap, 0), quad_segs=self._segs(expansion))
                radius = outer.bounds[2] - outer.bounds[0]
                spokes = [rotate(box(center.x, center.y - width / 2, center.x + radius,
                                     center.y + width / 2), i * 360 / entries, origin=center)
                          for i in range(entries)]
                cuts.append(outer.difference(inner).difference(unary_union(spokes)))
            else:
                raise PcbDocParseError('Unsupported plane connection style: %s' % style)
        return cuts

    def _finish(self):
        cutouts = unary_union(self._cutouts)
        for layer in self.copper_layers:
            if not 39 <= layer <= 54:
                continue
            negative = unary_union(self.layers.get(layer, {}).get('solid_geometry', []))
            copper = self.board_outline.difference(unary_union([negative, *self._plane_cuts(layer)]))
            if layer in self.layers:
                self.layers[layer]['solid_geometry'] = []
            self._add(layer, copper)
        for layer, result in list(self.layers.items()):
            geo = unary_union(result['solid_geometry'])
            if (layer in self.copper_layers or 35 <= layer <= 38) and not cutouts.is_empty:
                geo = geo.difference(cutouts)
            polygons = self._polygons_of(geo)
            # Exporter emits a shell followed by LPC interiors. Enclosing polygons
            # MUST precede islands, otherwise an enclosing hole erases the island.
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            result['solid_geometry'] = polygons
            result['tools'] = {0: {'type': 'REG', 'size': 0.0,
                                    'geometry': [{'solid': polygon} for polygon in polygons]}}
            self._primitives.finish(layer, result)
            if not polygons:
                del self.layers[layer]
