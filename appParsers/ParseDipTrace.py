# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing             #
# Native DipTrace PCB (.dip) geometry reader               #
# MIT Licence                                             #
# ##########################################################

"""Read native DipTrace boards, without DipTrace or companion CAM files.

Numbers are big-endian: three-byte integers have bias 1,000,000; four-byte
coordinates have bias 1,000,000,000 and 30,000 units/mm. Strings are a two-byte
character count followed by UTF-16BE in v39 and later. Version 37 uses a biased
three-byte byte count and single-byte text (only ASCII is verified). Output
preserves native X and reverses Y to use FlatCAM's Y-up frame.

Import saved copper, not a newly calculated pour: the cache consists of hatch
segments, open/closed boundary strokes and thermal spokes. Refilling its outline
would lose clearances and island decisions. Boards without pours are valid too.
Documentation graphics use the native component and board records. TrueType
text uses the requested installed font through FlatCAM's font renderer; native
vector text uses the approximate PCB stroke font with an accuracy warning.
Primary net tables retain pad membership, class references and local routing
defaults. Counted net-owned traces retain groups and circular arc midpoints;
unresolved segment geometry is rejected rather than imported as straight copper.
Saved v54/v60 teardrop contours are added as copper; an unframed table is reported.

Compatibility is layout-validated, not inferred from a version range. Native
v54/v60 common and explicit open/tented/solder/no-solder pad openings, mask by
paste, saved paste rectangles, copper offsets, pad angles and terminal outlines
are supported. Plane layers retain saved fills and their By Pads / Fixed Ring
plated-hole geometry; a Plane flag alone never invents a board-wide copper fill.
Modern rectangular pads and terminals retain their dimensional corner radius,
including expanded mask and shrunken paste outlines. D-shaped copper and terminal
outlines are supported; per-layer custom stacks remain unsupported. Versions 49, 50, 53 and 58
use the v54/v60 records. Versions 37, 38, 39, 40, 41, 44, 45 and 46 use
primary quarter-turn placement records. Legacy openings use explicit offsets
or verified project defaults; unresolved inherited offsets are reported.
Legacy automatic markings aren't reconstructed from application-wide preferences.
Routed via styles retain their copper-layer spans and separate blind/buried
drill groups; no drill depth is invented from the copper-layer ids.
Legacy pictures are drawn from their sidecar BMP (<board>.dipb<N>) beside the
board file; missing sidecars and modern raster-only pictures are reported.
Record interpretation here comes from fixture inspection and the published
DipTrace PCB/Pattern XML field semantics, not an external importer algorithm.
"""

import math
import os
import re

import numpy as np
from PIL import Image
from shapely import LineString, Point, Polygon, box, make_valid
from shapely.affinity import rotate, scale, translate
from shapely.ops import unary_union

from appParsers.ParseKiCadPcb import KiCadPcbParser


class DipTraceParseError(ValueError):
    """Malformed or unsupported native board; no partial manufacturing output."""


class _Reader:
    def __init__(self, data, pos=0, end=None):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def take(self, count):
        if count < 0 or self.pos < 0 or self.pos + count > self.end:
            raise DipTraceParseError('Truncated DipTrace record at byte %d' % self.pos)
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def number(self, size=3):
        return int.from_bytes(self.take(size), 'big') - (1000000 if size == 3 else 1000000000)

    def count(self, stride=1):
        count = self.number()
        if count < 0 or count * stride > self.end - self.pos:
            raise DipTraceParseError('Invalid DipTrace record count at byte %d' % (self.pos - 3))
        return count

    def string(self, limit=32768):
        legacy = self.data[:11] == b'\x07DTBOARD\x0f\x42\x65'
        count = self.count() if legacy else int.from_bytes(self.take(2), 'big')
        if count > limit:
            raise DipTraceParseError('Invalid DipTrace string length at byte %d' %
                                    (self.pos - (3 if legacy else 2)))
        if legacy:
            try:
                text = self.take(count).decode('ascii')
            except UnicodeDecodeError as error:
                raise DipTraceParseError('Unsupported DipTrace v37 text code page at byte %d' % self.pos) from error
        else:
            text = self.take(count * 2).decode('utf-16be')
        if any(not c.isprintable() and c not in '\r\n\t' for c in text):
            raise DipTraceParseError('Invalid DipTrace string at byte %d' % self.pos)
        return text

    def point(self):
        result = self.number(4) / 30000, -self.number(4) / 30000
        if max(map(abs, result)) > 10000:
            raise DipTraceParseError('Invalid DipTrace coordinate at byte %d' % (self.pos - 8))
        return result


