# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# PADS Layout ASCII/native manufacturing geometry reader   #
# MIT Licence                                             #
# ##########################################################

"""Read PADS ASCII 9.4--10.0/V2005.0 and native SDB 0x2026/0x2027.

Independent implementation from the PADS 9.4 ASCII Format Specification:
https://www.freecalypso.org/pub/CAD/PADS/pdfdocs/Ppcb_ASCII.pdf
Coordinates remain Cartesian, in millimetres. Pours and Default teardrops are
reconstructed, not original PADS CAM artwork. Romansim uses a disclosed substitute.
"""

import math
import re

from shapely import LineString, MultiLineString, Point, Polygon, box
from shapely.affinity import rotate, scale, translate
from shapely.ops import unary_union

from appParsers.ParsePcbDoc import PcbDocParser


class PADSParseError(ValueError):
    """Malformed ASCII or unsupported manufacturing geometry; no partial board."""


class _Rows:
    def __init__(self, rows):
        self.rows = rows
        self.pos = 0

    def __bool__(self):
        return self.pos < len(self.rows)

    def take(self):
        if not self:
            raise PADSParseError('truncated PADS record')
        row = self.rows[self.pos]
        self.pos += 1
        return row

    def fields(self):
        return self.take().split()


class PADSParser(PcbDocParser):
    source_kind = 'pads'
    HEADER = re.compile(r'^!PADS-(?:POWERPCB|LAYOUT)-V(\d+\.\d+)-'
                        r'(BASIC|MILS|METRIC|MM|INCHES|INCH)(?:-250L)?(?:-CP\d+)?!')
    UNITS = {'BASIC': 1 / 1500000, 'MILS': 0.0254, 'METRIC': 1.0,
             'MM': 1.0, 'INCHES': 25.4, 'INCH': 25.4}

    def parse_file(self, filename):
        with open(str(filename), 'rb') as handle:
            data = handle.read()
        if data.startswith(b'\x00\xff'):
            from appParsers.ParsePADSBinary import PADSBinaryReader

            native = PADSBinaryReader(data)
            result = self.parse_text(native.as_ascii())
            for message in native.warnings:
                self._warn(message)
            self._warn('Native PADS artwork is partly reconstructed. Pours are rebuilt from their outlines, the '
                       'clearance rules and four thermal spokes per pad; they can differ from the original PADS '
                       'fill in small slivers. Lettering and attribute labels use substitute glyphs and approximate '
                       'metrics. Drill plating, the clearance rules, the pour thermal line width and the teardrop '
                       'placement are read from the file. Teardrop tapers are approximate straight tapers, the original taper shape is not '
                       'reproduced. Pad thermal/antipad rows and the other thermal settings are decoded but not '
                       'drawn. Zero-width route objects are airwires, not copper. '
                       'Inspect CAM before manufacture.')
            return result
        head = data[:256].decode('ascii', errors='ignore')
        encoding = re.search(r'-CP(\d+)!', head)
        try:
            text = data.decode('utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else
                               ('cp' + encoding[1] if encoding else 'cp1252'))
        except (UnicodeError, LookupError) as error:
            raise PADSParseError('Unsupported or invalid PADS text encoding') from error
        return self.parse_text(text)

    def parse_text(self, text):
        self._reset()
        self._via_positions = set()
        self._outline_solids = []
        self._outline_holes = []
        self._current_net = None
        self._copper_items = []
        self._pad_items = []
        self._track_items = []
        self._teardrops = []
        self._thermal_points = set()
        self._pour_keepouts = {}
        self.pours = []
        lines = text.lstrip('\ufeff').splitlines()
        match = self.HEADER.match(lines[0] if lines else '')
        if match is None:
            raise PADSParseError('Not a PADS Layout ASCII board header')
        self.version = tuple(int(value) for value in match[1].split('.'))
        if not ((9, 4) <= self.version <= (10, 0) or self.version == (2005, 0)):
            raise PADSParseError('Unsupported PADS ASCII version: %s' % match[1])
        self.unit = self.UNITS[match[2]]
        self.basic = match[2] == 'BASIC'
        sections = {}
        section = None
        ended = False
        for line in lines[1:]:
            line = line.strip()
            if not line or line.startswith('*REMARK*'):
                continue
            control = re.match(r'^\*([A-Z][A-Z ]*)\*', line)
            if control:
                key = control[1]
                if key == 'END':
                    ended = True
                    break
                if key == 'SIGNAL' and section == 'ROUTE':
                    sections[section].append(line)
                    continue
                if key in sections and key != 'MISC':
                    raise PADSParseError('Duplicate PADS section: %s' % key)
                section = key
                sections.setdefault(section, [])
            elif section is not None:
                sections[section].append(line)
            else:
                raise PADSParseError('Data outside a PADS section')
        if not ended:
            raise PADSParseError('Missing *END*: truncated PADS file')
        allowed = {'PCB', 'TEXT', 'LINES', 'VIA', 'PARTDECAL', 'PARTTYPE', 'PART', 'ROUTE', 'MISC',
                   'NET', 'CONN', 'REUSE', 'CLUSTER', 'POUR'}
        for key, rows in sections.items():
            if rows and key not in allowed:
                raise PADSParseError('Unsupported PADS section: %s' % key)
        self._has_pours = bool(sections.get('POUR'))
        try:
            self._setup(sections.get('PCB', []), sections.get('MISC', []))
            self._pin_nets = {}
            net = None
            for row in sections.get('ROUTE', []):
                if row.startswith('*SIGNAL*'):
                    net = row.split()[1]
                elif self._pin_pair(row.split()):
                    for pin in row.split()[:2]:
                        if pin in self._pin_nets and self._pin_nets[pin] != net:
                            raise PADSParseError('Conflicting PADS pin nets: %s' % pin)
                        self._pin_nets[pin] = net
            vias = self._vias(_Rows(sections.get('VIA', [])))
            decals = self._decals(_Rows(sections.get('PARTDECAL', [])))
            types = self._part_types(_Rows(sections.get('PARTTYPE', [])))
            self._parts(_Rows(sections.get('PART', [])), types, decals)
            self._lines(_Rows(sections.get('LINES', [])))
            self._routes(_Rows(sections.get('ROUTE', [])), vias)
            self._texts(_Rows(sections.get('TEXT', [])), self.unit)
            self.board_outline = unary_union(self._outline_solids).difference(unary_union(self._outline_holes))
            self._hole_voids, self._mechanical_voids = self._drill_regions()
            self._reconstruct_teardrops()
            self._pours(_Rows(sections.get('POUR', [])))
        except (IndexError, KeyError, TypeError, OverflowError) as error:
            raise PADSParseError('Malformed or truncated PADS geometry: %s' % error) from error
        except ValueError as error:
            if isinstance(error, PADSParseError):
                raise
            raise PADSParseError('Invalid PADS geometry: %s' % error) from error
        for layer, result in self.layers.items():
            polygons = self._polygons_of(unary_union(result['solid_geometry']))
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            result['solid_geometry'] = polygons
            result['tools'] = {0: {'type': 'REG', 'size': 0.0,
                                   'geometry': [{'solid': polygon} for polygon in polygons]}}
        if not self.layers and not self.drill_groups:
            raise PADSParseError('PADS export contains no manufacturing geometry')
        if sections.get('REUSE'):
            raise PADSParseError('PADS reuse instances are not supported')
        self._warn('Only explicitly saved mask/paste shapes are imported; no CAM openings are inferred.')
        return self

    @staticmethod
    def _number(value):
        number = float(value)
        if not math.isfinite(number):
            raise PADSParseError('PADS coordinates and dimensions must be finite')
        return number

    @staticmethod
    def _count(value):
        count = int(value)
        if not 0 <= count <= 32767:
            raise PADSParseError('Invalid PADS record count')
        return count

    def _warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def _setup(self, pcb, misc):
        values = {row.split()[0]: row.split()[1:] for row in pcb}
        if 'MAXIMUMLAYER' not in values:
            raise PADSParseError('Missing PADS MAXIMUMLAYER')
        count = int(values['MAXIMUMLAYER'][0])
        if not 1 <= count <= 32:
            raise PADSParseError('Unsupported PADS copper layer count')
        self.copper_layers = list(range(1, count + 1))
        self._names = {1: 'Top Layer', count: 'Bottom Layer', 'Outline': 'Board Outline',
                       26: 'Top Silkscreen', 27: 'Bottom Silkscreen',
                       21: 'Top Assembly', 22: 'Bottom Assembly',
                       23: 'Top Paste', 24: 'Bottom Paste', 28: 'Top Mask', 29: 'Bottom Mask'}
        self._opposite = {1: count, count: 1, 21: 22, 22: 21, 23: 24, 24: 23,
                          26: 27, 27: 26, 28: 29, 29: 28}
        self._silk = {'Top': 26, 'Bottom': 27}
        self._clearance = self._number(values.get('DEFAULTCLEAR', ['0'])[0]) * self.unit
        self._clearance = max(self._clearance, self._number(values.get('MINCLEAR', ['0'])[0]) * self.unit)
        self._edge_clearance = 0.0
        # A conservative envelope also respects stricter per-net/class clearance overrides.
        for row in misc:
            fields = row.split()
            if len(fields) == 2 and (fields[0].startswith('COPPER_TO_') or fields[0] == 'OUTLINE_TO_COPPER'):
                value = self._number(fields[1]) * self.unit
                if value < 0:
                    raise PADSParseError('Negative PADS copper clearance')
                if fields[0] == 'OUTLINE_TO_COPPER':
                    self._edge_clearance = max(self._edge_clearance, value)
                else:
                    self._clearance = max(self._clearance, value)
        if self._clearance < 0:
            raise PADSParseError('Negative PADS copper clearance')
        if self._clearance == 0:
            self._clearance = 0.2
            if self._has_pours:
                self._warn('PADS pour clearance is absent; reconstruction uses 0.2 mm.')
        self._thermal_width = self._number(values.get('THERLINEWID', ['0'])[0]) * self.unit
        if self._thermal_width < 0:
            raise PADSParseError('Negative PADS thermal spoke width')
        self._thermal_width = self._thermal_width or 0.3
        # LAYER DATA includes nested color blocks; consume only direct fields of each layer.
        active, depth, layer_depth = None, 0, None
        for row in misc:
            if row.startswith('LAYER ') and len(row.split()) == 2 and row.split()[1].isdigit():
                active = int(row.split()[1])
                layer_depth = depth + 1
            elif row == '{':
                depth += 1
            elif row == '}':
                depth -= 1
                if depth < 0:
                    raise PADSParseError('Unbalanced PADS MISC braces')
                if layer_depth is not None and depth < layer_depth:
                    active, layer_depth = None, None
            elif active is not None and depth == layer_depth:
                if row.startswith('LAYER_NAME '):
                    name = row[len('LAYER_NAME '):]
                    self._names[active] = name
                    for side in self._silk:
                        if side in name and 'silk' in name.lower():
                            self._silk[side] = active
                if row == 'PLANE MIXED' and self._has_pours:
                    continue
                if ((row.startswith('PLANE ') and row != 'PLANE NONE') or
                        (row.startswith('LAYER_TYPE ') and 'PLANE' in row)):
                    raise PADSParseError('PADS plane copper requires generated Gerber artwork')
        if depth:
            raise PADSParseError('Unbalanced PADS MISC braces')
        # Named layer pairs override numeric defaults, including 250-layer mode.
        for layer, name in self._names.items():
            if isinstance(layer, int) and 'Top' in name:
                opposite = next((key for key, value in self._names.items()
                                 if value == name.replace('Top', 'Bottom')), None)
                if opposite is not None:
                    self._opposite[layer], self._opposite[opposite] = opposite, layer

    def _add(self, layer, geo, follow=None):
        if geo.is_empty:
            return
        if not geo.is_valid:
            raise PADSParseError('Invalid PADS geometry on layer %s' % layer)
        result = self.layers.setdefault(layer, {'name': self._names.get(layer, 'PADS Layer %s' % layer),
                                                'solid_geometry': [], 'follow_geometry': [], 'tools': {}})
        result['solid_geometry'].extend(self._polygons_of(geo))
        if layer in self.copper_layers:
            self._copper_items.append((layer, self._current_net, geo))
        if follow is not None:
            result['follow_geometry'].append(follow)

    def _arc_points(self, first, last, center, clockwise):
        radius = math.dist(first, center)
        if radius <= 0 or not math.isclose(radius, math.dist(last, center), rel_tol=1e-5, abs_tol=0.0003):
            raise PADSParseError('Noncircular PADS arc')
        start = math.atan2(first[1] - center[1], first[0] - center[0])
        end = math.atan2(last[1] - center[1], last[0] - center[0])
        sweep = -((start - end) % math.tau) if clockwise else (end - start) % math.tau
        if abs(sweep) < 1e-12:
            sweep = -math.tau if clockwise else math.tau
        count = max(2, math.ceil(self._steps(radius) * abs(sweep) / math.tau))
        return [first, *[(center[0] + radius * math.cos(start + sweep * i / count),
                         center[1] + radius * math.sin(start + sweep * i / count))
                        for i in range(1, count)], last]

    def _piece(self, rows, unit, decal=False):
        fields = rows.fields()
        kind, count = fields[0], self._count(fields[1])
        layer_index = 3 if self.version == (2005, 0) or (decal and self.version == (9, 4)) else 4
        if kind == 'TAG':
            if count != 0 or fields[layer_index] not in ('0', '1'):
                raise PADSParseError('Invalid PADS tag')
            return kind, int(fields[layer_index]), Polygon(), LineString(), Polygon()
        width, layer = self._number(fields[2]) * unit, int(fields[layer_index])
        if width < 0 or not 0 <= layer <= 250:
            raise PADSParseError('Invalid PADS piece width/layer')
        vertices = []
        for _ in range(count):
            fields = rows.fields()
            point = tuple(self._number(value) * unit for value in fields[:2])
            if len(point) != 2:
                raise PADSParseError('Invalid PADS vertex')
            vertices.append((point, fields))
        if count < 2:
            raise PADSParseError('PADS piece has fewer than two vertices')
        if kind in ('CIRCLE', 'COPCIR', 'COPCCO', 'BRDCIR', 'KPTCIR', 'CIRCUR'):
            if count != 2:
                raise PADSParseError('PADS circle needs two diameter endpoints')
            first, last = vertices[0][0], vertices[1][0]
            center = Point((first[0] + last[0]) / 2, (first[1] + last[1]) / 2)
            radius = math.dist(first, last) / 2
            solid = center.buffer(radius, quad_segs=self._segs(radius))
            path = LineString(solid.exterior.coords) if not solid.is_empty else LineString()
        else:
            points = []
            for index, (point, fields) in enumerate(vertices):
                points.append(point)
                if len(fields) > 2:
                    if len(fields) != 8 or index + 1 == count:
                        raise PADSParseError('Malformed PADS piece arc')
                    center = ((self._number(fields[4]) + self._number(fields[6])) * unit / 2,
                              (self._number(fields[5]) + self._number(fields[7])) * unit / 2)
                    points.extend(self._arc_points(point, vertices[index + 1][0], center,
                                                   self._number(fields[3]) < 0)[1:-1])
            path = LineString(points)
            if kind not in ('OPEN', 'COPOPN') and points[0] != points[-1]:
                raise PADSParseError('PADS polygon is not closed')
            solid = Polygon(points) if kind not in ('OPEN', 'COPOPN') else Polygon()
        if kind in ('COPCLS', 'COPCIR', 'COPCUT', 'COPCCO', 'BRDCLS', 'BRDCIR', 'KPTCLS', 'KPTCIR', 'CIRCUR'):
            if not solid.is_valid or solid.is_empty:
                raise PADSParseError('Invalid PADS filled polygon')
            geo = solid
        elif kind in ('OPEN', 'CLOSED', 'CIRCLE', 'COPOPN'):
            geo = path.buffer(width / 2, quad_segs=self._segs(width / 2)) if width else Polygon()
        else:
            raise PADSParseError('Unsupported PADS piece: %s' % kind)
        return kind, layer, geo, path, solid

    @staticmethod
    def _placed(geo, position, angle, mirrored):
        geo = rotate(geo, angle, origin=(0, 0))
        if mirrored:
            geo = scale(geo, xfact=-1, yfact=1, origin=(0, 0))
        return translate(geo, *position)

    def _graphics(self, pieces, position=(0, 0), angle=0, mirrored=False, decal=False, board=False):
        if any(piece[0] == 'TAG' for piece in pieces):
            group, opened = [], False
            for piece in pieces:
                if piece[0] != 'TAG':
                    group.append(piece)
                    continue
                if bool(piece[1]) == opened:
                    raise PADSParseError('Unbalanced PADS tags')
                if group:
                    self._graphics(group, position, angle, mirrored, decal, board)
                group, opened = [], bool(piece[1])
            if opened:
                raise PADSParseError('Unclosed PADS tag')
            if group:
                self._graphics(group, position, angle, mirrored, decal, board)
            return
        positive, negative = {}, {}
        for kind, layer, geo, path, solid in pieces:
            if kind.startswith('KPT'):
                targets = self.copper_layers if layer == 0 else [layer]
                for target in targets:
                    target = self._opposite.get(target, target) if mirrored else target
                    self._pour_keepouts.setdefault(target, []).append(self._placed(solid, position, angle, mirrored))
                self._warn('PADS keepouts constrain reconstructed pours; not emitted as artwork.')
                continue
            targets = [layer]
            if decal and kind in ('OPEN', 'CLOSED', 'CIRCLE') and layer in (0, 1, self.copper_layers[-1]):
                targets = [self._silk['Top' if layer != self.copper_layers[-1] else 'Bottom']]
            elif layer == 0:
                targets = list(self.copper_layers)
            if board:
                targets = ['Outline']
                transformed = self._placed(solid, position, angle, mirrored)
                (self._outline_holes if kind in ('BRDCLS', 'BRDCIR') else self._outline_solids).append(transformed)
                geo = path.buffer(0.0005, quad_segs=4)
            for target in targets:
                if mirrored:
                    target = self._opposite.get(target, target)
                transformed = self._placed(geo, position, angle, mirrored)
                if kind in ('COPCUT', 'COPCCO'):
                    negative.setdefault(target, []).append(transformed)
                else:
                    positive.setdefault(target, []).append(transformed)
                if not path.is_empty:
                    result = self.layers.setdefault(target, {'name': self._names.get(target, 'PADS Layer %s' % target),
                                                            'solid_geometry': [], 'follow_geometry': [], 'tools': {}})
                    result['follow_geometry'].append(self._placed(path, position, angle, mirrored))
        for layer, solids in positive.items():
            self._add(layer, unary_union(solids).difference(unary_union(negative.get(layer, []))))
        if set(negative) - set(positive):
            raise PADSParseError('PADS copper cutout has no associated positive copper')

    def _lines(self, rows):
        while rows:
            fields = rows.fields()
            name, kind = fields[:2]
            if kind not in ('LINES', 'BOARD', 'COPPER', 'COPCUT', 'KEEPOUT'):
                raise PADSParseError('Unsupported PADS line item: %s' % kind)
            position = tuple(self._number(value) * self.unit for value in fields[2:4])
            pieces = [self._piece(rows, self.unit) for _ in range(self._count(fields[4]))]
            if kind == 'COPCUT':
                # Unassociated voids constrain generated pours, never pads or routed tracks.
                for _, layer, _, _, solid in pieces:
                    for target in self.copper_layers if layer == 0 else [layer]:
                        self._pour_keepouts.setdefault(target, []).append(translate(solid, *position))
            else:
                self._graphics(pieces, position, board=kind == 'BOARD')
            if len(fields) > 6:
                self._texts(rows, self.unit, count=self._count(fields[6]), position=position)

    def _texts(self, rows, unit, count=None, position=(0, 0), angle=0, mirrored=False):
        remaining = count
        while rows and (remaining is None or remaining > 0):
            fields = rows.fields()
            font, text = rows.take(), rows.take()
            self._draw_text(fields, font, text, unit, position, angle, mirrored)
            if remaining is not None:
                remaining -= 1
        if remaining:
            raise PADSParseError('truncated PADS text')

    def _draw_text(self, fields, font, text, unit, position, angle, mirrored, label=False):
        if font != 'Regular <Romansim Stroke Font>':
            if not label and (int(fields[3]) in self.copper_layers or int(fields[3]) == 0):
                raise PADSParseError('PADS copper text font is unsupported: %s' % font)
            self._warn('PADS non-Romansim text is not imported: %s' % font)
        else:
            self._text_artwork(fields, text, unit, position, angle, mirrored, label)

    LABEL_VISIBLE = ('VALUE', 'FULL_NAME', 'NAME', 'FULL_BOTH', 'BOTH', 'NONE')

    def _label_records(self, rows, count, unit):
        """Read the three-line label entries: attributes line, font line, attribute name."""
        records = []
        for _ in range(count):
            fields = rows.fields()
            font, attribute = rows.take(), rows.take()
            if len(fields) < 10 or fields[0] not in self.LABEL_VISIBLE:
                raise PADSParseError('Invalid PADS attribute label')
            records.append((attribute.replace('. ', '.'), fields[0], fields[1:], font, unit))
        return records

    def _labels(self, defaults, overrides, reference, part_type, position, angle, mirrored):
        """Draw the decal default labels, where a part label replaces the defaults of its attribute."""
        replaced = {record[0] for record in overrides}
        for attribute, visible, fields, font, unit in [r for r in defaults if r[0] not in replaced] + overrides:
            if visible == 'NONE':
                continue
            value = {'Ref.Des.': reference, 'Part Type': part_type}.get(attribute)
            if visible in ('VALUE', 'BOTH', 'FULL_BOTH') and value is None:
                self._warn('PADS label of attribute %s is skipped: the file gives no value for it.' % attribute)
                continue
            text = {'VALUE': value, 'NAME': attribute, 'FULL_NAME': attribute,
                    'BOTH': '%s = %s' % (attribute, value), 'FULL_BOTH': '%s = %s' % (attribute, value)}[visible]
            self._draw_text(fields, font, text, unit, position, angle, mirrored, label=True)
            self._warn('PADS attribute labels are drawn with substitute FlatCAM stroke glyphs and '
                       'approximate metrics; compare them with the original before relying on them.')

    def _label_layer(self, layer, mirrored):
        """Silkscreen layer of an attribute label, or None for a label that must not become copper."""
        if layer != 0 and layer not in self.copper_layers:
            return self._opposite.get(layer, layer) if mirrored else layer
        target = self._opposite.get(layer, layer) if mirrored else layer
        if target == self.copper_layers[0] or target == self.copper_layers[-1]:
            self._warn('PADS attribute labels of copper layers were moved to the silkscreen layers.')
            return self._silk['Top' if target == self.copper_layers[0] else 'Bottom']
        self._warn('PADS attribute labels of inner copper layers or of all layers are not imported: '
                   'labels are never copper.')
        return None

    def _text_artwork(self, fields, text, unit, position, angle, mirrored, label=False):
        from appParsers.ParseKiCadPcb import KiCadPcbParser

        height, width = (self._number(value) * unit for value in fields[4:6])
        if height <= 0 or width <= 0 or fields[6] not in ('N', 'M'):
            raise PADSParseError('Invalid PADS text dimensions/mirror')
        cell, pen, paths = height * 0.69 / 6, 0.0, []
        for char in text:
            if char == ' ':
                pen += cell * 4
                continue
            glyph = self.STROKE_GLYPHS.get(char) or KiCadPcbParser._LOWER_GLYPHS.get(char)
            if glyph is None:
                raise PADSParseError('Unsupported PADS substitute glyph %r' % char)
            paths.extend(LineString([(pen + x * cell, y * cell) for x, y in path])
                         for path in glyph)
            pen += (max(x for path in glyph for x, _ in path) + 2) * cell
        if not paths:
            return
        geo = MultiLineString(paths).buffer(width / 2, quad_segs=self._segs(width / 2))
        hjust, vjust = fields[7:9]
        if hjust not in ('LEFT', 'CENTER', 'RIGHT') or vjust not in ('UP', 'CENTER', 'DOWN'):
            raise PADSParseError('Unsupported PADS text justification')
        xoff = {'LEFT': 0, 'CENTER': -pen / 2, 'RIGHT': -pen}[hjust]
        yoff = {'DOWN': 0, 'CENTER': -height * 0.69 / 2, 'UP': -height * 0.69}[vjust]
        geo = translate(geo, xoff=xoff, yoff=yoff)
        if fields[6] == 'M':
            geo = scale(geo, xfact=-1, yfact=1, origin=(0, 0))
        geo = rotate(geo, self._number(fields[2]), origin=(0, 0))
        geo = translate(geo, *(self._number(value) * unit for value in fields[:2]))
        if label:
            target = self._label_layer(int(fields[3]), mirrored)
            if target is not None:
                self._add(target, self._placed(geo, position, angle, mirrored))
        else:
            targets = self.copper_layers if int(fields[3]) == 0 else [int(fields[3])]
            for target in targets:
                target = self._opposite.get(target, target) if mirrored else target
                self._add(target, self._placed(geo, position, angle, mirrored))
        self._warn('Romansim lettering is substituted with FlatCAM stroke glyphs; case is preserved, '
                   'but glyph shapes, spacing and metrics are approximate, not original PADS artwork.')

    def _stack(self, rows, count):
        result = {}
        for _ in range(count):
            fields = rows.fields()
            layer = int(fields[0])
            if not -2 <= layer <= 250:
                raise PADSParseError('Invalid or duplicate PADS stack layer')
            # Thermal/antipad entries are alternate plane apertures, not extra signal copper.
            if layer in result:
                alternate = ('RT', 'ST', 'RA', 'SA')
                if fields[2] in alternate:
                    continue
                if result[layer][1] not in alternate:
                    raise PADSParseError('Duplicate PADS signal pad on a stack layer')
            result[layer] = fields[1:]
        if not {-2, -1, 0} <= result.keys():
            raise PADSParseError('Incomplete PADS pad stack')
        return result

    def _decals(self, rows):
        result = {}
        while rows:
            fields = rows.fields()
            name, units = fields[:2]
            if units not in ('I', 'M') or name in result:
                raise PADSParseError('Invalid or duplicate PADS decal')
            unit = self.unit if self.basic else (1.0 if units == 'M' else 0.0254)
            pieces = [self._piece(rows, unit, decal=True) for _ in range(self._count(fields[4]))]
            texts = [rows.take() for _ in range(3 * (self._count(fields[7]) if len(fields) > 7 else 0))]
            labels = self._label_records(rows, self._count(fields[8]) if len(fields) > 8 else 0, unit)
            terminals = []
            for _ in range(self._count(fields[5])):
                terminal = rows.take()
                if not terminal.startswith('T'):
                    raise PADSParseError('Missing PADS decal terminal')
                values = terminal[1:].split()
                point = tuple(self._number(value) * unit for value in values[:2])
                terminals.append((point, values[4] if len(values) > 4 else str(len(terminals) + 1)))
            stacks = {}
            for _ in range(self._count(fields[6])):
                pad = rows.fields()
                if pad[0] != 'PAD' or int(pad[1]) in stacks:
                    raise PADSParseError('Invalid PADS PAD definition')
                stacks[int(pad[1])] = self._stack(rows, self._count(pad[2]))
            result[name] = (unit, pieces, terminals, stacks, texts, labels)
        return result

    def _part_types(self, rows):
        result = {}
        while rows:
            fields = rows.fields()
            # V2005 saves a units column before TYPE; later exports omit it.
            start = 4 if self.version == (2005, 0) else 3
            if len(fields) < start + 4 or fields[0] in result:
                raise PADSParseError('Invalid PADS part type')
            if self.version == (2005, 0) and fields[2] not in ('I', 'M'):
                raise PADSParseError('Invalid PADS part type units')
            result[fields[0]] = fields[1].split(':')
            # Gate/pin mappings affect connectivity, not physical terminal order.
            gates, signals, pins = (self._count(value) for value in fields[start:start + 3])
            for _ in range(gates):
                gate = rows.fields()
                if gate[0] != 'G':
                    raise PADSParseError('Missing PADS gate definition')
                remaining = self._count(gate[2])
                while remaining > 0:
                    remaining -= len(rows.fields())
            for _ in range(signals):
                if rows.fields()[0] != 'SIGPIN':
                    raise PADSParseError('Missing PADS signal pin')
            while pins > 0:
                pins -= len(rows.fields())
        return result

    def _pad_shape(self, fields, unit, mounted=False):
        size, kind = self._number(fields[0]) * unit, fields[1]
        if size < 0:
            raise PADSParseError('Negative PADS pad size')
        extra = list(fields[2:])
        angle, offset, corner, length = 0.0, 0.0, 0.0, size
        if kind in ('RF', 'OF'):
            angle = self._number(extra.pop(0))
            length = self._number(extra.pop(0)) * unit
            offset = self._number(extra.pop(0)) * unit
        # V2005's next field is the drill, not the later corner radius.
        if self.version != (2005, 0) and kind in ('RF', 'S') and extra:
            corner = self._number(extra.pop(0)) * unit
        if kind not in ('R', 'S', 'RF', 'OF'):
            raise PADSParseError('Unsupported PADS pad shape: %s' % kind)
        if length < 0 or abs(corner) > min(size, length) / 2:
            raise PADSParseError('Invalid PADS pad dimensions')
        if kind == 'R':
            geo = Point(0, 0).buffer(size / 2, quad_segs=self._segs(size / 2))
        elif kind == 'OF':
            if length < size:
                raise PADSParseError('PADS oval length is shorter than its width')
            half = (length - size) / 2
            geo = LineString([(-half, 0), (half, 0)]).buffer(size / 2, quad_segs=self._segs(size / 2))
        elif corner > 0:
            geo = box(-length / 2 + corner, -size / 2 + corner,
                      length / 2 - corner, size / 2 - corner).buffer(corner, quad_segs=self._segs(corner))
        elif corner < 0:
            x, y, c = length / 2, size / 2, -corner
            geo = Polygon([(-x + c, -y), (x - c, -y), (x, -y + c), (x, y - c),
                           (x - c, y), (-x + c, y), (-x, y - c), (-x, -y + c)])
        else:
            geo = box(-length / 2, -size / 2, length / 2, size / 2) if size and length else Polygon()
        geo = rotate(translate(geo, xoff=offset), angle, origin=(0, 0))
        drill, plated, slotangle, slotlength, slotoffset = 0.0, True, 0.0, 0.0, 0.0
        if mounted and extra:
            drill = self._number(extra.pop(0)) * unit
            if extra and extra[0] in ('P', 'N'):
                plated = extra.pop(0) != 'N'
            if extra:
                if len(extra) != 3:
                    raise PADSParseError('Unsupported PADS slot parameters')
                slotangle = self._number(extra[0])
                slotlength, slotoffset = (self._number(value) * unit for value in extra[1:])
                extra = []
        if extra:
            raise PADSParseError('Unsupported PADS pad parameters')
        return geo, (drill, plated, slotangle, slotlength, slotoffset)

    def _emit_stack(self, stack, terminal, unit, position, angle, mirrored, span=None, via_drill=None):
        span = span or self.copper_layers
        for layer in span + [key for key in stack if key > len(self.copper_layers)]:
            fallback = -2 if layer == span[0] else (0 if layer == span[-1] else -1)
            fields = stack.get(layer, stack.get(fallback))
            geo, _ = self._pad_shape(fields, unit, mounted=fields is stack[-2])
            target = self._opposite.get(layer, layer) if mirrored else layer
            geo = self._placed(translate(geo, *terminal), position, angle, mirrored)
            self._add(target, geo)
            if target in self.copper_layers and not geo.is_empty:
                self._pad_items.append((target, self._current_net, geo))
        _, (diameter, plated, slotangle, slotlength, slotoffset) = self._pad_shape(stack[-2], unit, mounted=True)
        if via_drill is not None:
            diameter, plated = via_drill, True
        center = Point(terminal[0] + slotoffset * math.cos(math.radians(slotangle)),
                       terminal[1] + slotoffset * math.sin(math.radians(slotangle)))
        center = self._placed(center, position, angle, mirrored)
        self._drill((center.x, center.y), diameter, plated, span,
                    slotlength, 180 - angle - slotangle if mirrored else angle + slotangle)

    def _parts(self, rows, types, decals):
        seen = set()
        while rows:
            fields = rows.fields()
            reference, name = fields[:2]
            if reference in seen:
                raise PADSParseError('Duplicate PADS part reference')
            seen.add(reference)
            part_type, separator, modified = name.partition('@')
            if part_type not in types:
                raise PADSParseError('Missing PADS part type: %s' % part_type)
            alternatives = types[part_type]
            selected = int(fields[7])
            if not 0 <= selected < len(alternatives):
                raise PADSParseError('Invalid PADS alternate decal index')
            name = modified if separator else alternatives[selected]
            if name not in decals:
                raise PADSParseError('Missing PADS decal: %s' % name)
            unit, pieces, terminals, stacks, texts, labels = decals[name]
            position = tuple(self._number(value) * self.unit for value in fields[2:4])
            angle, mirrored = self._number(fields[4]), fields[6] == 'M'
            if fields[6] not in ('N', 'M'):
                raise PADSParseError('Invalid PADS part mirror flag')
            self._graphics(pieces, position, angle, mirrored, decal=True)
            self._texts(_Rows(texts), unit, position=position, angle=angle, mirrored=mirrored)
            for index, (terminal, pin_name) in enumerate(terminals, 1):
                stack = stacks.get(index, stacks.get(0))
                if stack is None:
                    raise PADSParseError('Missing PADS terminal pad stack')
                self._current_net = self._pin_nets.get('%s.%s' % (reference, pin_name))
                self._emit_stack(stack, terminal, unit, position, angle, mirrored)
            self._current_net = None
            overrides = self._label_records(rows, self._count(fields[11]) if len(fields) > 11 else 0, self.unit)
            self._labels(labels, overrides, reference, part_type, position, angle, mirrored)

    def _vias(self, rows):
        result = {}
        while rows:
            fields = rows.fields()
            name, diameter, count = fields[:3]
            first, last = (int(value) for value in fields[3:5]) if len(fields) == 5 else (1, self.copper_layers[-1])
            if name in result or first > last or first not in self.copper_layers or last not in self.copper_layers:
                raise PADSParseError('Invalid PADS via definition/span')
            result[name] = (self._number(diameter) * self.unit,
                            self._stack(rows, self._count(count)), list(range(first, last + 1)))
        return result

    def _routes(self, rows, vias):
        path = []
        signal = False
        pair = False

        def finish():
            if not path:
                return
            if len(path) < 2 or path[-1][1] != 65:
                raise PADSParseError('Missing PADS route end at component pin')
            index = 0
            while index < len(path) - 1:
                point, layer, width, flag, extra, drops = path[index]
                last = path[index + 1]
                if flag & 4096:
                    raise PADSParseError('PADS route starts on an arc center')
                if last[3] & 4096:
                    if index + 2 >= len(path) or not last[4] or last[4][0] not in ('CW', 'CCW'):
                        raise PADSParseError('Invalid PADS route arc center')
                    points = self._arc_points(point, path[index + 2][0], last[0], last[4][0] == 'CW')
                    index += 1
                else:
                    points = [point, last[0]]
                if layer == 0:
                    self._warn('PADS export contains unrouted connections; airwires are not copper.')
                elif layer not in self.copper_layers:
                    raise PADSParseError('Invalid PADS route layer')
                elif width <= 0:
                    raise PADSParseError('PADS track has no positive width')
                elif points[0] != points[-1]:
                    follow = LineString(points)
                    self._add(layer, follow.buffer(width / 2, quad_segs=self._segs(width / 2)), follow)
                    self._track_items.append((layer, self._current_net, follow, width))
                    for direction, endpoint, neighbour, marked in (
                            (1, point, points[1], 'N' in drops),
                            (-1, points[-1], points[-2], 'P' in path[index + 1][5])):
                        if not marked:
                            continue
                        # Span short collinear segments, but never jump a bend or a layer change.
                        end = neighbour
                        for offset in range(index + 2, len(path)) if direction == 1 else range(index - 1, -1, -1):
                            previous = path[offset - 1] if direction == 1 else path[offset]
                            candidate = path[offset][0]
                            if previous[1] != layer or previous[2] != width or previous[3] & 4096:
                                break
                            dx, dy = neighbour[0] - endpoint[0], neighbour[1] - endpoint[1]
                            cx, cy = candidate[0] - endpoint[0], candidate[1] - endpoint[1]
                            if abs(dx * cy - dy * cx) > 1e-8 or dx * cx + dy * cy <= 0:
                                break
                            end = candidate
                        self._teardrops.append((layer, self._current_net, endpoint, end, width))
                index += 1
            path.clear()

        for row in rows.rows:
            if row.startswith('*SIGNAL*'):
                finish()
                signal = True
                self._current_net = row.split()[1]
                pair = False
                continue
            fields = row.split()
            if self._pin_pair(fields):
                finish()
                pair = True
                continue
            if not pair:
                raise PADSParseError('PADS route has no pin pair')
            if not signal or len(fields) < 5:
                raise PADSParseError('Invalid PADS route record')
            point = tuple(self._number(value) * self.unit for value in fields[:2])
            layer, width, flags = int(fields[2]), self._number(fields[3]) * self.unit, int(fields[4])
            extra = fields[5:]
            drops = set()
            if 'TEARDROP' in extra:
                start = extra.index('TEARDROP')
                data, extra = extra[start + 1:], extra[:start]
                while data:
                    if len(data) < 3 or data[0] not in ('P', 'N') or data[0] in drops:
                        raise PADSParseError('Invalid PADS teardrop parameters')
                    drops.add(data[0])
                    if any(not 0 <= self._number(value) <= 100 for value in data[1:3]):
                        raise PADSParseError('Invalid PADS teardrop ratios')
                    data = data[3:]
                    if data and data[0] not in ('P', 'N'):
                        # Non-default styles need their own reconstruction policy, not silent conversion.
                        raise PADSParseError('Unsupported PADS teardrop style')
            if 'THERMAL' in extra:
                self._thermal_points.add((self._current_net, point))
                extra.remove('THERMAL')
            if len(extra) > 1 or (extra and extra[0] == 'THERMAL') or width < 0 or flags < 0:
                raise PADSParseError('Unsupported PADS route parameters')
            if bool(flags & 4096) != bool(extra and extra[0] in ('CW', 'CCW')):
                raise PADSParseError('PADS route arc flag/direction mismatch')
            if extra and extra[0] not in ('THERMAL', 'CW', 'CCW'):
                name = extra[0]
                if name not in vias:
                    raise PADSParseError('Missing PADS route via definition: %s' % name)
                diameter, stack, span = vias[name]
                key = (point, name)
                if key not in self._via_positions:
                    self._emit_stack(stack, (0, 0), self.unit, point, 0, False, span, diameter)
                    self._via_positions.add(key)
            path.append((point, layer, width, flags, extra, drops))
        finish()
        self._current_net = None

    @staticmethod
    def _pin_pair(fields):
        return (len(fields) >= 2 and '.' in fields[0] and '.' in fields[1] and
                (len(fields) == 2 or fields[2] in ('R', 'P', 'U') or not re.match(r'^[-+\d]', fields[0])))

    def _foreign_copper(self, layer, net):
        return unary_union([geo for target, owner, geo in self._copper_items
                            if target == layer and (owner is None or owner != net)])

    def _drill_regions(self):
        holes = {layer: [] for layer in self.copper_layers}
        mechanical = {layer: [] for layer in self.copper_layers}
        for group, tools in self.drill_groups.items():
            first, last = (int(value) for value in group.rsplit('_', 1)[1].split('-'))
            pieces = []
            for tool in tools.values():
                radius = tool['tooldia'] / 2 + (self._clearance if group.startswith('NPTH') else 0)
                pieces.extend(point.buffer(radius, quad_segs=self._segs(radius)) for point in tool['drills'])
                pieces.extend(LineString([start, end]).buffer(radius, quad_segs=self._segs(radius))
                              for start, end in tool['slots'])
            for layer in range(first, last + 1):
                holes[layer].extend(pieces)
                if group.startswith('NPTH'):
                    mechanical[layer].extend(pieces)
        return ({layer: unary_union(pieces) for layer, pieces in holes.items()},
                {layer: unary_union(pieces) for layer, pieces in mechanical.items()})

    def _reconstruct_teardrops(self):
        for layer, net, point, end, width in self._teardrops:
            length = math.dist(point, end)
            if length <= width / 2:
                continue
            pads = [geo for target, owner, geo in self._pad_items
                    if target == layer and owner == net and geo.distance(Point(point)) < 0.0005]
            if not pads:
                # At a track junction the wider local stroke is the landing, not a fictitious pad.
                widths = [size for target, owner, follow, size in self._track_items
                          if target == layer and owner == net and follow.distance(Point(point)) < 0.0005]
                pads = [Point(point).buffer(max(widths) / 2)] if widths else []
            if not pads:
                raise PADSParseError('PADS teardrop has no same-net landing')
            angle = math.degrees(math.atan2(end[1] - point[1], end[0] - point[0]))
            landing = rotate(translate(unary_union(pads), xoff=-point[0], yoff=-point[1]),
                             -angle, origin=(0, 0))
            # Measure only a local section; a long connected trace isn't the pad diameter.
            section = landing.intersection(LineString([(0, -10000), (0, 10000)]))
            if section.is_empty:
                continue
            low, high = section.bounds[1], section.bounds[3]
            half = min(-low, high)
            if half <= width / 2 + 1e-6:
                continue
            reach = min(length, 2 * half)
            taper = Polygon([(0, -half), (0, half), (reach, width / 2), (reach, -width / 2)])
            taper = translate(rotate(taper, angle, origin=(0, 0)), *point)
            foreign = self._foreign_copper(layer, net)
            if not foreign.is_empty:
                taper = taper.difference(foreign.buffer(self._clearance, quad_segs=self._segs(self._clearance)))
            taper = taper.difference(self._mechanical_voids[layer])
            if not self.board_outline.is_empty:
                taper = taper.intersection(self.board_outline.buffer(-self._edge_clearance))
            own = unary_union([geo for target, owner, geo in self._copper_items if target == layer and owner == net])
            taper = unary_union([polygon for polygon in self._polygons_of(taper)
                                 if polygon.intersection(own).area > 1e-9])
            self._current_net = net
            self._add(layer, taper)
        self._current_net = None
        if self._teardrops:
            self._warn('PADS Default teardrops are reconstructed as approximate local straight tapers, clipped to '
                       'foreign-net clearance; 90/90 is not interpreted as percentages. '
                       'Bends and short landings can limit the taper; not original PADS artwork.')

    def _pour_piece(self, rows, position):
        fields = rows.fields()
        kind, corners, arcs = fields[0], self._count(fields[1]), self._count(fields[2])
        width, layer = self._number(fields[3]) * self.unit, int(fields[4])
        if width <= 0 or layer not in self.copper_layers:
            raise PADSParseError('Invalid PADS pour width/layer')
        points, pending = [], None
        for _ in range(corners + arcs):
            values = rows.fields()
            point = tuple(self._number(value) * self.unit for value in values[:2])
            if len(values) == 4:
                if pending is not None or not points:
                    raise PADSParseError('Invalid PADS pour arc')
                pending = (point, self._number(values[3]))
            elif len(values) == 2:
                if pending is not None:
                    points.extend(self._arc_points(points[-1], point, pending[0], pending[1] < 0)[1:])
                    pending = None
                else:
                    points.append(point)
            else:
                raise PADSParseError('Invalid PADS pour vertex')
        if pending is not None:
            raise PADSParseError('Unfinished PADS pour arc')
        if kind in ('CIRCLE', 'CIRCUT') and len(points) == 2 and arcs == 0:
            radius = math.dist(*points) / 2
            geo = Point((points[0][0] + points[1][0]) / 2,
                        (points[0][1] + points[1][1]) / 2).buffer(radius, quad_segs=self._segs(radius))
        elif kind in ('POLY', 'POCUT', 'CUTOUT') and len(points) >= 4 and points[0] == points[-1]:
            geo = Polygon(points)
        else:
            raise PADSParseError('Unsupported or unclosed PADS pour piece: %s' % kind)
        if not geo.is_valid or geo.is_empty:
            raise PADSParseError('Invalid PADS pour boundary')
        return layer, translate(geo, *position), width, kind in ('CIRCUT', 'POCUT', 'CUTOUT')

    def _pours(self, rows):
        definitions, names = [], set()
        while rows:
            fields = rows.fields()
            if len(fields) < 8 or fields[1] != 'POUROUT' or fields[0] in names:
                raise PADSParseError('Unsupported or malformed PADS pour record')
            names.add(fields[0])
            position = tuple(self._number(value) * self.unit for value in fields[2:4])
            if int(fields[5]) != 0:
                raise PADSParseError('Unsupported PADS pour flags')
            priority = int(fields[10]) if len(fields) > 10 else 0
            for _ in range(self._count(fields[4])):
                layer, outline, width, cutout = self._pour_piece(rows, position)
                if cutout:
                    self._pour_keepouts.setdefault(layer, []).append(outline)
                else:
                    definitions.append((priority, layer, fields[7], outline, width))
        if not definitions:
            return
        if self.board_outline.is_empty or not self.board_outline.is_valid:
            raise PADSParseError('PADS pour reconstruction needs a valid board outline')
        # Keep existing pads/tracks intact: these holes and keepouts constrain only new fill.
        board = self.board_outline.buffer(-self._edge_clearance)
        for priority, layer, net, outline, width in sorted(definitions, key=lambda item: item[0]):
            foreign = self._foreign_copper(layer, net)
            excluded = foreign.buffer(self._clearance, quad_segs=self._segs(self._clearance))
            excluded = excluded.union(unary_union(self._pour_keepouts.get(layer, []))).union(self._hole_voids[layer])
            allowed = outline.intersection(board).difference(excluded)
            fill = allowed
            anchors = [geo for target, owner, geo in self._copper_items if target == layer and owner == net]
            if not anchors:
                raise PADSParseError('PADS pour has no same-net copper anchor: %s' % net)
            for target, owner, pad in self._pad_items:
                if target != layer or owner != net or not pad.intersects(outline):
                    continue
                if not any(owner == thermal_net and pad.distance(Point(point)) < 0.0005
                           for thermal_net, point in self._thermal_points):
                    continue
                center = pad.centroid
                gap = self._clearance
                relief = pad.buffer(gap, quad_segs=self._segs(gap))
                radius = max(relief.bounds[2] - relief.bounds[0], relief.bounds[3] - relief.bounds[1])
                spokes = unary_union([LineString([(center.x - radius, center.y), (center.x + radius, center.y)]),
                                      LineString([(center.x, center.y - radius), (center.x, center.y + radius)])])
                spokes = spokes.buffer(self._thermal_width / 2, cap_style='flat').intersection(relief)
                fill = fill.difference(relief).union(spokes.intersection(allowed))
            # Morphological opening removes slivers below the saved pour width.
            radius = width / 2
            fill = fill.buffer(-radius, quad_segs=self._segs(radius)).buffer(radius, quad_segs=self._segs(radius))
            fill = fill.intersection(allowed)
            anchor = unary_union(anchors)
            fill = unary_union([polygon for polygon in self._polygons_of(fill) if polygon.distance(anchor) < 1e-6])
            if fill.is_empty:
                raise PADSParseError('PADS pour reconstruction produced no connected copper: %s' % net)
            self._current_net = net
            self._add(layer, fill)
            self.pours.append({'layer': layer, 'net': net, 'geometry': fill, 'priority': priority})
        self._current_net = None
        self._warn('PADS pours are reconstructed: conservative clearance envelope, board clipping, '
                   'four orthogonal thermal spokes and unconnected-island removal. Vendor flood flags '
                   'are replaced by this policy, not original PADS artwork; '
                   'inspect clearances and connectivity before manufacture.')