class DipTraceParser:
    MAGIC = b'\x07DTBOARD'
    BOUNDARY = bytes.fromhex('0f42400f423f0f423f3b9aca00')
    FONT_MARKER = bytes.fromhex('0f42440f42440f4240')
    CHAIN = bytes.fromhex('0000000f423f')
    # Candidate teardrop settings: four three-byte types 0..2, then a ratio's
    # leading byte. Exact ranges are checked by _teardrop_settings().
    TEARDROP_SETTINGS = re.compile(rb'(?=(?:\x0f\x42[\x40-\x42]){4}[\x3b\x3c])')
    # Three display flags (each 0/1) and two zero points follow the two opening
    # offsets: 00 00 01 on most boards, 00 01 01 PCB_2/PCB_4 v54 (192729/178968),
    # 00 01 00 PCB_6 v54 (1661471) and v49 Differential_Pairs (1770067).
    MASK_FRAME = re.compile(rb'(?=[\0\1]{3}(?:\x3b\x9a\xca\x00){4})')
    COMPONENT_LAYERS = ('Top Silk', 'Top Assembly', 'Top Mask', 'Top Paste',
                        'Bottom Paste', 'Bottom Mask', 'Bottom Assembly', 'Bottom Silk',
                        0, 'Top Keepout', 'Bottom Keepout', 1, 'Cutout',
                        'Top Dimension', 'Bottom Dimension', 'Non-Signal',
                        'Top Courtyard', 'Bottom Courtyard', 'Top Outline', 'Bottom Outline',
                        'Top Terminals', 'Bottom Terminals')
    BOARD_LAYERS = ('Top Assembly', 'Top Silk', 'Route Keepout', None, 'Bottom Silk',
                    'Bottom Assembly', 'Top Mask', 'Top Paste', 'Bottom Paste', 'Bottom Mask',
                    'Cutout', 'Placement Keepout', None, 'Top Dimension', 'Bottom Dimension',
                    'Non-Signal', 'Top Courtyard', 'Bottom Courtyard', 'Top Outline',
                    'Bottom Outline', 'Top Terminals', 'Bottom Terminals')
    VERSIONS = (37, 38, 39, 40, 41, 44, 45, 46, 49, 50, 53, 54, 58, 60)
    # v54 placement/marking/pad-mode/extension records. v49 (official BGA_autorouter,
    # Differential_Pairs, BeagleBone_Black) has them too; v46 boards have none.
    MODERN = (49, 50, 53, 54, 58, 60)
    OPENINGS = (45, 46) + MODERN        # global mask/paste offset frame
    PICTURE_PIXELS = 16000000           # larger raster pictures aren't drawn

    def __init__(self, app=None, circle_steps=None, opening_defaults=None):
        self.app = app
        # Older projects inherited these values from the source installation's
        # Gerber export dialog, not from the board. Only use explicitly supplied
        # millimetre values; native project and per-pad values take precedence.
        self.opening_defaults = None if opening_defaults is None else dict(opening_defaults)
        # Via tenting is the export's per-layer "Vias" inclusion (gsettings.dat),
        # not a board, style or route-point field. Tent unless 'open' is chosen.
        self._via_mask = (self.opening_defaults or {}).get('vias', 'tented')
        for name, value in (self.opening_defaults or {}).items():
            if name == 'vias':
                if value not in ('open', 'tented'):
                    raise DipTraceParseError('Invalid DipTrace via mask choice %r' % (value,))
                continue
            if (name not in ('mask', 'paste') or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or not -10 < value < 10):
                raise DipTraceParseError('Invalid DipTrace source %s opening offset (mm)' % name)
        self._geometry = KiCadPcbParser(app=app, circle_steps=circle_steps)
        self._reset()

    def _reset(self):
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []
        self.warnings = []
        self.board_outline = Polygon()
        self.version = None
        self._solids = {}
        self._follows = {}
        self._drills = {}
        self._seen_vias = set()
        self._via_style_table = []
        self._copper_layers = (0, 1)
        self.copper_layers = {}
        self.pad_nets = {}
        self.pad_plane_settings = {}
        self.nets = {}
        self._route_nets = {}
        self._mask_swell = (self.opening_defaults or {}).get('mask')
        self._paste_shrink = (self.opening_defaults or {}).get('paste')
        self.missing_opening_defaults = set()
        # Inherited offsets that opening the tented vias would also need.
        self.via_opening_defaults = set()
        self._font_service = None
        self._marking_font = None
        self._marking_globals = []
        self.documentation_tables = []
        self.differential_pairs = []
        self.pours = []
        self.teardrops = {}
        self._source = None

    def parse_file(self, filename):
        self._reset()
        try:
            with open(str(filename), 'rb') as handle:
                data = handle.read()
        except OSError as error:
            raise DipTraceParseError('Cannot read DipTrace board: %s' % error) from error
        return self.parse_bytes(data, source=filename)

    def parse_bytes(self, data, source=None):
        self._reset()
        # Legacy pictures are BMP files beside the board; without a path they warn.
        self._source = None if source is None else str(source)
        try:
            self._parse(bytes(data))
        except Exception as error:
            self._reset()
            if isinstance(error, DipTraceParseError):
                raise
            raise DipTraceParseError('Cannot read DipTrace board: %s' % error) from error
        return self

    @staticmethod
    def _positions(data, pattern, start=0, end=None):
        end = len(data) if end is None else end
        while start < end:
            pos = data.find(pattern, start, end)
            if pos < 0:
                return
            yield pos
            start = pos + len(pattern)

    def _add(self, layer, shape, follow=None):
        if not shape.is_empty:
            self._solids.setdefault(layer, []).append(shape)
        if follow is not None:
            self._follows.setdefault(layer, []).append(follow)

    def _stroke(self, layer, points, width):
        if not 0 < width < 100:
            raise DipTraceParseError('Invalid DipTrace stroke width: %s' % width)
        self._add(layer, self._geometry._stroke(points, width), LineString(points))

    def _parse(self, data):
        reader = _Reader(data)
        if reader.take(8) != self.MAGIC:
            if data[1:8] == b'DTBOARD':   # tango_1_imported: 0b 'DTBOARD2.21' (DipTrace 2.2 era)
                raise DipTraceParseError('DipTrace board format %s is older than supported (v37 and newer)'
                                         % data[1:12].decode('latin-1'))
            raise DipTraceParseError('Not a native DipTrace PCB board (bad DTBOARD signature)')
        self.version = reader.number()
        if self.version not in self.VERSIONS:
            raise DipTraceParseError('Unsupported DipTrace format version %s; supported versions: %s'
                                     % (self.version, ', '.join(map(str, self.VERSIONS))))
        reader.take(10)
        reader.string()                   # source schematic path; never opened
        reader.take(17)                   # view/origin fields
        vertices = [(reader.point(), reader.take(1)[0]) for _ in range(reader.count(9))]
        if (vertices and len(vertices) < 3) or any(arc not in (0, 1) for _, arc in vertices):
            raise DipTraceParseError('Invalid DipTrace board outline')
        points = []
        for index, (point, midpoint) in enumerate(vertices):
            if midpoint:
                before, after = vertices[index - 1], vertices[(index + 1) % len(vertices)]
                if before[1] or after[1]:
                    raise DipTraceParseError('Adjacent DipTrace outline arc midpoints')
                points.extend(self._geometry._arc_points(before[0], point, after[0])[1:-1])
            else:
                points.append(point)
        if points:
            points.append(points[0])
            self.board_outline = Polygon(points)
            if not self.board_outline.is_valid:
                self.board_outline = self._touching_polygon(points, 'Invalid DipTrace board outline polygon')
                if self.board_outline.geom_type != 'Polygon' or self.board_outline.interiors:
                    raise DipTraceParseError('Invalid DipTrace board outline polygon')
                self.warnings.append('The DipTrace board outline retraces part of its edge; '
                                     'the enclosed area is used as the board edge.')
            if self.board_outline.area <= 0:
                raise DipTraceParseError('Invalid DipTrace board outline polygon')
            self._stroke('Outline', points, 0.01)
        else:
            self.warnings.append('The DipTrace board has no outline; no board edge was invented.')

        reader.take(35)                   # post-outline settings
        names = {}
        layer_count = reader.count(22)
        if not 1 <= layer_count <= 64:
            raise DipTraceParseError('Invalid DipTrace copper-layer count')
        for _ in range(layer_count):
            reader.take(1)
            layer = reader.number()
            reader.take(3)               # RGB
            name = reader.string()
            kind = reader.number()
            # Native PCB_1/5/6, arm and BGA_autorouter examples: the layer
            # tail is PlanePad (3), NetId (3), PlaneRing (4), Locked (1).
            # XML PCB 4.6.1.1 defines these as plated-hole parameters, not
            # image polarity. Plane copper comes from separately saved pours.
            plane_pad, net = reader.number(), reader.number()
            ring, locked = reader.number(4) / 30000, reader.take(1)[0]
            if kind not in (0, 1):
                raise DipTraceParseError('Unsupported DipTrace copper-layer type %s' % kind)
            if kind == 1 and (plane_pad not in (0, 1) or net < -1 or not 0 <= ring < 100 or locked > 1):
                raise DipTraceParseError('Unsupported DipTrace plane plated-hole settings')
            if layer < 0 or layer in names:
                raise DipTraceParseError('Invalid or duplicate DipTrace copper-layer id')
            names[layer] = name
            self.copper_layers[layer] = {'type': kind, 'plane_pad': plane_pad, 'net_id': net, 'ring': ring}
        self._copper_layers = tuple(names)
        post_layers = reader.pos
        font = self._pour_section(data, post_layers)

        components = self._components(data, post_layers, font)
        if not components:
            raise DipTraceParseError('No supported DipTrace component records found')
        self._via_styles(data, post_layers, components[0]['pos'])
        # The primary metadata also supplies the number of user-field marking
        # pairs in modern extension records; it isn't always twelve markings.
        self._legacy_placements(data, components, post_layers)
        if self.version in self.MODERN:
            self._placements(data, components, font)
            self._marking_defaults(data, post_layers, font,
                                   min(c['placement'] for c in components if c['placement'] is not None))
        else:
            self.warnings.append('Legacy DipTrace automatic component markings are not imported; '
                                 'saved explicit graphics are retained.')
        placements = [c['placement'] for c in components if c['placement'] is not None]
        if self.version in self.OPENINGS:
            end = min(placements, default=len(data))
            opening_settings = self._mask_defaults(data, font, end)
            if self.version in self.MODERN:
                self._pad_modes(data, opening_settings, end, components)
        for component in components:
            self._component_geometry(data, component)
        self._read_nets(data, components[-1]['geometry_end'], font)
        route_end = self._routing(data, components[-1]['geometry_end'], font)
        records = self._board_graphics_table(data, route_end or components[-1]['geometry_end'], font)
        self._draw_board_graphics(records)
        if self.version in self.MODERN:
            self._board_extensions(data, max(placements, default=font), records)
        self._pours(data, font)
        self._pair_metadata(data, font)
        if self.version in self.MODERN:
            # Only decode errors: _teardrops() adds copper after the whole frame
            # decodes, so a failure here leaves no partial teardrop copper.
            try:
                self._teardrops(data, components)
            except (DipTraceParseError, UnicodeError):
                self.teardrops = {}
                self.warnings.append('Saved DipTrace teardrops were not imported; '
                                     'the board is imported without them.')

        for layer in dict.fromkeys((*names, *self._solids)):
            polygons = self._geometry._polygons_of(unary_union(self._solids.get(layer, [])))
            polygons.sort(key=lambda p: Polygon(p.exterior).area, reverse=True)
            if polygons:
                self.layers[layer] = {
                    'name': names.get(layer, 'Board Outline' if layer == 'Outline' else str(layer)),
                    'solid_geometry': polygons,
                    'follow_geometry': self._follows.get(layer, []),
                    'tools': {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': p} for p in polygons]}}}
        for group, tools in self._drills.items():
            if tools:
                self.drill_groups[group] = {i: tool for i, (_, tool) in enumerate(sorted(tools.items()), 1)}
        self.default_layers = [layer for layer in self.layers if not str(layer).startswith('Unfilled Copper ')]
        self.warnings = list(dict.fromkeys(self.warnings))

    def _pour_section(self, data, start):
        # View indices and font size vary even within one format version. Decode
        # the counted font/settings frame rather than searching for their values.
        candidates = []
        for pos in self._positions(data, b'\x0f\x42', start):
            try:
                reader = _Reader(data, pos)
                # Check the view indices and the font-name length before decoding
                # a string at each b'\x0f\x42' hit (CNC_controller: 80% of a parse).
                if not all(-1 <= reader.number() <= 64 for _ in range(3)):
                    continue
                font = reader.string(127)
                size, flag = reader.number(), reader.take(1)[0]
                metrics = [reader.number(4) for _ in range(3)]
                if not (0 < len(font) < 128
                        and 0 < size < 1000 and flag <= 1
                        and all(abs(metric) < 10000000 for metric in metrics)):
                    continue
                self._documentation_tables(reader)
                if reader.count(27) <= 64:
                    candidates.append(pos)
            except (DipTraceParseError, UnicodeError):
                continue
        if len(candidates) != 1:
            raise DipTraceParseError('Missing or ambiguous DipTrace board settings / copper-cache section')
        return candidates[0]

    @staticmethod
    def _documentation_tables(reader):
        # Marking v38: count 11515, table 11518..12518, zero I3, pour count 12521.
        # Quick_Start v38: table 68999, pour count 72248. Habitat_V3 v54: table
        # 362230 (5 x 27 cells), pour count 371695. Cells are column-major;
        # the trailer's flags and font metrics vary, so decode, don't pin them.
        tables = []
        count = reader.count(108)
        if count > 64:
            raise DipTraceParseError('Unsupported DipTrace documentation-table count')
        for _ in range(count):
            flags = reader.take(2)
            parameters = tuple(reader.number() for _ in range(5))
            states = reader.take(3)
            metrics = tuple(reader.number(4) for _ in range(2))
            corners = reader.point(), reader.point()
            font, name = reader.string(), reader.string()
            columns = tuple(reader.number(4) / 30000 for _ in range(reader.count(4)))
            rows = tuple(reader.number(4) / 30000 for _ in range(reader.count(4)))
            if (max(flags + states) > 1
                    or not 0 < parameters[2] < 1000 or not 0 < len(font) < 128
                    or not columns or not rows or len(columns) * len(rows) > (reader.end - reader.pos) // 42
                    or any(not 0 < value < 20000 for value in (*columns, *rows))
                    or abs(corners[1][0] - corners[0][0] - sum(columns)) > 1 / 30000
                    or abs(corners[0][1] - corners[1][1] - sum(rows)) > 1 / 30000):
                raise DipTraceParseError('Unsupported DipTrace documentation-table dimensions')
            cells = []
            x = corners[0][0]
            for width in columns:
                y = corners[0][1]
                for height in rows:
                    reader.take(1)
                    size, _, align = reader.number(), reader.number(), reader.number()
                    reader.take(3)
                    text, family = reader.string(), reader.string()
                    rectangle = reader.point(), reader.point()
                    state = reader.take(1)[0]
                    thickness, aspect = reader.number(4) / 10000, reader.number(4) / 10000
                    # A cell off its grid rectangle would shift the copper cache.
                    if (state > 1 or not 0 < size < 1000 or not 0 < len(family) < 128
                            or any(abs(a - b) > 1 / 30000 for a, b in zip(
                                (*rectangle[0], *rectangle[1]), (x, y, x + width, y - height)))):
                        raise DipTraceParseError('Unsupported DipTrace documentation-table cell')
                    cells.append({'text': text, 'font': family, 'corners': rectangle, 'size': size,
                                  'align': align, 'vector': state, 'thickness': thickness, 'aspect': aspect})
                    y -= height
                x += width
            trailer = reader.take(2), reader.number(4), reader.number(4), reader.take(2)
            # The I3 after the trailer is 0 in all three specimens; the real
            # pour count follows it. Unknown content mustn't become a pour count.
            if max(trailer[0] + trailer[3]) > 1 or reader.number() != 0:
                raise DipTraceParseError('Unsupported DipTrace documentation-table trailer')
            tables.append({'name': name, 'font': font, 'corners': corners, 'columns': columns, 'rows': rows,
                           'flags': flags, 'parameters': parameters, 'states': states,
                           'metrics': metrics, 'cells': cells, 'trailer': trailer})
        return tables

    def _pair_metadata(self, data, start):
        # Differential_Pairs v41: 1562692..1584830, 19 complete records.
        # Primary nets already carry expanded copper. Keep center parameters
        # raw rather than guessing their editing semantics or drawing them.
        candidates = {}
        names = {net['name'] for net in self.nets.values()}
        for name in names:
            if not name.endswith('_P') or name[:-2] + '_N' not in names:
                continue
            encoded = name[:-2].encode('utf-16be')
            marker = (len(encoded) // 2).to_bytes(2, 'big') + encoded
            for pos in self._positions(data, marker, start + 4):
                reader = _Reader(data, pos - 4)
                try:
                    count = reader.count(30)
                    if not 0 < count <= len(self.nets) // 2:
                        continue
                    pairs = []
                    for index in range(count):
                        state = reader.take(1)[0]
                        label = reader.string()
                        identity, positive, negative, parameter = [reader.number() for _ in range(4)]
                        color = reader.take(4)
                        if (state > 1 or identity != index or positive == negative
                                or positive not in self.nets or negative not in self.nets
                                or self.nets[positive]['name'] != label + '_P'
                                or self.nets[negative]['name'] != label + '_N'):
                            raise DipTraceParseError('Invalid DipTrace differential-pair membership')
                        endpoints = tuple(tuple(reader.number() for _ in range(4))
                                          for _ in range(reader.count(12)))
                        segments = []
                        for segment_index in range(reader.count(21)):
                            parameters = tuple(reader.number() for _ in range(6))
                            if (parameters[0] != segment_index or parameters[1] < 0
                                    or any(parameters[1] >= len(self.nets[net]['traces'])
                                           for net in (positive, negative))):
                                raise DipTraceParseError('Invalid DipTrace differential-pair trace reference')
                            centers = []
                            points = reader.count(44)
                            if not points:
                                raise DipTraceParseError('Empty DipTrace differential-pair segment')
                            for _ in range(points):
                                xy = reader.point()
                                values = tuple(reader.number() for _ in range(9))
                                flags = reader.take(3)
                                sides = []
                                for _ in range(2):
                                    offsets = tuple((reader.point(), reader.take(1)[0])
                                                    for _ in range(reader.count(9)))
                                    if any(flag > 1 for _, flag in offsets):
                                        raise DipTraceParseError('Invalid DipTrace differential-pair offset')
                                    sides.append(offsets)
                                if values[0] not in self._copper_layers or max(flags) > 1:
                                    raise DipTraceParseError('Invalid DipTrace differential-pair center')
                                centers.append({'point': xy, 'parameters': values, 'flags': flags,
                                                'positive_offsets': sides[0], 'negative_offsets': sides[1]})
                            segments.append({'parameters': parameters, 'centers': centers})
                        pairs.append({'id': identity, 'name': label, 'positive_net': positive,
                                      'negative_net': negative, 'parameters': (parameter,), 'state': state,
                                      'color': color, 'endpoints': endpoints, 'segments': segments})
                    candidates[pos - 4] = pairs
                except (DipTraceParseError, UnicodeError):
                    continue
        if len(candidates) > 1:
            raise DipTraceParseError('Ambiguous DipTrace differential-pair metadata')
        self.differential_pairs = next(iter(candidates.values()), [])

    def _components(self, data, start, end):
        components = []
        for marker in self._positions(data, self.BOUNDARY[3:], start + 3, end):
            pos = marker - 3
            reader = _Reader(data, pos + 14, end)
            # Net records use the same sentinel. Only a complete, valid component header qualifies.
            try:
                library = reader.string()
            except (DipTraceParseError, UnicodeError):
                continue
            if not library and data[reader.pos:reader.pos + 2] != b'\x0f\x42':
                continue
            reader.number()
            reader.take(reader.count(8) * 8)  # default pad-stack polygon
            flags = reader.take(4)
            if max(flags) > 1:
                raise DipTraceParseError('Unsupported DipTrace component flags at byte %d' % pos)
            xy = reader.point()
            reader.take(8)
            if reader.take(1) != b'\0':
                raise DipTraceParseError('Invalid DipTrace component header at byte %d' % pos)
            reader.take(6)
            reader.take(6)
            pattern = reader.string()
            dimensions = reader.take(8)
            reader.take(19)               # default pad dimensions and field state
            reader.take(reader.count(3) * 3)  # counted field IDs, including v41 lists of two or three
            display, ref, value, extra = [reader.string() for _ in range(4)]
            component = {'pos': pos, 'xy': xy, 'bottom': bool(flags[1]), 'ref': ref,
                         'pattern': pattern, 'display': display, 'value': value,
                         'dimensions': dimensions, 'header_end': reader.pos}
            components.append(component)
        for i, component in enumerate(components):
            component['end'] = components[i + 1]['pos'] if i + 1 < len(components) else end
        return components

    def _placements(self, data, components, start):
        # Frame flags are state, not a magic signature. Dimensions, bounded text
        # records and the reference designator identify the component independently
        # of table order. Do not confuse similarly named embedded library patterns.
        suffix = bytes.fromhex('0f42410f42400f4240')
        frames = []
        marking_frames = {}
        for pos in self._positions(data, suffix, start + 14):
            begin = pos - 14
            if max(data[pos - 2:pos]) > 1:
                continue
            try:
                dimensions = _Reader(data, begin, begin + 12)
                if not all(0 < dimensions.number(4) / 30000 < 1000 for _ in range(2)):
                    continue
                if abs(dimensions.number(4) / 10000) > 1000:
                    continue
            except (DipTraceParseError, UnicodeError):
                continue
            for string_count in (2, 3):
                try:
                    reader = _Reader(data, pos + len(suffix))
                    for _ in range(string_count):
                        reader.string()
                    visibility = reader.take(3)
                    size, flag = reader.number(), reader.take(1)[0]
                    if max(visibility) <= 1 and 0 < size < 1000 and flag <= 1:
                        frames.append(begin)
                        marking_frames[begin] = (reader.pos, visibility[2], size, flag)
                        break
                except (DipTraceParseError, UnicodeError):
                    continue
        identities = []
        for component in components:
            encoded = component['ref'].encode('utf-16be')
            name = (len(encoded) // 2).to_bytes(2, 'big') + encoded
            dimensions = component['dimensions']
            dimension_options = (dimensions, dimensions[4:] + dimensions[:4])
            matches = [begin for begin, end in zip(frames, frames[1:] + [len(data)])
                       if data[begin:begin + 8] in dimension_options
                       and self._reference_text(data, name, begin + 23, end)]
            identities.append(matches)
        # Hidden labels have no cached reference string. Native deck65 v18
        # and HabitatControl v4 retain one extension per component, in dense
        # component order. Accept that order only when all dimensions and
        # every available reference agree; reordered named frames still bind
        # by identity below.
        ordered = len(frames) == len(components) and all(
            data[begin:begin + 8] in (component['dimensions'],
                                     component['dimensions'][4:] + component['dimensions'][:4])
            and (not matches or begin in matches)
            for component, begin, matches in zip(components, frames, identities))
        used = set()
        for index, (component, matches) in enumerate(zip(components, identities)):
            if ordered:
                matches = [frames[index]]
            if not matches and not component['pattern']:
                matches = [begin for begin in frames if data[begin:begin + 8] == component['dimensions']
                           and begin not in used]
            if len(matches) != 1 and not component['pattern'] and self._rotation_invariant(data, component):
                component.update(angle=0, placement=None, local_dimensions=component['dimensions'])
                continue
            if len(matches) != 1 or matches[0] in used:
                raise DipTraceParseError('Missing or ambiguous DipTrace placement for %s' % component['ref'])
            begin = matches[0]
            used.add(begin)
            component['placement'] = begin
            marking_start, custom, size, font_all = marking_frames[begin]
            component['marking_font'] = (custom, size, font_all)
            reader = _Reader(data, marking_start)
            markings = []
            for _ in range(12 + 2 * component['additional_fields']):
                show, align, h, v = [reader.number() for _ in range(4)]
                point, angle = reader.point(), math.degrees(reader.number(4) / 10000)
                if (show not in (0, 1, 2) or not 0 <= align <= 8
                        # -1 is an unset alignment: Astable R1..R4 v54 37413.., bluestoothilizer
                        # U2 v54 133329, BGA_autorouter v49 C1 1745451, all (0, 8, 0, -1).
                        or (show != 2 and (h not in (-1, 0, 1, 2) or v not in (-1, 0, 1, 2)))
                        or abs(angle) > 100000):
                    raise DipTraceParseError('Invalid DipTrace component marking settings')
                markings.append((show, align, h, v, point, angle))
            component['markings'] = markings
            component['pad_extension_start'] = reader.pos
            component['placement_end'] = frames[frames.index(begin) + 1] if begin != frames[-1] else len(data)
            component['local_dimensions'] = data[begin:begin + 8]
            radians = _Reader(data, begin + 8, begin + 12).number(4) / 10000
            degrees = math.degrees(radians) % 360
            cardinal = round(degrees / 90) * 90
            component['angle'] = cardinal % 360 if abs(degrees - cardinal) < 0.01 else degrees

    @staticmethod
    def _reference_text(data, name, start, end):
        # A designator is the single line of a component text record: count 1,
        # string, inverted, three-byte field id, visible (rotate v60 C1 11487).
        # The last frame's window reaches the board text table: LazyRolls nema17
        # v50 silk "VDD" at 272938 is followed by a four-byte width, not a field.
        record = b'\x0f\x42\x41' + name
        pos = data.find(record, start, end)
        while pos >= 0:
            tail = data[pos + len(record):pos + len(record) + 5]
            if (len(tail) == 5 and tail[0] <= 1 and tail[4] <= 1
                    and -1 <= int.from_bytes(tail[1:4], 'big') - 1000000 < 1000):
                return True
            pos = data.find(record, pos + 1, end)
        return False

    def _marking_defaults(self, data, start, extension_start, end):
        # The primary settings immediately follow the copper-layer table. The
        # first number is a view index, not a marking flag. The extension's six
        # field records end in a counted additional-field table, directly before
        # the first placement (GPSDO v54, rotate v54/v60, keyboard v54).
        reader = _Reader(data, start)
        reader.take(15)                  # view index and legacy display selectors
        size, font = reader.number(), reader.string()
        reader.point()                  # view position, not the label position
        vector = reader.take(1)[0]
        thickness, aspect = reader.number(4) / 10000, reader.number(4) / 10000
        if vector > 1 or not 0 < size < 1000 or not 0 < aspect < 100:
            raise DipTraceParseError('Invalid DipTrace default marking font')
        self._marking_font = (size, font, vector, thickness, aspect)
        matches = []
        for pos in self._positions(data, b'\x0f\x42', extension_start, end):
            try:
                reader = _Reader(data, pos, end)
                fields = [(reader.number(), reader.number()) for _ in range(12)]
                if any(show not in (1, 2) or not 1 <= align <= 7 for show, align in fields):
                    continue
                for _ in range(reader.count(16)):
                    reader.string()      # display name
                    reader.string()      # normalized field key
                    for _ in range(2):
                        show, align = reader.number(), reader.number()
                        if show not in (1, 2) or not 1 <= align <= 7:
                            raise DipTraceParseError('Invalid DipTrace additional-field marking settings')
                if reader.pos == end:
                    matches.append(fields)
            except (DipTraceParseError, UnicodeError):
                continue
        if len(matches) != 1:
            raise DipTraceParseError('Missing or ambiguous DipTrace global marking settings')
        self._marking_globals = matches[0]

    def _positioned_markings(self, component, records, extensions):
        if 'markings' not in component or self._marking_font is None:
            return set()
        cached = set()
        size, font, vector, thickness, aspect = self._marking_font
        custom, custom_size, font_all = component['marking_font']
        size = custom_size if custom else size
        # XML PCB 4.18.1.8-10 defines Show/Common and Position semantics. Native
        # records have two layer entries per field: RefDes, Name, Value, etc.
        # Only Position has an independently saved anchor/angle. Center ignores
        # those stale coordinates. Other generated alignments need native layout
        # semantics; do not invent their clearance/collision-dependent placement.
        for field, text in ((0, component['ref']), (2, component['display']),
                            (4, component['value']), (6, component['pattern'])):
            for layer_index, suffix in enumerate(('Silk', 'Assembly')):
                index = field + layer_index
                show, align, h, v, point, angle = component['markings'][index]
                global_show, global_align = self._marking_globals[index]
                show, align = show or global_show, align or global_align
                if not text.strip():
                    continue
                saved = [i for i, record in enumerate(records)
                         if record[2] == layer_index and record[3] == text
                         and 700 <= record[0] <= 706]
                # Visibility belongs to the field, not its inactive Position
                # anchor. Generated alignments can retain a cache elsewhere.
                if show == 2:
                    cached.update(saved)
                    continue
                if align == 1:
                    if saved:
                        if not (custom or font_all):
                            continue
                        cached.update(saved)
                    point, angle, h, v = (0, 0), 0, 0, 0
                elif align != 8:
                    if show == 1 and component['pattern'] and not saved:
                        self.warnings.append('DipTrace automatic component markings outside Center/Position '
                                             'with no saved text geometry are not imported; '
                                             'this documentation layer may be incomplete.')
                    continue
                layer = ('Bottom ' if component['bottom'] else 'Top ') + suffix
                matches = []
                if extensions is not None:
                    for i, (record, extension) in enumerate(zip(records, extensions)):
                        if 700 <= record[0] <= 706 and record[2] == layer_index:
                            if (extension[0] and extension[0][0] == point
                                    and extension[6] in ('', text)):
                                matches.append(i)
                if not matches or custom or font_all:
                    cached.update(matches)
                    if h < 0 or v < 0:
                        # An unset alignment on a Position anchor: bluestoothilizer U2's CAM
                        # text fits no Center/Bottom/Top anchor, nor its stale saved record.
                        self.warnings.append('A DipTrace component marking with an unset text alignment '
                                             'is not imported; the documentation layer may be incomplete.')
                        continue
                    self._text(layer, text, point, size, aspect, thickness, h, v,
                               angle=angle, component=component, font=font, vector=vector)
        return cached

    def _legacy_placements(self, data, components, start):
        # Legacy placement belongs to the *preceding* metadata, not the end of
        # the component body. User fields make that metadata variable-length.
        # A candidate must consume the entire metadata up to the library header.
        for component in components:
            matches = []
            end = component['pos'] + 3
            for pos in self._positions(data, b'\x01', start, end - 70):
                try:
                    reader = _Reader(data, pos, end)
                    flags = reader.take(2)
                    categories = [reader.number() for _ in range(3)]
                    turns, group, identity, mounting = [reader.number() for _ in range(4)]
                    flag = reader.take(1)[0]
                    reader.take(12 + 16)  # marking modes and offsets
                    mark_flag, mark_id = reader.take(1)[0], reader.number()
                    origin_flag, origin_id = reader.take(1)[0], reader.number()
                    reader.point()
                    tail = reader.take(2)
                    if (max(*flags, flag, mark_flag, origin_flag, *tail) > 1
                            or not all(-1 <= value <= 65535 for value in categories)
                            or not -10000 < turns < 10000 or not -1 <= group <= 1000000
                            or identity < 0 or mounting not in (0, 1, 2, 3, 10)):
                        continue
                    count = reader.count(7)
                    if count > 128:
                        continue
                    for _ in range(count):
                        reader.string()
                        reader.string()
                        if reader.number() not in (0, 1):
                            raise DipTraceParseError('Invalid DipTrace user-field type')
                    if reader.pos == end:
                        matches.append((turns, count, categories[0]))
                except (DipTraceParseError, UnicodeError):
                    continue
            if len(matches) != 1:
                raise DipTraceParseError('Missing or ambiguous legacy DipTrace placement for %s' % component['ref'])
            # Source object type: 14 free pad, 15 mechanical hole, 16 static via
            # (Habitat v54 258 vias, deck65 v18 310); ordinary parts use <= 10.
            turns, count, object_type = matches[0]
            turns %= 4
            dimensions = component['dimensions']
            component.update(angle=turns * 90, placement=None, additional_fields=count, object_type=object_type,
                             local_dimensions=dimensions[4:] + dimensions[:4] if turns % 2 else dimensions)
            start = component['header_end']

    @staticmethod
    def _rotation_invariant(data, component):
        reader = _Reader(data, component['header_end'], component['end'])
        pads = DipTraceParser._pad_table(reader)
        if len(pads) != 1 or reader.count() != 0:
            return False
        xy, width, height, drill, slot, hole_type, mount, style, vertices, orientation, mask, net, settings = pads[0]
        return (xy == (0, 0) and width == height and style in (0, 1) and hole_type == 0
                and not vertices)

    def _mask_defaults(self, data, start, end):
        # In v45/v46/v54/v60 the two global opening offsets precede the unpositioned
        # dimension-display record. Per-object display records follow it.
        # Native z80_board v45: offsets at 601565/601569, both 3000;
        # logic_probe v46: 367536/367540, both 3000 (0.1 mm).
        # XML PCB spec 4.4.12/13 gives the semantics, not binary locations.
        for pos in (match.start() for match in self.MASK_FRAME.finditer(data, start + 8, end)):
            reader = _Reader(data, pos - 8, pos)
            swell, shrink = reader.number(4) / 30000, reader.number(4) / 30000
            if -10 < swell < 10 and -10 < shrink < 10:
                self._mask_swell, self._paste_shrink = swell, shrink
                return pos
        raise DipTraceParseError('Unsupported DipTrace mask/paste settings record')

    def _pad_modes(self, data, start, end, components):
        # Modern four-state modes supersede the primary pad's old boolean flags.
        # HabitatControl f2850aff v4-5ch: table 743038, U3.6 at 757330,
        # U4.9 at 757462, both [Common, Common, Segments, Common]. The table
        # uses primary pad counts INCLUDING the two unused sentinel records;
        # embedded-library pad modes follow the placed components, not vice versa.
        reader = _Reader(data, start, end)
        # Dimension display extensions precede the table: three flags and
        # zero/two/four saved points (rotate v60 / Habitat v54 / GPSDO v54).
        # Valid biased coordinates can't begin with a flag or mode's first byte.
        while reader.pos < end and data[reader.pos] in (0, 1):
            if max(reader.take(3)) > 1:
                raise DipTraceParseError('Invalid DipTrace dimension-display flags')
            points = 0
            while reader.pos < end and data[reader.pos] not in (0, 1, 15):
                reader.point()
                points += 1
                if points > 4:
                    raise DipTraceParseError('Unsupported DipTrace dimension-display geometry')
            if points not in (0, 2, 4):
                raise DipTraceParseError('Invalid DipTrace dimension-display geometry')
        for component in components:
            count = _Reader(data, component['header_end'], component['end']).count(70)
            if count < 2:
                raise DipTraceParseError('Invalid DipTrace pad table')
            modes = [tuple(reader.number() for _ in range(4)) for _ in range(count)]
            if any(state not in (0, 1, 2, 3) for states in modes for state in states):
                raise DipTraceParseError('Unsupported DipTrace effective mask/paste state in %s' % component['ref'])
            component['pad_modes'] = modes[1:-1]

    @staticmethod
    def _place(geometry, component):
        geometry = rotate(geometry, component['angle'], origin=(0, 0))
        if component['bottom']:
            geometry = scale(geometry, xfact=-1, yfact=1, origin=(0, 0))
        return translate(geometry, *component['xy'])

    def _hole(self, center, width, height=None, angle=0, group='PTH'):
        if not 0 < width < 100:
            raise DipTraceParseError('Invalid DipTrace drill diameter: %s' % width)
        height = width if height is None else height
        if not 0 < height < 100:
            raise DipTraceParseError('Invalid DipTrace slot dimension: %s' % height)
        diameter = min(width, height)
        tool = self._drills.setdefault(group, {}).setdefault(
            diameter, {'tooldia': diameter, 'drills': [], 'slots': []})
        if abs(width - height) < 1e-8:
            tool['drills'].append(center)
        else:
            half = abs(width - height) / 2
            ends = [(-half, 0), (half, 0)] if width >= height else [(0, -half), (0, half)]
            slot = translate(rotate(LineString(ends), angle, origin=(0, 0)), center.x, center.y)
            tool['slots'].append(tuple(Point(point) for point in slot.coords))

    @staticmethod
    def _pad_table(reader):
        count = reader.count(70)
        if count < 2:
            raise DipTraceParseError('Invalid DipTrace pad table')
        pads = []
        for index in range(count):
            orientation = reader.take(1)[0]
            identity = reader.number()
            net = reader.number()
            xy = reader.point()
            reader.string()
            reader.string()
            width, height, drill, slot = [reader.number(4) / 30000 for _ in range(4)]
            hole_type = reader.number()
            mount = reader.take(1)[0]
            style = reader.number()
            pad_flag = reader.take(1)[0]
            vertices = [reader.point() for _ in range(reader.count(8))]
            plane = reader.take(1)
            try:
                hidden = tuple(reader.number() for _ in range(reader.count(3)))
                blind = tuple(reader.number() for _ in range(reader.count(3)))
                thermal = tuple((reader.number(), reader.number(), reader.number(), reader.number(4) / 30000)
                                for _ in range(reader.count(13)))
            except DipTraceParseError as error:
                raise DipTraceParseError('Invalid DipTrace plane pad settings') from error
            if any(layer < 0 for layer in (*hidden, *blind)) or any(
                    layer < -1 or kind not in (0, 1, 2) or spoke not in range(5) or not 0 < width < 100
                    for layer, kind, spoke, width in thermal):
                raise DipTraceParseError('Unsupported DipTrace plane pad settings')
            # Preserve the common mask/paste frame independently of the three
            # variable-length lists (HideRingLay, BlindLay, CustomSpokes).
            mask = plane + bytes.fromhex('0f42400f42400f4240') + reader.take(14)
            if pad_flag > 1:
                raise DipTraceParseError('Unsupported DipTrace pad state')
            settings = {'hidden': hidden, 'blind': blind, 'thermal': thermal,
                        'pad_flag': pad_flag}
            if index in (0, count - 1):
                # The trailing unused pad retains the old next-pad id after
                # deletion (Re-ARM JD1/JP1 both retain 7). It isn't a live pad.
                if identity < -1 or (index == 0 and identity not in (-1, 0)):
                    raise DipTraceParseError('Invalid DipTrace default-pad sentinel')
                continue                # unused records may still contain names/polygons
            if identity != index:
                raise DipTraceParseError('Invalid DipTrace pad index at byte %d' % reader.pos)
            if not (0 < width < 100 and 0 < height < 100 and style in (0, 1, 2, 3, 4)
                    and hole_type in (0, 1) and mount in (0, 1) and orientation in (0, 1) and net >= -1):
                raise DipTraceParseError('Unsupported DipTrace pad at byte %d' % reader.pos)
            pads.append((xy, width, height, drill, slot, hole_type, mount, style, vertices, orientation, mask, net,
                         settings))
        return pads

    def _terminal_shapes(self, reader):
        count = reader.count(30)
        if count > 4:
            raise DipTraceParseError('Invalid DipTrace terminal count')
        shapes = []
        for _ in range(count):
            style, xy = reader.number(), reader.point()
            angle = math.degrees(reader.number(4) / 10000)
            width, height = reader.number(4) / 30000, reader.number(4) / 30000
            corner = reader.number(4) / 1000000
            vertices = [reader.point() for _ in range(reader.count(8))]
            if not (style in (0, 1, 2, 3, 4) and 0 < width < 100 and 0 < height < 100
                    and abs(angle) < 100000 and 0 <= corner <= 0.5):
                raise DipTraceParseError('Unsupported DipTrace terminal shape or corner radius')
            shape = self._pad_outline(style, width, height, vertices, corner=corner * min(width, height))
            shapes.append(translate(rotate(shape, angle, origin=(0, 0)), *xy))
        return unary_union(shapes)

    @staticmethod
    def _paste_segments(reader):
        percent = reader.number(4) / 10000
        edge, gap, side = [reader.number(4) / 30000 for _ in range(3)]
        if not (0 <= percent <= 100 and 0 <= edge < 100 and 0 <= gap < 100 and 0 <= side < 100):
            raise DipTraceParseError('Invalid DipTrace paste-segment settings')
        segments = []
        for _ in range(2):
            rectangles = []
            for _ in range(reader.count(16)):
                a, b = reader.point(), reader.point()
                if a[0] == b[0] or a[1] == b[1]:
                    raise DipTraceParseError('Invalid DipTrace paste-segment rectangle')
                rectangles.append(box(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
            segments.append(unary_union(rectangles))
        return segments

    def _pad_extensions(self, data, component, count):
        if self.version not in self.MODERN or component['placement'] is None:
            return [((0, 0), 0, Polygon(), None, 0)] * count
        reader = _Reader(data, component['pad_extension_start'], component['placement_end'])
        # GPSDO v54: C1 marking table ends at 388980, then the default pad
        # extension and four pad records (including both unused sentinels).
        # GPSDO U1 has forty nonempty Rectangle terminals at 429132 onward.
        # Terminal records also occur in the official patterns.zip library:
        # _qfn_r_p0.50.lib, 287698..287731: count=1, Rectangle, XY, angle,
        # width, height, corner, counted polygon. XML PattEdit sections 9.2/9.3
        # document the geometry semantics, not these native byte locations.
        # Native corners are fractions of the shorter side, not lengths:
        # HabitatControl v4-5ch, 1.21 x 1.57 mm / 206612 -> r=0.25 mm,
        # independently confirmed by its published Gerber OUTLINE0 aperture.
        corner = reader.number(4) / 1000000
        reader.point()                   # default copper offset
        self._terminal_shapes(reader)
        states = [reader.number() for _ in range(4)]
        reader.take(8)                   # default swell/shrink; live pads carry their own
        if not 0 <= corner <= 0.5 or any(state not in (0, 1, 2, 3) for state in states):
            raise DipTraceParseError('Unsupported DipTrace default-pad extension')
        self._paste_segments(reader)
        extensions = []
        for index in range(count + 2):
            reader.number()              # pad group / auxiliary linkage ids
            reader.number()
            corner = reader.number(4) / 1000000
            angle = math.degrees(reader.number(4) / 10000)
            cardinal = round(angle / 90) * 90
            angle = cardinal if abs(angle - cardinal) < 0.01 else angle
            offset = reader.point()
            terminals = self._terminal_shapes(reader)
            segments = self._paste_segments(reader)
            if not 0 <= corner <= 0.5 or abs(angle) > 100000:
                raise DipTraceParseError('Unsupported DipTrace pad corner radius or angle')
            if 0 < index < count + 1:
                extensions.append((offset, angle, terminals, segments, corner))
        component['graphic_extension_start'] = reader.pos
        return extensions

    def _component_geometry(self, data, component):
        reader = _Reader(data, component['header_end'], component['end'])
        pads = self._pad_table(reader)
        component['pad_end'] = reader.pos
        component['padless'] = not pads
        extensions = self._pad_extensions(data, component, len(pads))
        for index, (pad, (offset, pad_angle, terminals, segments, corner)) in enumerate(zip(pads, extensions), 1):
            xy, width, height, drill, slot, hole_type, mount, style, vertices, orient, mask, net, plane = pad
            states = component['pad_modes'][index - 1] if 'pad_modes' in component else mask[11:15]
            corner *= min(width, height)
            self.pad_nets[component['ref'], index] = net
            self.pad_plane_settings[component['ref'], index] = plane
            if (mask[0] != 0 or any(layer not in self._copper_layers
                                   for layer in (*plane['hidden'], *plane['blind']))
                    or any(layer != -1 and layer not in self._copper_layers
                           for layer, _, _, _ in plane['thermal'])):
                raise DipTraceParseError('Unresolved DipTrace per-layer plane pad settings in %s' % component['ref'])
            span = tuple(layer for layer in self._copper_layers if not plane['blind'] or layer in plane['blind'])
            if plane['blind'] and (not span or span != self._copper_layers[
                    self._copper_layers.index(span[0]):self._copper_layers.index(span[-1]) + 1]):
                raise DipTraceParseError('Noncontiguous DipTrace blind pad span in %s' % component['ref'])
            if max(states) > 3 or mask[23] > 1:
                raise DipTraceParseError('Unsupported DipTrace per-pad mask/paste state in %s' % component['ref'])
            if 3 in states[2:4] and segments is None:
                raise DipTraceParseError('Missing native DipTrace segmented-paste extension')
            # The last boolean is the pad's own Side, not a MaskPaste mode.
            # Surface pads can be on the opposite side of their footprint.
            pad_side = mask[23] if self.version in self.MODERN else 0
            angle = pad_angle if segments is not None else (90 if orient else 0)
            shape = translate(self._pad_outline(style, width, height, vertices, corner=corner), *offset)
            shape = self._place(translate(rotate(shape, angle, origin=(0, 0)), *xy), component)
            center = self._place(Point(xy), component)
            outer = self._copper_layers[-1 if bool(component['bottom']) ^ bool(pad_side) else 0]
            for layer in ([outer] if mount else self._copper_layers):
                if layer in plane['hidden'] or (plane['blind'] and layer not in plane['blind']):
                    continue
                copper = shape
                settings = self.copper_layers.get(layer, {})
                if not mount and settings.get('type') == 1 and settings['plane_pad'] == 1:
                    if not 0 < drill < 100 or (hole_type and not 0 < slot < 100):
                        raise DipTraceParseError('Invalid DipTrace plane plated hole')
                    ring = settings['ring']
                    copper = self._pad_outline(1, drill + 2 * ring,
                                               (slot if hole_type else drill) + 2 * ring, [])
                    copper = self._place(translate(rotate(copper, angle, origin=(0, 0)), *xy), component)
                self._add(layer, copper, center)
            terminal_layer = ('Bottom' if bool(component['bottom']) ^ bool(pad_side) else 'Top') + ' Terminals'
            terminals = translate(terminals, *offset)
            self._add(terminal_layer, self._place(translate(rotate(terminals, angle, origin=(0, 0)), *xy), component))
            # Common openings follow mounting type. Explicit states override it,
            # including paste on through pads and opposite-side surface openings.
            for side in (0, 1):
                name = 'Bottom' if side else 'Top'
                local_side = side ^ int(component['bottom'])
                mask_mode, paste_mode = states[local_side], states[2 + local_side]
                common_mask = not mount or local_side == pad_side
                if plane['blind'] and self._copper_layers[-1 if side else 0] not in span:
                    common_mask = False
                common_via = component.get('object_type') == 16 and mask_mode == 0 and common_mask
                if common_via:
                    # Common static via: the import's via choice. Explicit modes win.
                    self.missing_opening_defaults.add('vias')
                    common_mask = self._via_mask == 'open'
                common_paste = bool(mount) and local_side == pad_side
                segmented = paste_mode == 3
                paste_open = paste_mode in (1, 3) or (paste_mode == 0 and common_paste)
                swell, shrink = _Reader(mask, 15, 23).number(4), _Reader(mask, 19, 23).number(4)
                swell = self._mask_swell if swell == -10000000 else swell / 30000
                shrink = self._paste_shrink if shrink == -10000000 else shrink / 30000
                if common_via and swell is None:
                    self.via_opening_defaults.add('mask')
                if ((swell is not None and not -10 < swell < 10)
                        or (shrink is not None and not -10 < shrink < 10)):
                    raise DipTraceParseError('Invalid DipTrace mask/paste offset')
                mask_open = (mask_mode == 1 or (mask_mode == 0 and common_mask)
                             or (mask_mode == 3 and paste_open))
                # v37-v41 contain both explicit zero offsets (Re-ARM v41)
                # and -10000000 inheritance (including every DAP42 v40 pad).
                # Older export defaults could live outside the project. Retain
                # resolved openings and allow explicit source-installation values.
                mask_offset = -shrink if mask_mode == 3 and shrink is not None else swell
                if not (mask_mode == 3 and segmented) and ((mask_mode == 3 and shrink is None)
                                                         or mask_offset is None):
                    if mask_open:
                        self.missing_opening_defaults.add('paste' if mask_mode == 3 else 'mask')
                        self.warnings.append('Legacy DipTrace mask openings with unresolved inherited offsets '
                                             'are omitted; this mask layer may be incomplete.')
                    mask_open = False
                if paste_open and shrink is None and not segmented:
                    self.missing_opening_defaults.add('paste')
                    self.warnings.append('Legacy DipTrace paste openings with unresolved inherited offsets '
                                         'are omitted; this paste layer may be incomplete.')
                    paste_open = False
                paste = Polygon()
                if paste_open:
                    if segmented and segments is not None:
                        paste = segments[local_side]
                    elif shrink is not None:
                        paste = translate(self._pad_outline(style, width, height, vertices, -shrink, corner), *offset)
                    paste = self._place(translate(rotate(paste, angle, origin=(0, 0)), *xy), component)
                if mask_open:
                    # By Paste uses the paste silhouette, not the copper swell.
                    opening = paste
                    if mask_mode != 3 and mask_offset is not None:
                        opening = translate(self._pad_outline(style, width, height, vertices, mask_offset, corner), *offset)
                        opening = self._place(translate(rotate(opening, angle, origin=(0, 0)), *xy), component)
                    self._add(name + ' Mask', opening, center)
                if paste_open:
                    self._add(name + ' Paste', paste, center)
            if not mount and drill > 0:
                group = 'PTH' if span == self._copper_layers else 'PTH_%d-%d' % (span[0], span[-1])
                self._hole(center, drill, slot if hole_type else drill,
                           (-component['angle'] - angle) if component['bottom'] else component['angle'] + angle,
                           group=group)
        self._component_graphics(reader, component)
        self._mount_holes(reader, component)
        component['geometry_end'] = reader.pos

    def _pad_outline(self, style, width, height, vertices, offset=0.0, corner=0.0):
        if style == 4:
            # Official D-shape video (8tvZ8BT7AHg, 70s), SON/terminal QFN
            # libraries and observed native output: flat bottom at angle zero.
            # Wide D pads are upper half ellipses (semi-axis H, flat at -H/2),
            # not rotated tall D pads.
            rx, ry = width / 2, width / 2 if width <= height else height
            cy = height / 2 - ry
            steps = max(2, math.ceil(self._geometry._steps(max(rx, ry)) / 2))
            points = [(rx * math.cos(math.pi * i / steps),
                       cy + ry * math.sin(math.pi * i / steps)) for i in range(steps + 1)]
            if width <= height:
                points.extend([(-rx, -height / 2), (rx, -height / 2)])
            shape = Polygon(points)
            # Preserve normal clearance to the contour, as for custom polygons;
            # native D-specific opening-offset equivalence isn't verified.
            return shape.buffer(offset) if offset else shape
        if style == 2 and not 0 <= corner <= min(width, height) / 2:
            raise DipTraceParseError('Invalid DipTrace rectangular-pad corner radius')
        if style == 3:
            # PCB_1 v38 serializes the same octagon three times. Collapse
            # exact repeated traversals only, never repair arbitrary crossings.
            for count in range(3, len(vertices)):
                if len(vertices) % count == 0 and vertices == vertices[:count] * (len(vertices) // count):
                    vertices = vertices[:count]
                    break
            shape = Polygon(vertices)
            if not shape.is_valid or shape.area <= 0:
                raise DipTraceParseError('Invalid DipTrace custom pad polygon')
            return shape.buffer(offset) if offset else shape
        width, height = width + 2 * offset, height + 2 * offset
        if width <= 0 or height <= 0:
            return Polygon()
        if style == 2:
            if corner:
                return self._geometry._rect_poly(width, height, max(0, corner + offset))
            return box(-width / 2, -height / 2, width / 2, height / 2)
        if style == 0:
            return scale(self._geometry._pad_shape({'shape': 'circle', 'w': width, 'h': width}),
                         xfact=1, yfact=height / width, origin=(0, 0))
        return self._geometry._pad_shape({'shape': 'oval', 'w': width, 'h': height})

    def _mount_holes(self, reader, component):
        count = reader.count(18)
        if count == 0:
            return
        if count < 2 or reader.take(1)[0] > 1:
            raise DipTraceParseError('Invalid DipTrace mechanical-hole block')
        reader.point()                  # unused default record may retain a position
        if any(reader.number(4) for _ in range(2)):
            raise DipTraceParseError('Invalid DipTrace mechanical-hole block')
        for _ in range(count - 2):
            flags = reader.take(2)
            xy = reader.point()
            outer, drill = reader.number(4) / 30000, reader.number(4) / 30000
            if max(flags) > 1 or not 0 < drill <= outer < 100:
                raise DipTraceParseError('Invalid DipTrace mechanical hole')
            center = self._place(Point(xy), component)
            self._hole(center, drill, group='NPTH')
            opening = translate(self._geometry._pad_shape({'shape': 'circle', 'w': drill, 'h': drill}),
                                center.x, center.y)
            for side in ('Top', 'Bottom'):
                self._add(side + ' Mask', opening, center)

    def _component_graphics(self, reader, component):
        dimensions = _Reader(component['local_dimensions'])
        width, height = dimensions.number(4) / 10000, dimensions.number(4) / 10000
        records = []
        for _ in range(reader.count(60)):
            kind = reader.number()
            # Unused handles can hold the 2000000000 sentinel (Motus v46 kind-9
            # polygon @666042 with counted points): checked only where used.
            handles = [(reader.number(4) / 30000, -reader.number(4) / 30000) for _ in range(3)]
            reader.take(1)               # lock state
            layer_id = reader.number()
            text, font = reader.string(), reader.string()
            vector, size = reader.take(1)[0], reader.number()
            aspect, thickness = reader.number(4) / 10000, reader.number(4) / 10000
            horizontal, vertical = reader.number(), reader.number()
            line_width = reader.number(4) / 30000
            points = [(x * width, y * height) for x, y in
                      (reader.point() for _ in range(reader.count(8)))]
            records.append((kind, handles, layer_id, text, font, vector, size, aspect, thickness,
                            horizontal, vertical, line_width, points))
        extensions = self._component_text(reader.data, component, records) if self.version in self.MODERN else None
        cached_markings = self._positioned_markings(component, records, extensions)
        for index, record in enumerate(records):
            (kind, handles, layer_id, text, font, vector, size, aspect, thickness,
             horizontal, vertical, line_width, points) = record
            if kind in (-1, 0) or index in cached_markings:
                continue
            if not points and self.version not in self.MODERN:
                if max(abs(value) for handle in handles for value in handle) > 10000:
                    raise DipTraceParseError('Invalid DipTrace component graphic coordinate')
                handles = [(x * width, y * height) for x, y in handles]
                if kind == 6:
                    points = handles
                elif kind == 7 or 700 <= kind <= 706:
                    points = [((handles[0][0] + handles[1][0]) / 2,
                               (handles[0][1] + handles[1][1]) / 2)]
                else:
                    points = handles[:2]
            if layer_id == -1:
                # BeagleBone v49 U12 @1011749..1012085: five arcs on no layer that
                # duplicate the pin-1 circle of record #1 on Top Silk.
                self.warnings.append('DipTrace component graphics without a layer are not imported.')
                continue
            if not 0 <= layer_id < len(self.COMPONENT_LAYERS):
                raise DipTraceParseError('Unsupported DipTrace component graphic layer %s' % layer_id)
            layer = self.COMPONENT_LAYERS[layer_id]
            if component['bottom']:
                if isinstance(layer, int):
                    layer = 1 - layer
                elif layer.startswith(('Top ', 'Bottom ')):
                    layer = ('Bottom ' if layer.startswith('Top ') else 'Top ') + layer.split(' ', 1)[1]
            if isinstance(layer, int):
                layer = self._copper_layers[-1 if layer else 0]
            if kind == 7 or 700 <= kind <= 706:
                if points:
                    position, angle = points[0], 0
                    spacing, align = 1.2, 0
                    if extensions is not None:
                        native_points, angle, h, v, align, spacing, text = extensions[index]
                        position = native_points[0]
                        horizontal = h if h >= 0 else horizontal
                        vertical = v if v >= 0 else vertical
                    self._text(layer, text, position, size, aspect, thickness, horizontal, vertical,
                               angle=angle, component=component, font=font, vector=vector,
                               spacing=spacing, align=align)
            else:
                shape, follow = self._graphic(kind, points, line_width if line_width > 0 else 0.1524)
                self._add(layer, self._place(shape, component), self._place(follow, component))

    def _component_text(self, data, component, records):
        if not records or component['placement'] is None:
            return None
        # The native extension repeats the primary graphics in declaration order.
        # Point counts come from that primary table; text lines are counted here.
        # Matching individual strings confuses blank, multiline and identical runs
        # with metadata or other labels, and loses independent rotation settings.
        end = component['placement_end']
        positions = (component['graphic_extension_start'],)
        if component['padless']:
            # Mechanical-only patterns can retain deleted pad extensions. They
            # cannot contribute copper; match the complete saved graphic table
            # inside this component's bounded frame, as for legacy stale caches.
            minimum = sum(len(record[-1]) * 8 + 25 for record in records)
            positions = range(component['graphic_extension_start'], end - minimum + 1)
        matches = []
        for pos in positions:
            try:
                reader = _Reader(data, pos, end)
                candidate = []
                for record in records:
                    points = [reader.point() for _ in record[-1]]
                    angle = math.degrees(reader.number(4) / 10000)
                    h, v, align = [reader.number() for _ in range(3)]
                    spacing = reader.number(4) / 10000
                    if (any(value not in (-1, 0, 1, 2) for value in (h, v, align))
                            or not 0 <= spacing < 100 or abs(angle) > 100000):
                        raise DipTraceParseError('Invalid DipTrace component text settings')
                    lines = [reader.string() for _ in range(reader.count(2))]
                    inverted = reader.take(1)[0]
                    field, visible = reader.number(), reader.take(1)[0]
                    if (max(inverted, visible) > 1 or field < -1
                            or (lines[0] if lines else '') != record[3]):
                        raise DipTraceParseError('Invalid DipTrace component graphic extension')
                    if inverted and (record[0] == 7 or 700 <= record[0] <= 706):
                        raise DipTraceParseError('DipTrace inverted component text has no saved vector outline')
                    candidate.append((points, angle, h, v, align, spacing, '\n'.join(lines)))
                matches.append(candidate)
            except (DipTraceParseError, UnicodeError):
                continue
        if len(matches) != 1:
            raise DipTraceParseError('Missing or ambiguous DipTrace component graphic extensions in %s' % component['ref'])
        return matches[0]

    def _graphic(self, kind, points, width):
        if kind in (1, 8) and len(points) >= 2:
            follow = LineString(points)
        elif kind == 6 and len(points) == 3:
            follow = LineString(self._geometry._arc_points(*points))
        elif kind == 2 and len(points) >= 3:
            # Legacy rectangles can retain an edited, non-rectangular contour
            # (v41 bus connectors). Its saved vertices are authoritative.
            follow = LineString(points if points[0] == points[-1] else points + [points[0]])
        elif (kind in (2, 3) and len(points) == 2
              and (points[0][0] == points[1][0] or points[0][1] == points[1][1])):
            # A collapsed outline rectangle/obround still has its line-width
            # stroke (psu-switch v50 board records 32397, 32486, 32575). A
            # zero-size one is a dot of that width, like a zero-length line
            # (Motus v46 component graphic at -22.4744, -8.572).
            if points[0] == points[1]:
                message = 'Zero-size DipTrace outline shapes are drawn as dots of their line width.'
                if message not in self.warnings:
                    self.warnings.append(message)
            follow = LineString(points)
        elif kind in (2, 3, 4, 5) and len(points) == 2:
            (x1, y1), (x2, y2) = points
            if x1 == x2 or y1 == y2:
                # A fill has no stroke: valves v53 U1 @49737, a 0.5 mm dot, is
                # 0.1958 mm2 in TopSilk.gbr. m2c v1 J3 @43062/43124/43248.
                if 'Zero-area filled DipTrace shapes have no area and are not drawn.' not in self.warnings:
                    self.warnings.append('Zero-area filled DipTrace shapes have no area and are not drawn.')
                return Polygon(), LineString(points)
            if kind in (2, 4):
                shape = box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            else:
                shape = self._geometry._pad_shape({'shape': 'oval', 'w': abs(x2 - x1), 'h': abs(y2 - y1)})
                shape = translate(shape, (x1 + x2) / 2, (y1 + y2) / 2)
            if kind in (4, 5):
                return shape, shape.boundary
            follow = shape.boundary
        elif kind == 9 and len(points) >= 3:
            if len(set(points)) == 1:
                # Motus v46 board record @1432863: 36 copies of one point on Top copper.
                message = 'DipTrace polygons of one repeated point have no area and are not drawn.'
                if message not in self.warnings:
                    self.warnings.append(message)
                return Polygon(), LineString(points[:2])
            shape = Polygon(points)
            if not shape.is_valid:
                shape = self._touching_polygon(points, 'Invalid DipTrace graphic polygon')
                return shape, shape.boundary
            return shape, shape.exterior
        else:
            raise DipTraceParseError('Unsupported DipTrace graphic type %s / %s points' % (kind, len(points)))
        return follow.buffer(width / 2, quad_segs=self._geometry._segs(width / 2)), follow

    @staticmethod
    def _touching_polygon(points, message):
        # A saved contour may touch or retrace itself but never cross: deck65
        # main.dip 758613 is a logo with 10 holes as one ring joined by zero-width
        # cut-ins (KiCad CAM region: same 76 vertices, 51.66 mm2); m2c v1_2 @80
        # closes its outline through an existing corner (1.27 mm spur). Noding
        # a crossing adds a vertex the ring lacks: that stays an error.
        noded = unary_union(LineString(list(points) + [points[0]]))
        vertices = set(points)
        if any(xy not in vertices for line in getattr(noded, 'geoms', [noded]) for xy in line.coords):
            raise DipTraceParseError(message)
        repaired = make_valid(Polygon(points))
        shape = unary_union([g for g in getattr(repaired, 'geoms', [repaired])
                             if g.geom_type in ('Polygon', 'MultiPolygon')])
        if shape.is_empty or not shape.is_valid:
            raise DipTraceParseError(message)
        return shape

    def _text(self, layer, text, position, size, aspect, thickness, horizontal, vertical,
              angle=0, mirror=False, component=None, font='', vector=True, spacing=1.2, align=0):
        if not text.strip():
            return
        size = 4 if size < 0 else size
        if not 0 < size < 1000 or not 0 < aspect < 100 or not 0 < spacing < 100:
            raise DipTraceParseError('Invalid DipTrace text dimensions')
        height = size * 25.4 / 72
        stroke = height / {-3: 18, -2: 9, -1: 6}.get(thickness, 9) if thickness < 0 else thickness
        service = None
        if not vector:
            from appParsers.SVG.text import FontService
            if self._font_service is None:
                self._font_service = FontService(self.app)
            service = self._font_service
            if (not service._init() or font.casefold() not in service.lower['regular']):
                self.warnings.append('DipTrace TrueType font %r is unavailable; text uses an approximate '
                                     'PCB stroke font, not a manufacturing-identical substitute.' % font)
                service = None
        runs = []
        for index, line in enumerate(text.split('\n')):
            if service is not None:
                rendered = service.render(line, (font,), False, False)
                if rendered is None:
                    raise DipTraceParseError('Cannot render DipTrace TrueType font %r' % font)
                outline, advance = rendered
                if outline is None:
                    continue
                outline = scale(outline, xfact=height * aspect, yfact=height, origin=(0, 0))
                width = advance * height * aspect
            else:
                outline, follows = self._geometry._text_geometry(
                    line, height * aspect, height, stroke, ['left', 'bottom'], False)
                if outline.is_empty:
                    continue
                width = outline.bounds[2] - outline.bounds[0]
                self.warnings.append('DipTrace vector text uses the existing approximate PCB stroke font; '
                                     'verify glyphs, spacing and alignment before fabrication.')
            runs.append((outline, width, index))
        width = max((run[1] for run in runs), default=0)
        outlines = []
        for outline, advance, index in runs:
            offset = width - advance if align == 1 else (0 if align == 2 else (width - advance) / 2)
            outlines.append(translate(outline, offset, -index * height * spacing))
        solid = unary_union(outlines)
        if solid.is_empty:
            return
        _, y1, _, y2 = solid.bounds
        x = -width if horizontal == 1 else (0 if horizontal == 2 else -width / 2)
        y = -y1 if vertical == 1 else (-y2 if vertical == 2 else -(y1 + y2) / 2)
        solid = translate(solid, x, y)
        if mirror:
            solid = scale(solid, xfact=-1, yfact=1, origin=(0, 0))
        solid = translate(rotate(solid, angle, origin=(0, 0)), *position)
        if component is not None:
            solid = self._place(solid, component)
        self._add(layer, solid)

    def _board_graphics(self, reader):
        records = []
        for index in range(reader.count(57)):
            flags = reader.take(2)
            angle = reader.number() * 90
            reader.take(1)
            layer_id, copper, kind, size, style, net = [reader.number() for _ in range(6)]
            reader.take(9)               # display colors
            width = reader.number(4) / 30000
            points = [reader.point() for _ in range(reader.count(8))]
            text, font = reader.string(), reader.string()
            vector = reader.take(1)[0]
            reader.number()              # group id
            reader.take(1)
            thickness, aspect = reader.number(4) / 10000, reader.number(4) / 10000
            identity = reader.number()
            reader.take(1)
            if (max(flags) > 1 or not 0 <= layer_id < len(self.BOARD_LAYERS)
                    or kind not in range(10) or identity != index):
                raise DipTraceParseError('Invalid DipTrace board graphic')
            layer = copper if layer_id == 3 else self.BOARD_LAYERS[layer_id]
            records.append({'kind': kind, 'layer': layer, 'points': points, 'text': text,
                            'enabled': bool(flags[0]), 'angle': angle, 'size': size,
                            'font': font, 'vector': vector,
                            'horizontal': 0, 'vertical': 0,
                            'thickness': thickness, 'aspect': aspect, 'width': width, 'picture': style})
        return records

    def _board_graphics_table(self, data, start, end):
        candidates = []
        # Counts aren't signatures: SJTwo's 194 graphics encode as 0f4302.
        # Bound candidates by the available minimum-sized records instead.
        for pos in range(start, end - 2):
            count = int.from_bytes(data[pos:pos + 3], 'big') - 1000000
            if not 0 <= count <= (end - pos - 3) // 57:
                continue
            try:
                reader = _Reader(data, pos, end)
                records = self._board_graphics(reader)
                # A counted group table and the view/origin footer terminate the
                # primary board section. Empty or unrouted nets can follow the
                # last routed trace, so its end alone isn't the graphics boundary.
                self._sheet_settings(reader)
                if reader.pos == end:
                    candidates.append(records)
            except (DipTraceParseError, UnicodeError):
                continue
        if len(candidates) != 1:
            raise DipTraceParseError('Missing or ambiguous DipTrace board-graphics table')
        return candidates[0]

    @staticmethod
    def _sheet_settings(reader):
        # Groups, sheet dimensions and six optional title blocks. A title block
        # has a counted grid and text fields, so a fixed footer size loses boards
        # using a non-empty drawing template (including the v40 reference board).
        for _ in range(reader.count(14)):
            if max(reader.take(3)) > 1:
                raise DipTraceParseError('Invalid DipTrace group flags')
            reader.number()
            reader.point()
        reader.take(24)                  # page dimensions/margins
        for _ in range(6):
            reader.string()
            reader.take(8)               # title-block dimensions
            columns, rows = reader.count(4), reader.count(4)
            reader.take((columns + rows) * 4)
            for _ in range(columns * rows):
                reader.number()
                if max(reader.take(4)) > 1:
                    raise DipTraceParseError('Invalid DipTrace title-block border flags')
            for _ in range(reader.count(42)):
                reader.take(13)          # font/content/alignment and style
                reader.string()         # field text
                reader.string()         # font family
                reader.take(25)          # rectangle and font metrics
        if reader.take(1)[0] > 1:
            raise DipTraceParseError('Invalid DipTrace sheet display flag')

    def _draw_board_graphics(self, records):
        for record in records:
            if not record['enabled'] or record['layer'] is None:
                continue
            kind, layer, points, width = record['kind'], record['layer'], record['points'], record['width']
            if kind not in (6, 7):
                mapping = {0: 1, 1: 6, 2: 2, 3: 4, 4: 3, 5: 5, 8: 8, 9: 9}
                shape, follow = self._graphic(mapping.get(kind, -1), points, width if width > 0 else 0.1524)
                self._add(layer, shape, follow)
            elif self.version not in self.MODERN:
                if kind == 6 and points:
                    self._text(layer, record['text'], points[0], record['size'], record['aspect'],
                               record['thickness'], 0, 0, angle=record['angle'],
                               mirror=str(layer).startswith('Bottom '), font=record['font'],
                               vector=record['vector'])
                elif kind == 7:
                    self._picture(layer, points, record['picture'], record['angle'])

    def _picture(self, layer, points, number, angle):
        # Legacy (pre-v54) pictures keep no pixels in the board. DipTrace saves
        # picture N as the BMP "<board stem>.dipb<N>" beside the .dip; N is the
        # record's style field: Habitat_V1 v46 1, z80 v45 1, logic_probe v46 1..4
        # (dipb0 there is an orphan). Exported Gerber (z80 TopSilk, logic_probe
        # Top/BottomSilk): the bitmap is stretched to the 2-point box, pixel row 0
        # at the top, not mirrored on Bottom Silk, and the dark pixels are ink.
        if self._source is None:
            self.warnings.append('DipTrace picture %d on %s is not imported: its image file (.dipb%d) '
                                 'is found only when the board is opened from its file path.'
                                 % (number, layer, number))
            return
        name = '%s.dipb%d' % (os.path.splitext(self._source)[0], number)
        if not os.path.isfile(name):
            self.warnings.append('DipTrace picture %d on %s is not imported: its image file %s '
                                 'was not found beside the board.' % (number, layer, os.path.basename(name)))
            return
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        if len(points) != 2 or angle or min(xs) == max(xs) or min(ys) == max(ys):
            self.warnings.append('DipTrace picture %d on %s is not imported: unsupported placement.' % (number, layer))
            return
        try:
            with Image.open(name) as image:
                # One box per dark pixel run: keep the geometry bounded.
                if image.width * image.height > self.PICTURE_PIXELS:
                    self.warnings.append('DipTrace picture %d on %s is not imported: %d x %d pixels is more '
                                         'than %d MP.' % (number, layer, image.width, image.height,
                                                          self.PICTURE_PIXELS // 1000000))
                    return
                ink = np.asarray(image.convert('L')) < 128
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.warnings.append('DipTrace picture %d on %s is not imported: %s' % (number, layer, error))
            return
        rows, cols = ink.shape
        x0, top = min(xs), max(ys)
        pw, ph = (max(xs) - x0) / cols, (top - min(ys)) / rows
        boxes = []
        for k in range(rows):
            if not ink[k].any():
                continue
            edges = np.flatnonzero(np.diff(np.concatenate(([0], ink[k].astype(np.int8), [0]))))
            y = top - k * ph
            boxes.extend(box(x0 + a * pw, y - ph, x0 + b * pw, y) for a, b in zip(edges[::2], edges[1::2]))
        if boxes:
            self._add(layer, unary_union(boxes))
        self.warnings.append('DipTrace picture %d on %s is drawn from the dark pixels of %s; verify it '
                             'before fabrication.' % (number, layer, os.path.basename(name)))

    def _board_extensions(self, data, start, records):
        if not records:
            return
        # Extension records repeat the primary text and contain counted line and
        # vector-contour arrays. Validate the whole table, not just a matching word
        # in the last component or in the embedded library.
        first = records[0]['text'].encode('utf-16be')
        marker = (len(first) // 2).to_bytes(2, 'big') + first
        # An unnamed shape can have zero TextLines, not one empty string
        # (HabitatControl v4-5ch). Its count starts 17 bytes into the frame.
        positions = [pos - 17 for pos in self._positions(data, bytes.fromhex('0f4240'), start + 17)] if not first else []
        positions.extend(pos - 20 for pos in self._positions(data, marker, start + 20))
        extensions = None
        for pos in positions:
            reader = _Reader(data, pos)
            candidate = []
            try:
                for record in records:
                    angle = math.degrees(reader.number(4) / 10000)
                    horizontal, vertical, align = [reader.number() for _ in range(3)]
                    spacing = reader.number(4) / 10000
                    if (any(value not in (-1, 0, 1, 2) for value in (horizontal, vertical, align))
                            or not 0 < spacing < 100 or abs(angle) > 100000):
                        raise DipTraceParseError('Invalid DipTrace graphic extension')
                    lines = [reader.string() for _ in range(reader.count(2))]
                    if (lines[0] if lines else '') != record['text']:
                        raise DipTraceParseError('DipTrace graphic extension identity mismatch')
                    inverted = reader.take(1)[0]
                    width, height = reader.number(4) / 30000, reader.number(4) / 30000
                    raster = reader.take(1)[0]
                    vector_width, vector_height = reader.number(4) / 30000, reader.number(4) / 30000
                    if max(inverted, raster) > 1 or min(width, height, vector_width, vector_height) < 0:
                        raise DipTraceParseError('Invalid DipTrace picture dimensions')
                    contours = [[reader.point() for _ in range(reader.count(8))]
                                for _ in range(reader.count(3))]
                    candidate.append((angle, horizontal, vertical, align, spacing, lines, inverted, width, height,
                                      vector_width, vector_height, contours))
            except (DipTraceParseError, UnicodeError):
                continue
            extensions = candidate
            break
        if extensions is None:
            raise DipTraceParseError('Missing or malformed DipTrace board-graphic extensions')
        for record, extension in zip(records, extensions):
            angle, horizontal, vertical, align, spacing, lines, inverted, width, height, vw, vh, contours = extension
            if not record['enabled'] or record['layer'] is None:
                continue
            layer, points, kind = record['layer'], record['points'], record['kind']
            if kind not in (6, 7) or not points:
                continue
            if contours:
                if min(width, height, vw, vh) <= 0:
                    raise DipTraceParseError('Invalid DipTrace vector picture scale')
                image = Polygon()
                for contour in contours:
                    if len(contour) >= 3:
                        # Nested contours carry voids. Union would incorrectly fill
                        # counters/holes, so retain the native even-odd silhouette.
                        polygon = make_valid(Polygon(contour))
                        polygon = unary_union(self._geometry._polygons_of(polygon))
                        image = image.symmetric_difference(polygon)
                image = scale(image, xfact=width / vw, yfact=height / vh, origin=(0, 0))
                x = -width if horizontal == 1 else (0 if horizontal == 2 else -width / 2)
                y = height if vertical == 1 else (0 if vertical == 2 else height / 2)
                image = translate(image, x, y)
                if str(layer).startswith('Bottom '):
                    image = scale(image, xfact=-1, yfact=1, origin=(0, 0))
                image = translate(rotate(image, angle, origin=(0, 0)), *points[0])
                self._add(layer, image)
            elif kind == 6:
                if inverted:
                    raise DipTraceParseError('DipTrace inverted text has no saved vector outline')
                self._text(layer, '\n'.join(lines), points[0], record['size'], record['aspect'],
                           record['thickness'], horizontal if horizontal >= 0 else record['horizontal'],
                           vertical if vertical >= 0 else record['vertical'], angle=angle,
                           mirror=str(layer).startswith('Bottom '), font=record['font'],
                           vector=record['vector'], spacing=spacing, align=align)
            else:
                self.warnings.append('A DipTrace picture has no saved vector geometry; '
                                     'the missing/raster-only picture is not imported. No external image is opened.')

    def _via_styles(self, data, start, end):
        # Native v40/v54 records: counted UTF-16 name, state, diameters,
        # and two copper-layer ids. ViaStyle is a positional reference, not
        # a layer id (https://diptrace.com/books/DipTraceXML_Pcb_En.pdf,
        # section 4.11). Resolve span order through the physical copper stack.
        candidates = []
        for pos in self._positions(data, b'\x0f\x42', start, end):
            try:
                reader = _Reader(data, pos, end)
                count = reader.count(17)
                if not 0 < count <= 1024:
                    continue
                styles = []
                for _ in range(count):
                    reader.string()
                    state = reader.take(1)[0]
                    size, drill = reader.number(4) / 30000, reader.number(4) / 30000
                    first, last = reader.number(), reader.number()
                    # -1 is an unset end layer (BeagleBone v49 Default @571): no span.
                    if state > 1 or not 0 < drill <= size < 100 or min(first, last) < -1:
                        raise DipTraceParseError('Invalid DipTrace via style')
                    span = None
                    if first in self._copper_layers and last in self._copper_layers:
                        begin, finish = self._copper_layers.index(first), self._copper_layers.index(last)
                        if begin > finish:
                            raise DipTraceParseError('Reversed DipTrace via span')
                        span = self._copper_layers[begin:finish + 1]
                    # PCB_1 v38 retains an unused Default style ending at
                    # deleted layer 3. Keep its index; reject only a live use.
                    styles.append((size, drill, span))
                candidates.append((pos, reader.pos, styles))
            except (DipTraceParseError, UnicodeError):
                continue
        # A suffix of a multi-style table can look like a separate table.
        # Only outermost fully decoded tables qualify.
        candidates = [c for c in candidates if not any(a < c[0] < b for a, b, _ in candidates)]
        if len(candidates) != 1:
            raise DipTraceParseError('Missing or ambiguous DipTrace via-style table')
        self._via_style_table = candidates[0][2]

    def _read_nets(self, data, start, end):
        # Native DAP42 v40 [33566:46500] and GPSDO v54 [78350:91463]:
        # counted nets contain their pad references and counted trace records.
        # Decode the whole table before accepting a candidate; a name or a
        # trace sentinel alone cannot establish electrical membership.
        candidates = []
        for pos in self._positions(data, b'\1', start + 3, end):
            try:
                reader = _Reader(data, pos - 3, end)
                count = reader.count(52)
                if not count:
                    continue
                nets, routes = {}, {}
                for index in range(count):
                    flags = reader.take(2)
                    fields = [reader.number() for _ in range(5)]
                    width, clearance = [reader.number(4) / 30000 for _ in range(2)]
                    name = reader.string()
                    size, drill = [reader.number(4) / 30000 for _ in range(2)]
                    states = reader.take(8)
                    net_class = reader.number()
                    if (max(flags) > 1 or fields[3] != index or min(fields) < -1
                            or max(fields) > 1000000 or max(states[:5]) > 1
                            or not -1 <= net_class <= 1000000
                            or any(not -100 < value < 100 for value in (width, clearance, size, drill))):
                        raise DipTraceParseError('Invalid DipTrace net header')
                    pads = tuple((reader.number(), reader.number()) for _ in range(reader.count(6)))
                    if any(component < 0 or pad < 1 for component, pad in pads):
                        raise DipTraceParseError('Invalid DipTrace net pad reference')
                    traces = []
                    for _ in range(reader.count(32)):
                        endpoints = [reader.number() for _ in range(6)]
                        kinds = reader.take(2)
                        route_pos = reader.pos
                        route_flags = reader.take(3)
                        group, identity = reader.number(), reader.number()
                        if (max(route_flags) > 1 or group < -1 or identity < 0
                                or any(kind not in (0, 1, 5, 6, 7) for kind in kinds)):
                            raise DipTraceParseError('Invalid DipTrace net trace header')
                        points = reader.count(41)
                        if not points:
                            raise DipTraceParseError('Empty DipTrace net trace')
                        reader.take(points * 41)
                        routes[route_pos] = index
                        traces.append({'id': identity, 'start': (kinds[0], *endpoints[:3]),
                                       'end': (kinds[1], *endpoints[3:])})
                    nets[index] = {'name': name, 'pads': pads, 'width': width, 'clearance': clearance,
                                   'via_size': size, 'via_drill': drill, 'net_class': net_class,
                                   'traces': traces}
                if all(net < 0 or net in nets for net in self.pad_nets.values()):
                    candidates.append((nets, routes))
            except (DipTraceParseError, UnicodeError):
                continue
        if len(candidates) == 1:
            self.nets, self._route_nets = candidates[0]
        elif candidates or any(net >= 0 for net in self.pad_nets.values()):
            raise DipTraceParseError('Missing or ambiguous DipTrace net table')

    def _routing(self, data, start, end):
        pos = start
        route_end = None
        positions = iter(self._route_nets) if self.nets else None
        vias, transitions, joins, point_layers = [], set(), set(), {}
        while True:
            if positions is not None:
                pos = next(positions, -1)
            else:
                # Standalone trace records have no owning table. The three
                # state flags aren't fixed zeros (SJTwo byte 249832).
                pos = data.find(self.CHAIN[3:], pos + 3, end)
                if pos >= 0:
                    pos -= 3
            if pos < 0:
                break
            if max(data[pos:pos + 3]) > 1:
                pos += 6
                continue
            reader = _Reader(data, pos + 6, end)
            chain = reader.number()
            if chain < 0:
                pos += 6
                continue
            count = reader.count(41)
            if count == 0:
                raise DipTraceParseError('Empty DipTrace route chain at byte %d' % pos)
            net = self._route_nets.get(pos)
            nodes = []
            for index in range(count):
                xy = reader.point()
                layer = reader.number()
                reader.take(3)
                width = reader.number(4) / 30000
                via = reader.number(4) / 30000
                via_flags = reader.take(5)
                via_style = reader.number()
                drill = reader.number(4) / 30000
                # The via span (first, last physical copper index), (0, 0) without a via:
                # Banana Pi v54 styles 0/2/4 = (0,5)/(0,1)/(1,4) on 1904/109/217 points,
                # SJTwo v45 (0,3) on 1036, Habitat V4-5ch and CNC (0,1).
                first, last = reader.number(), reader.number()
                flag = reader.take(1)[0]
                # The entering segment uses its second point's width. Native
                # differential-pair traces retain zero on their first point.
                if (layer not in self._copper_layers or (index and not 0 < width < 100)
                        or not (first == last == 0 or 0 <= first < last < len(self._copper_layers))
                        or flag > 1 or via_flags[0] > 1):
                    raise DipTraceParseError('Unsupported DipTrace routing record at byte %d' % reader.pos)
                if index and (via_flags[1:4] != bytes.fromhex('0f4240') or via_flags[4] > 1):
                    raise DipTraceParseError('Unsupported DipTrace routing geometry at byte %d' % reader.pos)
                nodes.append((xy, layer, width, bool(index and via_flags[4])))
                if via_style >= 0 or via_flags[0]:
                    span = self._copper_layers[first:last + 1] if first < last else self._copper_layers
                    vias.append((net, xy, via, drill, via_style, span))
            if pos >= start + 2:
                for index, connected in ((0, data[pos - 2]), (-1, data[pos - 1])):
                    if connected == 1:
                        joins.add((net, nodes[index][0]))
            for index, (previous, current) in enumerate(zip(nodes, nodes[1:])):
                if current[3]:
                    # arm v38 stores circular midpoints between ordinary end
                    # points, just like the board-outline arc representation.
                    if (index + 2 >= len(nodes) or previous[3] or nodes[index + 2][3]
                            or current[1:3] != nodes[index + 2][1:3]
                            or previous[0] == nodes[index + 2][0]):
                        raise DipTraceParseError('Invalid DipTrace routing geometry: incomplete or inconsistent arc')
                    arc = self._geometry._arc_points(previous[0], current[0], nodes[index + 2][0])
                    self._stroke(current[1], arc, current[2])
                elif not previous[3]:
                    self._stroke(current[1], [previous[0], current[0]], current[2])
                for xy in (previous[0], current[0]):
                    point_layers.setdefault((net, xy), set()).add(current[1])
                if index and previous[1] != current[1]:
                    transitions.add((net, previous[0]))
            pos = reader.pos
            route_end = pos
        for net, xy, via, drill, via_style, span in vias:
            # Every route point keeps copied via defaults (Banana Pi: 1173 interior
            # single-layer style-0 points). A via exists at a layer change or a joined
            # end where two layers meet, not at an unconnected crossing or a plated-pad
            # endpoint: CAM drills Habitat V4-5ch 339, LazyRolls da74c017 52, SJTwo 174.
            connection = net, xy
            if connection not in transitions and (
                    connection not in joins or len(point_layers.get(connection, ())) < 2):
                continue
            if via_style >= 0:
                if via_style >= len(self._via_style_table):
                    raise DipTraceParseError('Invalid DipTrace via style %s' % via_style)
                via, drill, span = self._via_style_table[via_style]
                if span is None:
                    raise DipTraceParseError('DipTrace routed via references a missing copper layer')
            if not 0 < drill <= via < 100:
                raise DipTraceParseError('Invalid DipTrace via dimensions')
            key = xy, via, drill, span
            if key not in self._seen_vias:
                self._seen_vias.add(key)
                shape = self._geometry._pad_shape({'shape': 'circle', 'w': via, 'h': via})
                for copper in span:
                    pad = shape
                    settings = self.copper_layers.get(copper, {})
                    if settings.get('type') == 1 and settings['plane_pad'] == 1:
                        diameter = drill + 2 * settings['ring']
                        pad = self._geometry._pad_shape({'shape': 'circle', 'w': diameter, 'h': diameter})
                    self._add(copper, translate(pad, *xy), Point(xy))
                group = 'PTH' if span == self._copper_layers else 'PTH_%d-%d' % (span[0], span[-1])
                self._hole(Point(xy), drill, group=group)
                # A route point has no mask state (Habitat, SJTwo): the import's via choice.
                self.missing_opening_defaults.add('vias')
                if self._mask_swell is None:
                    self.via_opening_defaults.add('mask')
                if self._via_mask == 'open':
                    if self._mask_swell is None:
                        self.missing_opening_defaults.add('mask')
                        self.warnings.append('Legacy DipTrace mask openings with unresolved inherited offsets '
                                             'are omitted; this mask layer may be incomplete.')
                        continue
                    size = via + 2 * self._mask_swell
                    opening = translate(self._geometry._pad_shape({'shape': 'circle', 'w': size, 'h': size}), *xy)
                    for name, copper in (('Top', self._copper_layers[0]), ('Bottom', self._copper_layers[-1])):
                        if copper in span and size > 0:
                            self._add(name + ' Mask', opening, Point(xy))
        return route_end

    def _pours(self, data, font):
        reader = _Reader(data, font + len(self.FONT_MARKER))
        reader.string()
        reader.take(16)                   # font size/metrics
        self.documentation_tables = self._documentation_tables(reader)
        zones = reader.count(27)
        for _ in range(zones):
            start = reader.pos
            flags = reader.take(3)
            width = reader.number(4) / 30000
            clearance = reader.number(4) / 30000
            area = reader.number(4)
            separator, layer, net = [reader.number() for _ in range(3)]
            if separator != -1 or layer not in self._copper_layers:
                raise DipTraceParseError('Unsupported DipTrace copper cache at byte %d' % start)
            if not 0 < width < 100 or not 0 < clearance < 100 or area < 0 or flags[0] != 1 or max(flags) > 2:
                raise DipTraceParseError('Unsupported DipTrace copper pour settings')
            outline = [reader.point() for _ in range(reader.count(8))]
            if len(outline) < 3:
                raise DipTraceParseError('Invalid DipTrace pour outline')
            segments = reader.count(19)
            for _ in range(segments):
                points = [reader.point(), reader.point()]
                reader.number()          # cache region id
                self._stroke(layer, points, width)
            polygons = reader.count(6)
            for _ in range(polygons):
                points = [reader.point() for _ in range(reader.count(8))]
                reader.number()
                # Saved cache boundaries are stroked paths, including two-point
                # slivers. SJTwo v45's Gerbers confirm closure is explicit;
                # joining open endpoints invents copper across clearances.
                if len(points) < 2:
                    raise DipTraceParseError('Invalid DipTrace copper boundary')
                self._stroke(layer, points, width)
            reader.take(14)               # thermal style and hatch spacing
            spokes = reader.count(23)
            for _ in range(spokes):
                points = [reader.point(), reader.point()]
                spoke_width = reader.number(4) / 30000
                reader.number()
                self._stroke(layer, points, spoke_width)
            tail = reader.take(28)        # cache status, island/connection settings and a final value
            # The final value is 1 on Banana_Pi pour 14 and CNC_controller pours
            # 0 and 4, else 0. Its meaning is unproved: keep it, accept only 0/1.
            final = int.from_bytes(tail[-3:], 'big') - 1000000
            if tail[24] not in (0, 1) or final not in (0, 1):
                raise DipTraceParseError('DipTrace copper cache is incomplete; refill in DipTrace before import')
            self.pours.append({'layer': layer, 'net': net, 'final': final})
            if segments or polygons:
                if tail[24] != 1:
                    raise DipTraceParseError('DipTrace copper cache is incomplete; refill in DipTrace before import')
            else:
                if spokes:
                    raise DipTraceParseError('DipTrace copper pour contains spokes without saved fill')
                self._stroke('Unfilled Copper %s' % layer, outline + [outline[0]], width)
                self.warnings.append('A DipTrace copper pour has no saved fill. Its boundary is imported '
                                     'as unchecked documentation, not manufactured copper.')
        if self.documentation_tables:
            self._draw_documentation_tables(reader)

    def _draw_documentation_tables(self, reader):
        # The pour list is followed by a counted list, then 29 bytes and the
        # LineWidth Silk/Table/Titles/BoardOutline values (XML order). With an
        # empty list: Marking 12556, Quick_Start 72283, Habitat_V3 515125, all
        # 0.25/0.25/0.25/0.14 mm (0.14 = Habitat's BoardOutline.gbr aperture).
        # GPSDO v54 has 6 list entries of undecoded size: no width is read.
        width = None
        if reader.count(1) == 0:
            reader.take(29)
            width = [reader.number(4) / 30000 for _ in range(4)][1]
        for table in self.documentation_tables:
            # Habitat_V3 TopAssembly.gbr proves p0 = p1 = p3 = 0 is Top Assembly.
            # Which of them holds the layer is unproved, so other values stay out.
            if any(table['parameters'][index] for index in (0, 1, 3)) or not 0 < (width or 0) < 100:
                self.warnings.append('A DipTrace documentation table uses an unverified layer or line '
                                     'width; its rectangles and text are not imported.')
                continue
            for cell in table['cells']:
                (x1, y1), (x2, y2) = cell['corners']
                # Every cell is its own closed rectangle (Habitat_V3: 135 cells,
                # 540 D10 segments); shared edges are drawn twice natively.
                self._stroke('Top Assembly', [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)], width)
                if cell['align'] not in (0, 1):
                    if cell['text'].strip():
                        self.warnings.append('A DipTrace table cell uses an unverified text alignment; '
                                             'its text is not imported.')
                    continue
                # Align 1 is centred. Align 0: Habitat_V3's size-8 strokes (0.3137
                # mm) are centred 1.157 mm from the left edge, so the ink starts at
                # 1.000 mm. Vertical centring is within 0.25 mm there (rule unproved).
                x = (x1 + x2) / 2
                if cell['align'] == 0:
                    height = cell['size'] * 25.4 / 72
                    thickness = cell['thickness']
                    stroke = height / {-3: 18, -2: 9, -1: 6}.get(thickness, 9) if thickness < 0 else thickness
                    x = x1 + 1.0 + (stroke / 2 if cell['vector'] else 0)
                position = (x, (y1 + y2) / 2)
                self._text('Top Assembly', cell['text'], position, cell['size'], cell['aspect'],
                           cell['thickness'], 2 if cell['align'] == 0 else 0, 0, font=cell['font'],
                           vector=cell['vector'], align=2 if cell['align'] == 0 else 0)

    @staticmethod
    def _teardrop_settings(data, pos):
        # Four types (PadVia, Smd, Trace, TJunc: 0 none, 1 line, 2 curve) and
        # six four-byte ratios /10000 (widths and lengths, 0..1000 %).
        if pos + 36 > len(data):
            return None
        types = tuple(int.from_bytes(data[pos + 3 * i:pos + 3 * i + 3], 'big') - 1000000 for i in range(4))
        ratios = tuple(int.from_bytes(data[pos + 12 + 4 * i:pos + 16 + 4 * i], 'big') - 1000000000
                       for i in range(6))
        if any(kind not in (0, 1, 2) for kind in types) or any(not 0 <= ratio <= 10000000 for ratio in ratios):
            return None
        return types, ratios

    def _teardrops(self, data, components):
        # Saved teardrops (v49..v60): settings for every primary pad including
        # the two sentinels (same count as _pad_modes), then per primary net its
        # settings, one counted record-index list per primary trace and counted
        # records (copper-layer id, DrawX/DrawY, counted absolute XY contour).
        # CNC_controller c3d580ba: 1957951..1997443..2034635, 496 records in 94
        # nets; Habitat f870aa2f 1202807..1250492 and GPSDO 542514..551685 have
        # none. The library records before it aren't framed, so the complete
        # counted frame must decode at exactly one offset after the placements.
        pads = sum(_Reader(data, c['header_end'], c['end']).count(70) for c in components)
        placements = [c['placement'] for c in components if c.get('placement') is not None]
        settings = {}
        for match in self.TEARDROP_SETTINGS.finditer(data, max(placements, default=components[-1]['header_end'])):
            value = self._teardrop_settings(data, match.start())
            if value is not None:
                settings[match.start()] = value
        # Cheap pruning: the pad settings (and the first net's) are contiguous.
        runs = {}
        for pos in sorted(settings, reverse=True):
            runs[pos] = 1 + runs.get(pos + 36, 0)
        needed = pads + (1 if self.nets else 0)
        frames = []
        for pos in sorted(pos for pos, run in runs.items() if run >= needed):
            reader = _Reader(data, pos + 36 * pads)
            nets = {}
            try:
                for index, net in self.nets.items():
                    if reader.pos not in settings:
                        raise DipTraceParseError('Invalid DipTrace net teardrop settings')
                    net_settings = settings[reader.pos]
                    reader.take(36)
                    links = [[reader.number() for _ in range(reader.count(3))] for _ in net['traces']]
                    records = []
                    for _ in range(reader.count(17)):
                        layer, draw = reader.number(), reader.point()
                        points = [reader.point() for _ in range(reader.count(8))]
                        if layer < 0 or not points:
                            raise DipTraceParseError('Invalid DipTrace teardrop record')
                        records.append({'layer': layer, 'draw': draw, 'points': points})
                    if any(not 0 <= ref < len(records) for group in links for ref in group):
                        raise DipTraceParseError('Invalid DipTrace teardrop reference')
                    nets[index] = {'settings': net_settings, 'links': links, 'records': records}
            except (DipTraceParseError, UnicodeError):
                continue
            frames.append((pos, nets))
        if len(frames) != 1:
            raise DipTraceParseError('Missing or ambiguous DipTrace teardrop table')
        pos, nets = frames[0]
        shapes, unmapped = [], 0
        for net in nets.values():
            for record in net['records']:
                # CNC_controller keeps six one-point records (draw 561.408 mm):
                # no area, so no copper is lost.
                if len(record['points']) < 3:
                    continue
                if record['layer'] not in self._copper_layers:
                    unmapped += 1
                    continue
                # Four CNC contours cross themselves. Nonzero and even-odd both
                # fill every ear; make_valid keeps them, buffer(0) wouldn't.
                polygon = make_valid(Polygon(record['points']))
                shapes.append((record['layer'], unary_union(self._geometry._polygons_of(polygon))))
        for layer, shape in shapes:
            self._add(layer, shape)
        self.teardrops = {'pads': [settings[pos + 36 * i] for i in range(pads)], 'nets': nets}
        if unmapped:
            self.warnings.append('%d saved DipTrace teardrops use an unknown copper layer; '
                                 'they are not imported.' % unmapped)
