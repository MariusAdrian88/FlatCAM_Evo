# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# IPC-2581 board reader                                     #
# Author: Marius Stanciu (c)                                #
# Format facts also from TRACE Boardviewer (MIT)            #
# https://github.com/trace-boardviewer/trace-boardviewer    #
# IPC-2581 consortium schema and public sources (see below) #
# MIT Licence                                               #
# ##########################################################
"""
IPC-2581 (revisions B and C) board import.

The reader makes the result of the other board importers (ParsePADS, ParseODB): one Gerber-like layer for each
graphic layer of the file, Excellon-like drill groups (PTH / NPTH) from the Hole and SlotCavity records and the board
profile with its cutouts. Units are mm, Y is up (IPC-2581 is Y up, like FlatCAM).

Sources of the format facts:
- IPC-2581 consortium, http://www.ipc2581.com (revision summaries) and the XML schema IPC-2581C.xsd that the KiCad
  project ships as a test file, https://gitlab.com/kicad/code/kicad (qa/data/pcbnew/ipc2581/IPC-2581C.xsd): element and
  attribute names, units (MILLIMETER, MICRON, INCH), layerFunction, platingStatus, the standard primitives, Xform,
  Features / Pad / Set / Hole / SlotCavity, Contour (Polygon + Cutout), PolyStepCurve, LineDesc / FillDesc.
- TRACE Boardviewer (MIT) https://github.com/trace-boardviewer/trace-boardviewer (src/lib/formats/ipc2581.ts): a
  transform is mirror (x -> -x), then scale, then counter-clockwise rotation, then the offset, then the Location.
- KiCad's exporter (pcbnew/pcb_io/ipc2581, GPL, facts only): what KiCad writes (copper pours as Contour in a
  UserSpecial, tracks as Line / Arc in a UserSpecial, pads as Pad with a standard primitive, holes as Hole and
  SlotCavity in the DRILL layers, the profile in Step/Profile).

What is read: Ecad/CadHeader units, CadData/Layer (layerFunction, polarity, side), Step/Profile, the LayerFeature
Sets (Pad, Fiducial, Features with Location / Xform, Hole, SlotCavity, set polarity) and the dictionaries of Content
(standard and user primitives, line and fill descriptions). Packages, Components, BOM, AVL, Stackup, netlists and
padstack definitions are not needed for the artwork (every pad of a layer is placed in its LayerFeature); only a Pad
without a shape takes the shape of its padstack.

Copper (and the outline layer) with a feature that cannot be drawn exactly stops the import with an IPC2581ParseError
that names the feature. The same feature on another layer (mask, paste, silkscreen, documents) is skipped and counted
in the warnings.
"""

import gzip
import io
import math
import re
import zipfile

from lxml import etree
from shapely import affinity
from shapely.errors import ShapelyError
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.validation import make_valid

from camlib import circle_quad_segs, gerber_circle_steps
from appParsers.board_primitives import BoardPrimitives

PROFILE_WIDTH_MM = 0.01
MAX_BYTES = 1 << 29                 # file, or the XML of an archive
MAX_RATIO = 200                     # decompressed / compressed
MAX_CIRCLE_STEPS = 100_000        # segments of a full circle, whatever the preference or the radius
MAX_POINTS = 20_000_000             # points of all polygons, arcs and placed copies of the file
UNITS = {'MILLIMETER': 1.0, 'MICRON': 0.001, 'INCH': 25.4}

COPPER_FUNCTIONS = ('CONDUCTOR', 'SIGNAL', 'PLANE', 'MIXED', 'CONDFILM', 'CONDFOIL')
KINDS = {
    'SOLDERMASK': 'mask', 'SOLDERPASTE': 'paste', 'PASTEMASK': 'paste', 'SILKSCREEN': 'silk', 'LEGEND': 'silk',
    'BOARD_OUTLINE': 'outline', 'DRILL': 'drill',
    'DIELBASE': None, 'DIELCORE': None, 'DIELPREG': None, 'DIELADHV': None, 'DIELBONDPLY': None,
    'DIELCOVERLAY': None, 'STACKUP_COMPOSITE': None, 'COMPONENT': None, 'COMPONENT_BOTTOM': None,
    'COMPONENT_TOP': None, 'COMPONENT_EMBEDDED': None, 'COMPONENT_FORMED': None,
}
for _function in COPPER_FUNCTIONS:
    KINDS[_function] = 'copper'
STRICT_KINDS = ('copper', 'outline')
DEFAULT_ON = ('copper', 'mask', 'paste', 'silk', 'outline')

STANDARD_SHAPES = ('Butterfly', 'Circle', 'Contour', 'Diamond', 'Donut', 'Ellipse', 'Hexagon', 'Moire', 'Octagon',
                   'Oval', 'RectCenter', 'RectCham', 'RectCorner', 'RectRound', 'Thermal', 'Triangle')
STROKES = ('Line', 'Arc', 'Polyline', 'Outline')
FEATURE_TAGS = STANDARD_SHAPES + STROKES + ('StandardPrimitiveRef', 'UserPrimitiveRef', 'UserSpecial', 'Text',
                                            'Polygon')
SET_TAGS = ('NonstandardAttribute', 'Pad', 'Fiducial', 'Hole', 'SlotCavity', 'SpecRef', 'Features', 'ColorGroup',
            'Color', 'ColorRef', 'ColorTerm', 'LineDesc', 'LineDescRef', 'FillDesc', 'FillDescRef', 'NetShort')


class IPC2581ParseError(ValueError):
    """A bad IPC-2581 file, or copper that cannot be imported exactly. The parser gives no partial output."""


class _Unsupported(Exception):
    """A feature that cannot be drawn; the layer decides: copper stops the import, the other layers skip it."""


def _local(node):
    return node.tag.rpartition('}')[2]


def _children(node):
    return node.iterchildren(etree.Element)


def _text_flag(value):
    return str(value).strip().lower() in ('true', '1')


def _cos_sin(degrees):
    """Cosine and sine of an angle in degrees, exact at the multiples of 90."""
    turns = degrees / 90.0
    if turns == int(turns):
        quarter = int(turns) % 4
        return (1.0, 0.0, -1.0, 0.0)[quarter], (0.0, 1.0, 0.0, -1.0)[quarter]
    radians = math.radians(degrees)
    return math.cos(radians), math.sin(radians)


def _rect(w, h, radius=0.0, chamfer=0.0, round_corners=(), chamfer_corners=(), quad=4):
    """A rectangle centred at the origin; the named corners ('ll', 'lr', 'ur', 'ul') are rounded or chamfered."""
    if w <= 0 or h <= 0:
        return Polygon()
    radius = max(0.0, min(radius, w / 2, h / 2))
    chamfer = max(0.0, min(chamfer, w, h))
    hx, hy = w / 2, h / 2
    start_angle = {'ll': 180, 'lr': 270, 'ur': 0, 'ul': 90}
    points = []
    for name, (cx, cy), (sx, sy) in (('ll', (-hx, -hy), (1, 1)), ('lr', (hx, -hy), (-1, 1)),
                                     ('ur', (hx, hy), (-1, -1)), ('ul', (-hx, hy), (1, -1))):
        along_y_first = name in ('ll', 'ur')        # the edge that arrives at the corner is vertical
        if name in chamfer_corners and chamfer > 0:
            first, second = (cx, cy + sy * chamfer), (cx + sx * chamfer, cy)
            points.extend((first, second) if along_y_first else (second, first))
        elif name in round_corners and radius > 0:
            centre = (cx + sx * radius, cy + sy * radius)
            steps = max(2, quad)
            for index in range(steps + 1):
                angle = math.radians(start_angle[name] + 90.0 * index / steps)
                points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
        else:
            points.append((cx, cy))
    return Polygon(points)


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if hasattr(geometry, 'geoms'):
        result = []
        for part in geometry.geoms:
            result.extend(_polygons(part))
        return result
    return []


class IPC2581Parser:
    """
    parse_file(path, step=None) -> self, with the attributes of the other board importers: layers, drill_groups,
    default_layers, default_drill_groups, warnings, board_profile, board_outline, name, source_kind, plus steps, step
    and revision.
    """

    source_kind = 'ipc2581'

    def __init__(self, app=None, circle_steps=None):
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        self._reset()

    def _reset(self):
        self._primitives = BoardPrimitives(self._options)
        self._primitive_buffer = None
        self._last_primitives = []
        self._primitive_cache = {}
        self.source_kind = 'ipc2581'
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []
        self.default_drill_groups = []
        self.warnings = []
        self.board_profile = Polygon()
        self.board_outline = self.board_profile
        self.steps = []
        self.step = None
        self.name = ''
        self.revision = ''
        self.units = 'MM'
        self.software = ''
        self._counts = {}
        self._points = 0
        self._lines = {}
        self._fills = {}
        self._std = {}
        self._user = {}
        self._cache = {}
        self._resolving = set()
        self._factor = 1.0
        self._set_line = None
        self._set_fill = None
        self._follow_buffer = None
        self._padstacks = {}
        self._notes = set()

    # ------------------------------------------------------------------------------------------------ entry
    def parse_file(self, filename, step=None):
        self._reset()
        try:
            with open(str(filename), 'rb') as handle:
                head = handle.read(4)
                handle.seek(0)
                if head[:2] == b'PK':
                    data = self._unzip(handle)
                elif head[:2] == b'\x1f\x8b':
                    data = self._ungzip(handle)
                else:
                    data = handle.read(MAX_BYTES + 1)
        except OSError as error:
            raise IPC2581ParseError('Cannot read the IPC-2581 file: %s' % error) from error
        return self.parse_bytes(data, step=step)

    @staticmethod
    def _unzip(handle):
        try:
            archive = zipfile.ZipFile(handle)
        except zipfile.BadZipFile as error:
            raise IPC2581ParseError('Bad ZIP archive') from error
        members = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith('.xml')]
        if len(members) != 1:
            raise IPC2581ParseError('The archive must hold exactly 1 XML file of the IPC-2581 board')
        info = members[0]
        if info.file_size > MAX_BYTES or info.file_size > MAX_RATIO * max(1, info.compress_size):
            raise IPC2581ParseError('The XML file of the archive is too large')
        with archive.open(info) as member:
            data = member.read(MAX_BYTES + 1)
        return data

    @staticmethod
    def _ungzip(handle):
        raw = handle.read(MAX_BYTES + 1)
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                data = stream.read(MAX_BYTES + 1)
        except (OSError, EOFError) as error:
            raise IPC2581ParseError('Bad gzip file') from error
        if len(data) > MAX_RATIO * max(1, len(raw)):
            raise IPC2581ParseError('The compressed IPC-2581 file expands too much')
        return data

    def parse_bytes(self, data, step=None):
        self._reset()
        if len(data) > MAX_BYTES:
            raise IPC2581ParseError('The IPC-2581 file is too large')
        head = data[:data.find(b'<IPC-2581')] if b'<IPC-2581' in data[:1 << 20] else data[:1 << 16]
        if b'<!ENTITY' in head:
            raise IPC2581ParseError('The file declares XML entities; they are never expanded')
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
                                 huge_tree=False, remove_comments=True, remove_pis=True, collect_ids=False)
        try:
            root = etree.fromstring(data, parser)
        except etree.XMLSyntaxError as error:
            raise IPC2581ParseError('Malformed XML: %s' % error) from error
        dtd = root.getroottree().docinfo.internalDTD
        if dtd is not None and any(True for _entity in dtd.entities()):
            raise IPC2581ParseError('The file declares XML entities; they are never expanded')
        try:
            self._parse_root(root, step)
        except IPC2581ParseError:
            raise
        except (KeyError, IndexError, TypeError, AttributeError, ValueError, OverflowError, ZeroDivisionError,
                RecursionError, MemoryError, ShapelyError, StopIteration) as error:
            raise IPC2581ParseError('Invalid IPC-2581 data: %s: %s' % (type(error).__name__, error)) from error
        self.warnings = list(dict.fromkeys(self.warnings))
        return self

    # ------------------------------------------------------------------------------------------------ helpers
    def _count(self, fmt):
        self._counts[fmt] = self._counts.get(fmt, 0) + 1

    def _flush_counts(self):
        for fmt, total in self._counts.items():
            self.warnings.append(fmt % total)
        self._counts = {}

    def _note(self, message):
        if message not in self._notes:
            self._notes.add(message)
            self.warnings.append(message)

    def _spend(self, count):
        self._points += count
        if self._points > MAX_POINTS:
            raise IPC2581ParseError('The file has too many points (limit %d)' % MAX_POINTS)

    def _steps(self, radius):
        if self.circle_steps is not None:
            return self.circle_steps
        return min(MAX_CIRCLE_STEPS, gerber_circle_steps(self._options, radius, 'MM'))

    def _quad(self, radius):
        return circle_quad_segs(self._steps(radius))

    @staticmethod
    def _number(node, name, factor=1.0, default=None):
        value = node.get(name)
        if value is None:
            if default is None:
                raise IPC2581ParseError('<%s> has no %s' % (_local(node), name))
            return default
        try:
            number = float(value)
        except ValueError:
            raise IPC2581ParseError('<%s> %s is not a number: %r' % (_local(node), name, value)) from None
        if not math.isfinite(number):
            raise IPC2581ParseError('<%s> %s is not finite' % (_local(node), name))
        return number * factor

    def _unit_factor(self, node, default):
        value = node.get('units')
        if value is None:
            return default
        factor = UNITS.get(value.strip().upper())
        if factor is None:
            raise IPC2581ParseError('Unsupported units %r' % value)
        return factor

    # ------------------------------------------------------------------------------------------------ document
    def _parse_root(self, root, step):
        if _local(root) != 'IPC-2581':
            raise IPC2581ParseError('Not an IPC-2581 document')
        self.revision = (root.get('revision') or '').strip().upper()
        if not re.match(r'^[BC]\d*$', self.revision):
            raise IPC2581ParseError('Unsupported IPC-2581 revision %r (revisions B and C are read)' %
                                    (root.get('revision') or ''))
        top = {}
        for child in _children(root):
            top.setdefault(_local(child), child)
        ecad = top.get('Ecad')
        if ecad is None:
            raise IPC2581ParseError('The file has no Ecad section (a BOM-only file has no board)')
        header = next((c for c in _children(ecad) if _local(c) == 'CadHeader'), None)
        data = next((c for c in _children(ecad) if _local(c) == 'CadData'), None)
        if header is None or data is None:
            raise IPC2581ParseError('The Ecad section has no CadHeader and CadData')
        units = (header.get('units') or '').strip().upper()
        if units not in UNITS:
            raise IPC2581ParseError('Unsupported CadHeader units %r' % header.get('units'))
        self._factor = UNITS[units]
        record = top.get('HistoryRecord')
        if record is not None:
            self.software = record.get('software') or ''
        if top.get('Content') is not None:
            self._dictionaries(top['Content'])
        layers, steps = self._layers(data)
        self._read_steps(steps, step)
        self._build(layers)

    def _dictionaries(self, content):
        for dictionary in _children(content):
            tag = _local(dictionary)
            if tag not in ('DictionaryLineDesc', 'DictionaryFillDesc', 'DictionaryStandard', 'DictionaryUser'):
                continue
            factor = self._unit_factor(dictionary, self._factor)
            for entry in _children(dictionary):
                key = entry.get('id')
                body = next(_children(entry), None)
                if key is None or body is None:
                    continue
                if tag == 'DictionaryLineDesc':
                    self._lines[key] = self._line_desc(body, factor)
                elif tag == 'DictionaryFillDesc':
                    self._fills[key] = (body.get('fillProperty') or 'FILL').upper()
                elif tag == 'DictionaryStandard':
                    self._std[key] = (body, factor)
                else:
                    self._user[key] = (body, factor)

    def _layers(self, data):
        layers = {}
        steps = []
        for child in _children(data):
            tag = _local(child)
            if tag == 'Layer':
                name = child.get('name')
                if not name:
                    raise IPC2581ParseError('A Layer has no name')
                if name in layers:
                    raise IPC2581ParseError('Duplicate layer name %r' % name)
                function = (child.get('layerFunction') or '').strip().upper()
                layers[name] = {'name': name, 'function': function,
                                'kind': KINDS.get(function, 'other'),
                                'polarity': (child.get('polarity') or 'POSITIVE').strip().upper(),
                                'side': (child.get('side') or '').strip().upper()}
            elif tag == 'Step':
                steps.append(child)
        if not steps:
            raise IPC2581ParseError('The file has no Step')
        return layers, steps

    def _read_steps(self, steps, wanted):
        info = []
        for node in steps:
            repeats = any(_local(c) == 'StepRepeat' for c in _children(node))
            info.append((node.get('name') or '', (node.get('type') or '').upper(), repeats, node))
        self.steps = [name for name, _kind, _rep, _node in info]
        if wanted is not None:
            chosen = next((item for item in info if item[0].lower() == str(wanted).lower()), None)
            if chosen is None:
                raise IPC2581ParseError('No step %r in the file' % wanted)
        else:
            plain = [item for item in info if not item[2]]
            boards = [item for item in plain if item[1] in ('BOARD', '')]
            chosen = (boards or plain or info)[0]
            if len(info) > 1:
                self.warnings.append('The file has %d steps (%s); the step %r is imported.' %
                                     (len(info), ', '.join(self.steps), chosen[0]))
        if chosen[2]:
            raise IPC2581ParseError('The step %r repeats other steps (a panel); import a board step' % chosen[0])
        self.step = chosen[0]
        self.name = chosen[0] or 'ipc2581'
        self._step_node = chosen[3]

    # ------------------------------------------------------------------------------------------------ descriptions
    def _line_desc(self, node, factor):
        return (self._number(node, 'lineWidth', factor), (node.get('lineEnd') or 'ROUND').strip().upper())

    def _descs(self, node, factor):
        line, fill = None, None
        for child in _children(node):
            tag = _local(child)
            if tag == 'LineDesc':
                line = self._line_desc(child, factor)
            elif tag == 'LineDescRef':
                if child.get('id') not in self._lines:
                    raise IPC2581ParseError('Unknown LineDescRef %r' % child.get('id'))
                line = self._lines[child.get('id')]
            elif tag == 'FillDesc':
                fill = (child.get('fillProperty') or 'FILL').strip().upper()
            elif tag == 'FillDescRef':
                if child.get('id') not in self._fills:
                    raise IPC2581ParseError('Unknown FillDescRef %r' % child.get('id'))
                fill = self._fills[child.get('id')]
        return line or self._set_line, fill or self._set_fill

    # ------------------------------------------------------------------------------------------------ geometry
    def _circle(self, diameter):
        if diameter <= 0:
            return Polygon()
        return Point(0, 0).buffer(diameter / 2, quad_segs=self._quad(diameter / 2))

    def _oval(self, w, h):
        if w <= 0 or h <= 0:
            return Polygon()
        if abs(w - h) < 1e-12:
            return self._circle(w)
        if w > h:
            return LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=self._quad(h / 2))
        return LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=self._quad(w / 2))

    def _corners(self, node):
        names = {'lowerLeft': 'll', 'lowerRight': 'lr', 'upperRight': 'ur', 'upperLeft': 'ul'}
        if not any(key in node.attrib for key in names):
            return tuple(names.values())        # no corner given: all corners
        return tuple(short for key, short in names.items() if _text_flag(node.get(key, 'false')))

    def _arc_points(self, start, end, centre, clockwise):
        """The points after 'start' up to 'end' of an arc around 'centre'; start == end is a full circle."""
        r0 = math.hypot(start[0] - centre[0], start[1] - centre[1])
        r1 = math.hypot(end[0] - centre[0], end[1] - centre[1])
        if r0 <= 0 or r1 <= 0:
            return [end]
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        sweep = math.atan2(end[1] - centre[1], end[0] - centre[0]) - a0
        if math.hypot(end[0] - start[0], end[1] - start[1]) <= 1e-9 * max(1.0, r0):
            sweep = -2 * math.pi if clockwise else 2 * math.pi
        elif clockwise:
            if sweep >= 0:
                sweep -= 2 * math.pi
        elif sweep <= 0:
            sweep += 2 * math.pi
        steps = max(2, math.ceil(abs(sweep) / (2 * math.pi) * self._steps(max(r0, r1))))
        self._spend(steps)
        points = []
        for index in range(1, steps):
            fraction = index / steps
            angle = a0 + sweep * fraction
            radius = r0 + (r1 - r0) * fraction
            points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
        points.append(end)
        return points

    def _poly_points(self, node, factor):
        """PolyBegin and the PolyStepSegment / PolyStepCurve steps of a Polygon or Polyline as points (mm)."""
        points = []
        current = None
        for step in _children(node):
            tag = _local(step)
            if tag == 'PolyBegin':
                if current is not None:
                    raise IPC2581ParseError('A polygon has a second PolyBegin')
                current = (self._number(step, 'x', factor), self._number(step, 'y', factor))
                points.append(current)
            elif tag == 'PolyStepSegment':
                if current is None:
                    raise IPC2581ParseError('A polygon step comes before its PolyBegin')
                current = (self._number(step, 'x', factor), self._number(step, 'y', factor))
                points.append(current)
            elif tag == 'PolyStepCurve':
                if current is None:
                    raise IPC2581ParseError('A polygon step comes before its PolyBegin')
                end = (self._number(step, 'x', factor), self._number(step, 'y', factor))
                centre = (self._number(step, 'centerX', factor), self._number(step, 'centerY', factor))
                points.extend(self._arc_points(current, end, centre, _text_flag(step.get('clockwise', 'false'))))
                current = end
        self._spend(len(points))
        return points

    def _fix(self, geometry):
        if geometry.is_empty or geometry.is_valid:
            return geometry
        self._count('%d invalid polygon(s) of the file were repaired.')
        return unary_union(_polygons(make_valid(geometry)))

    def _polygon(self, node, factor):
        points = self._poly_points(node, factor)
        if len(points) < 3:
            return Polygon()
        polygon = self._fix(Polygon(points))
        transform = next((c for c in _children(node) if _local(c) == 'Xform'), None)
        if transform is not None:
            polygon = self._apply(polygon, self._xform(transform, factor), (0.0, 0.0))
        return polygon

    def _contour(self, node, factor):
        shell = next((c for c in _children(node) if _local(c) == 'Polygon'), None)
        if shell is None:
            raise IPC2581ParseError('A Contour has no Polygon')
        polygon = self._polygon(shell, factor)
        cutouts = [self._polygon(c, factor) for c in _children(node) if _local(c) == 'Cutout']
        cutouts = [c for c in cutouts if not c.is_empty]
        if cutouts and not polygon.is_empty:
            polygon = polygon.difference(unary_union(cutouts))
        return polygon, shell

    @staticmethod
    def _spokes(count, angle, gap, reach):
        bars = []
        half = gap / 2
        for index in range(count):
            a = math.radians(angle + index * 360.0 / count)
            c, s = math.cos(a), math.sin(a)
            bars.append(Polygon([(-s * half, c * half), (reach * c - s * half, reach * s + c * half),
                                 (reach * c + s * half, reach * s - c * half), (s * half, -c * half)]))
        return unary_union(bars)

    def _base_shape(self, node, factor):
        """(filled geometry at the origin, the element that carries the line and fill description)."""
        tag = _local(node)
        number = lambda name: self._number(node, name, factor)          # noqa: E731
        if tag == 'Circle':
            return self._circle(number('diameter')), node
        if tag == 'RectCenter':
            w, h = number('width'), number('height')
            return (box(-w / 2, -h / 2, w / 2, h / 2) if w > 0 and h > 0 else Polygon()), node
        if tag in ('RectRound', 'RectCham'):
            w, h = number('width'), number('height')
            corners = self._corners(node)
            if tag == 'RectRound':
                radius = number('radius')
                return _rect(w, h, radius=radius, round_corners=corners, quad=self._quad(radius)), node
            return _rect(w, h, chamfer=number('chamfer'), chamfer_corners=corners), node
        if tag == 'RectCorner':
            x0, y0 = number('lowerLeftX'), number('lowerLeftY')
            x1, y1 = number('upperRightX'), number('upperRightY')
            return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), node
        if tag == 'Oval':
            return self._oval(number('width'), number('height')), node
        if tag == 'Diamond':
            w, h = number('width'), number('height')
            return (Polygon([(-w / 2, 0), (0, h / 2), (w / 2, 0), (0, -h / 2)]) if w > 0 and h > 0
                    else Polygon()), node
        if tag == 'Ellipse':
            w, h = number('width'), number('height')
            if w <= 0 or h <= 0:
                return Polygon(), node
            return affinity.scale(Point(0, 0).buffer(0.5, quad_segs=self._quad(max(w, h) / 2)), w, h), node
        if tag in ('Donut', 'Thermal'):
            shape = (node.get('shape') or '').strip().upper()
            if shape not in ('ROUND', 'SQUARE'):
                raise _Unsupported('%s with shape %s' % (tag, shape or '?'))
            outer, inner = number('outerDiameter'), number('innerDiameter')
            if shape == 'ROUND':
                ring = self._circle(outer).difference(self._circle(inner))
            else:
                ring = box(-outer / 2, -outer / 2, outer / 2, outer / 2).difference(
                    box(-inner / 2, -inner / 2, inner / 2, inner / 2))
            if tag == 'Thermal' and not ring.is_empty:
                try:
                    count = int(node.get('spokeCount', '4'))
                except ValueError:
                    raise IPC2581ParseError('Thermal spokeCount is not a number') from None
                gap = number('spokeWidth')
                angle = self._number(node, 'spokeStartAngle', 1.0)
                if count > 0:
                    ring = ring.difference(self._spokes(count, angle, gap, outer * 2))
                self._note('Thermal primitives are drawn as a ring with open gaps of spokeWidth at the spoke '
                           'angles; check their pads.')
            return ring, node
        if tag == 'Contour':
            polygon, shell = self._contour(node, factor)
            return polygon, shell
        raise _Unsupported(tag)

    def _outline(self, geometry, line):
        width, end = line
        rings = []
        for polygon in _polygons(geometry):
            rings.append(polygon.exterior)
            rings.extend(polygon.interiors)
        if not rings or width <= 0:
            return Polygon()
        return unary_union([LineString(ring.coords).buffer(width / 2, quad_segs=self._quad(width / 2),
                                                           cap_style=1, join_style=1 if end == 'ROUND' else 2)
                            for ring in rings])

    def _decorate(self, geometry, line, fill):
        """(geometry, negative) of a closed shape with its line and fill description."""
        if geometry.is_empty:
            return geometry, False
        fill = fill or 'FILL'
        if fill == 'HOLLOW':
            return (self._outline(geometry, line) if line else Polygon()), False
        if fill in ('HATCH', 'MESH'):
            raise _Unsupported('%s fill' % fill)
        if line and line[0] > 0:
            geometry = unary_union([geometry, self._outline(geometry, line)])
        return geometry, fill == 'VOID'

    def _stroke(self, points, line):
        """A line of 'points' (mm) with the width and the end of 'line'."""
        if not line or line[0] <= 0:
            return Polygon()
        width, end = line
        radius = width / 2
        clean = [points[0]]
        for point in points[1:]:
            if point != clean[-1]:
                clean.append(point)
        if self._follow_buffer is not None and len(clean) > 1:
            self._follow_buffer.append(LineString(clean))
        if len(clean) == 1:
            if end == 'ROUND':
                solid = Point(clean[0]).buffer(radius, quad_segs=self._quad(radius))
                if self._primitive_buffer is not None:
                    self._primitive_buffer.append(('stroke', solid, Point(clean[0]), width, width, 0.0))
                return solid
            if end == 'SQUARE':
                return box(clean[0][0] - radius, clean[0][1] - radius, clean[0][0] + radius, clean[0][1] + radius)
            return Polygon()
        cap = {'ROUND': 1, 'SQUARE': 3}.get(end, 2)
        solid = LineString(clean).buffer(radius, quad_segs=self._quad(radius), cap_style=cap,
                                        join_style=1 if end == 'ROUND' else 2)
        if end == 'ROUND' and self._primitive_buffer is not None:
            self._primitive_buffer.append(('stroke', solid, LineString(clean), width, width, 0.0))
        return solid

    def _stroke_node(self, node, factor):
        tag = _local(node)
        line, _fill = self._descs(node, factor)
        if tag == 'Line':
            points = [(self._number(node, 'startX', factor), self._number(node, 'startY', factor)),
                      (self._number(node, 'endX', factor), self._number(node, 'endY', factor))]
            return self._stroke(points, line)
        if tag == 'Arc':
            start = (self._number(node, 'startX', factor), self._number(node, 'startY', factor))
            end = (self._number(node, 'endX', factor), self._number(node, 'endY', factor))
            centre = (self._number(node, 'centerX', factor), self._number(node, 'centerY', factor))
            points = [start] + self._arc_points(start, end, centre, _text_flag(node.get('clockwise', 'false')))
            return self._stroke(points, line)
        if tag == 'Polyline':
            return self._stroke(self._poly_points(node, factor), line)
        # Outline: a polygon drawn with a line
        shell = next((c for c in _children(node) if _local(c) == 'Polygon'), None)
        if shell is None:
            raise IPC2581ParseError('An Outline has no Polygon')
        points = self._poly_points(shell, factor)
        if len(points) < 2:
            return Polygon()
        return self._stroke(points + [points[0]], line)

    def _feature(self, node, factor, source_pad=False):
        if not self._primitives.enabled:
            return self._feature_geometry(node, factor)
        parent = self._primitive_buffer
        self._primitive_buffer = []
        try:
            result = self._feature_geometry(node, factor)
            records = self._primitive_buffer
            tag = _local(node)
            if (source_pad and tag == 'UserSpecial') or (
                    not records and (source_pad or tag not in STROKES + ('Polygon', 'Contour', 'UserSpecial'))):
                solid = result[0]
                if not solid.is_empty:
                    x0, y0, x1, y1 = solid.bounds
                    records = [('block', solid, Point(0, 0), x1 - x0, y1 - y0, 0.0)]
        finally:
            self._primitive_buffer = parent
        if parent is not None:
            parent.extend(records)
        self._last_primitives = records
        return result

    def _feature_geometry(self, node, factor):
        """(geometry at its own origin in mm, negative) of one feature element."""
        tag = _local(node)
        if tag in ('StandardPrimitiveRef', 'UserPrimitiveRef'):
            key = node.get('id')
            if (tag, key) not in self._cache:
                table, other = (self._std, self._user) if tag == 'StandardPrimitiveRef' else (self._user, self._std)
                entry = table.get(key) or other.get(key)
                if entry is None:
                    raise IPC2581ParseError('Unknown %s %r' % (tag, key))
                if (tag, key) in self._resolving:
                    raise IPC2581ParseError('The %s %r refers to itself' % (tag, key))
                saved = (self._set_line, self._set_fill, self._follow_buffer)
                self._set_line = self._set_fill = self._follow_buffer = None
                self._resolving.add((tag, key))
                try:
                    self._cache[(tag, key)] = self._feature(entry[0], entry[1], source_pad=True)
                    self._primitive_cache[(tag, key)] = self._last_primitives
                finally:
                    self._resolving.discard((tag, key))
                    self._set_line, self._set_fill, self._follow_buffer = saved
            elif self._primitive_buffer is not None:
                self._primitive_buffer.extend(self._primitive_cache.get((tag, key), []))
            return self._cache[(tag, key)]
        if tag == 'UserSpecial':
            items = [self._feature(child, factor) for child in _children(node)
                     if _local(child) in FEATURE_TAGS]
            return self._merge(items), False
        if tag in STROKES:
            return self._stroke_node(node, factor), False
        if tag == 'Polygon':
            polygon = self._polygon(node, factor)
            line, fill = self._descs(node, factor)
            return self._decorate(polygon, line, fill)
        if tag == 'Text':
            raise _Unsupported('Text')
        geometry, carrier = self._base_shape(node, factor)
        line, fill = self._descs(carrier, factor)
        result = self._decorate(geometry, line, fill)
        if self._primitive_buffer is not None and not (line and line[0] > 0) and fill in (None, 'FILL', 'VOID'):
            kind = {'Circle': 'circle', 'RectCenter': 'rect', 'RectCorner': 'rect', 'Oval': 'oval'}.get(tag)
            if kind:
                if tag == 'Circle':
                    w = h = self._number(node, 'diameter', factor)
                    centre = Point(0, 0)
                elif tag == 'RectCorner':
                    x0, y0 = self._number(node, 'lowerLeftX', factor), self._number(node, 'lowerLeftY', factor)
                    x1, y1 = self._number(node, 'upperRightX', factor), self._number(node, 'upperRightY', factor)
                    w, h, centre = abs(x1 - x0), abs(y1 - y0), Point((x0 + x1) / 2, (y0 + y1) / 2)
                else:
                    w, h = self._number(node, 'width', factor), self._number(node, 'height', factor)
                    centre = Point(0, 0)
                self._primitive_buffer.append((kind, geometry, centre, w, h, 0.0))
        return result

    def _place_primitives(self, records, xf, at):
        mirror, scale, rotation, _dx, _dy = xf if xf else (False, 1.0, 0.0, 0.0, 0.0)
        placed = []
        for kind, solid, path, w, h, angle in records:
            angle = rotation + (180 - angle if mirror else angle)
            if kind in ('rect', 'oval') and abs(angle / 90 - round(angle / 90)) > 1e-10:
                kind = 'block'
            placed.append((kind, self._apply(solid, xf, at), self._apply(path, xf, at),
                           w * scale, h * scale, angle))
        return placed

    @staticmethod
    def _merge(items):
        """Features of one group in order: a VOID feature erases what came before."""
        result = Polygon()
        run, negative = [], False
        for geometry, void in items + [(Polygon(), None)]:
            if void != negative and run:
                union = unary_union(run)
                result = result.difference(union) if negative else unary_union([result, union])
                run = []
            if void is not None:
                negative = void
                if not geometry.is_empty:
                    run.append(geometry)
        return result

    # ------------------------------------------------------------------------------------------------ transforms
    def _xform(self, node, factor):
        if node is None:
            return None
        scale = self._number(node, 'scale', 1.0, 1.0)
        if scale <= 0:
            raise IPC2581ParseError('Xform scale must be positive')
        return (_text_flag(node.get('mirror', 'false')), scale, self._number(node, 'rotation', 1.0, 0.0),
                self._number(node, 'xOffset', factor, 0.0), self._number(node, 'yOffset', factor, 0.0))

    @staticmethod
    def _matrix(xf, location):
        mirror, scale, rotation, dx, dy = xf if xf else (False, 1.0, 0.0, 0.0, 0.0)
        c, s = _cos_sin(rotation)
        m = -1.0 if mirror else 1.0
        return [m * scale * c, -scale * s, m * scale * s, scale * c, dx + location[0], dy + location[1]]

    def _apply(self, geometry, xf, location):
        if geometry.is_empty:
            return geometry
        if xf is None and location == (0.0, 0.0):
            return geometry
        return affinity.affine_transform(geometry, self._matrix(xf, location))

    # ------------------------------------------------------------------------------------------------ step
    def _read_padstacks(self, step):
        for node in _children(step):
            if _local(node) != 'PadStackDef':
                continue
            pads = {}
            for pad in _children(node):
                if _local(pad) == 'PadstackPadDef' and pad.get('layerRef'):
                    pads.setdefault(pad.get('layerRef'), []).append(pad)
            self._padstacks[node.get('name')] = pads

    def _profile(self, step):
        node = next((c for c in _children(step) if _local(c) == 'Profile'), None)
        if node is None:
            return Polygon()
        shell = next((c for c in _children(node) if _local(c) == 'Polygon'), None)
        if shell is None:
            return Polygon()
        polygon = self._polygon(shell, self._factor)
        cutouts = [self._polygon(c, self._factor) for c in _children(node) if _local(c) == 'Cutout']
        cutouts = [c for c in cutouts if not c.is_empty]
        if cutouts and not polygon.is_empty:
            polygon = polygon.difference(unary_union(cutouts))
        return polygon

    def _build(self, layer_info):
        step = self._step_node
        self._read_padstacks(step)
        self.board_profile = self._profile(step)
        self.board_outline = self.board_profile
        built = {}          # layer name -> {'items': [(negative, geometry)], 'follow': [], 'holes': []}
        for feature_layer in _children(step):
            if _local(feature_layer) != 'LayerFeature':
                continue
            name = feature_layer.get('layerRef')
            if name not in layer_info:
                raise IPC2581ParseError('LayerFeature refers to the unknown layer %r' % name)
            info = layer_info[name]
            if info['kind'] is None:
                continue
            entry = built.setdefault(name, {'items': [], 'follow': [], 'holes': [], 'primitives': []})
            for node in _children(feature_layer):
                if _local(node) == 'Set':
                    self._read_set(node, info, entry)
        outline_lines = []
        for name, info in layer_info.items():
            entry = built.get(name)
            if entry is None:
                continue
            kind = info['kind']
            if kind == 'drill' or entry['holes']:
                self._drill_group(name, entry['holes'])
            if kind in (None, 'drill'):
                continue
            geometry = self._merge(entry['items'])
            if info['polarity'] == 'NEGATIVE':
                if self.board_profile.is_empty:
                    raise IPC2581ParseError('The negative layer %r needs a board profile' % name)
                geometry = self.board_profile.difference(geometry)
                entry['follow'] = []
            polygons = _polygons(geometry)
            if kind == 'outline':
                outline_lines.extend(entry['follow'])
            if not polygons and not entry['follow']:
                continue
            for shape, solid, path, w, h, angle in entry['primitives']:
                if shape == 'stroke':
                    self._primitives.stroke(name, solid, path, w)
                else:
                    self._primitives.flash(name, solid, path, shape, w, h, angle)
            self._add_layer(name, polygons, entry['follow'])
            if kind in DEFAULT_ON:
                self.default_layers.append(name)
            if kind in ('mask', 'paste'):
                self._note('The features of mask and paste layers are taken as the openings.')
        if self.board_profile.is_empty and outline_lines:
            self._outline_from_lines(outline_lines)
        self._profile_layer(layer_info, outline_lines)
        self.default_drill_groups = list(self.drill_groups)
        if not self.layers and not self.drill_groups:
            raise IPC2581ParseError('The IPC-2581 file has no manufacturing geometry')
        self._note('Only the artwork is imported (copper, mask, paste, silkscreen, outline, drills); components, '
                   'packages, BOM, stackup and nets are ignored.')
        self._flush_counts()

    def _add_layer(self, name, polygons, follow):
        self.layers[name] = {'name': name, 'solid_geometry': polygons, 'follow_geometry': follow,
                             'tools': {0: {'type': 'REG', 'size': 0.0,
                                           'geometry': [{'solid': polygon} for polygon in polygons]}}}
        self._primitives.finish(name, self.layers[name])

    def _outline_from_lines(self, lines):
        faces = list(polygonize(unary_union(lines)))
        if not faces:
            return
        faces.sort(key=lambda face: face.area, reverse=True)
        board = faces[0]
        holes = [face for face in faces[1:] if board.contains(face)]
        self.board_profile = board.difference(unary_union(holes)) if holes else board
        self.board_outline = self.board_profile
        self._note('The file has no Step/Profile; the board outline layer closes the board profile.')

    def _profile_layer(self, layer_info, outline_lines):
        rings = []
        for polygon in _polygons(self.board_profile):
            rings.append(polygon.exterior)
            rings.extend(polygon.interiors)
        if not rings:
            self._note('The file has no board profile.')
            return
        name = 'profile'
        while name in self.layers or name in layer_info:
            name += ' (outline)'
        solid = _polygons(unary_union([ring.buffer(PROFILE_WIDTH_MM / 2, join_style=2) for ring in rings]))
        self._add_layer(name, solid, [LineString(ring.coords) for ring in rings])
        if not any(layer_info[n]['kind'] == 'outline' for n in self.layers if n in layer_info):
            self.default_layers.append(name)

    # ------------------------------------------------------------------------------------------------ sets
    def _read_set(self, node, info, entry):
        factor = self._factor
        strict = info['kind'] in STRICT_KINDS
        set_negative = (node.get('polarity') or 'POSITIVE').strip().upper() == 'NEGATIVE'
        self._set_line = self._set_fill = None
        self._set_line, self._set_fill = self._descs_of_set(node, factor)
        follow_on = info['polarity'] != 'NEGATIVE' and not set_negative
        for child in _children(node):
            tag = _local(child)
            try:
                if tag in ('Pad', 'Fiducial'):
                    self._read_pad(child, info, entry, set_negative, factor)
                elif tag == 'Features':
                    self._read_features(child, entry, set_negative, factor, follow_on)
                elif tag == 'Hole':
                    self._read_hole(child, entry, factor)
                elif tag == 'SlotCavity':
                    self._read_slot(child, entry, factor)
                elif tag not in SET_TAGS and strict:
                    raise _Unsupported(tag)
            except _Unsupported as error:
                if strict:
                    raise IPC2581ParseError('Layer %s: the IPC-2581 feature %s is not supported (the copper would '
                                            'be wrong)' % (info['name'], error)) from None
                self._count('%d unsupported feature(s) of non-copper layers were skipped (text, hatch fills, '
                            'special shapes).')
        self._set_line = self._set_fill = None

    def _descs_of_set(self, node, factor):
        saved = (self._set_line, self._set_fill)
        self._set_line = self._set_fill = None
        try:
            return self._descs(node, factor)
        finally:
            self._set_line, self._set_fill = saved

    def _read_pad(self, node, info, entry, set_negative, factor):
        xform = next((c for c in _children(node) if _local(c) == 'Xform'), None)
        location = next((c for c in _children(node) if _local(c) == 'Location'), None)
        if location is None:
            if _local(node) == 'Fiducial':
                location = node
            else:
                raise IPC2581ParseError('A Pad has no Location')
        feature = next((c for c in _children(node) if _local(c) in FEATURE_TAGS), None)
        at = (self._number(location, 'x', factor), self._number(location, 'y', factor))
        xf = self._xform(xform, factor)
        if feature is None:
            self._padstack_pad(node, info, entry, set_negative, at, xf)
            return
        geometry, void = self._feature(feature, factor, source_pad=True)
        if not (set_negative or void) or info['polarity'] == 'NEGATIVE':
            entry['primitives'].extend(self._place_primitives(self._last_primitives, xf, at))
        self._add_item(entry, self._apply(geometry, xf, at), set_negative or void)

    def _padstack_pad(self, node, info, entry, set_negative, at, xf):
        pads = self._padstacks.get(node.get('padstackDefRef'), {}).get(info['name'])
        if not pads:
            raise IPC2581ParseError('A Pad of layer %s has no shape and no padstack shape' % info['name'])
        factor = self._factor
        for pad in pads:
            feature = next((c for c in _children(pad) if _local(c) in FEATURE_TAGS), None)
            location = next((c for c in _children(pad) if _local(c) == 'Location'), None)
            if feature is None or location is None:
                continue
            geometry, void = self._feature(feature, factor, source_pad=True)
            inner_xf = self._xform(next((c for c in _children(pad) if _local(c) == 'Xform'), None), factor)
            inner_at = (self._number(location, 'x', factor), self._number(location, 'y', factor))
            inner = self._apply(geometry, inner_xf, inner_at)
            if not (set_negative or void) or info['polarity'] == 'NEGATIVE':
                records = self._place_primitives(self._last_primitives, inner_xf, inner_at)
                entry['primitives'].extend(self._place_primitives(records, xf, at))
            self._add_item(entry, self._apply(inner, xf, at), set_negative or void)

    def _read_features(self, node, entry, set_negative, factor, follow_on):
        xform = next((c for c in _children(node) if _local(c) == 'Xform'), None)
        xf = self._xform(xform, factor)
        locations = [(self._number(c, 'x', factor), self._number(c, 'y', factor))
                     for c in _children(node) if _local(c) == 'Location']
        features = [c for c in _children(node) if _local(c) in FEATURE_TAGS]
        for feature in features:
            self._follow_buffer = [] if follow_on else None
            try:
                geometry, void = self._feature(feature, factor)
                lines = self._follow_buffer or []
            finally:
                self._follow_buffer = None
            if geometry.is_empty and not lines:
                continue
            placements = locations or [(0.0, 0.0)]
            vertices = max(1, sum(len(p.exterior.coords) for p in _polygons(geometry)))
            self._spend(vertices * len(placements) if len(placements) > 1 else 0)
            for at in placements:
                entry['primitives'].extend(self._place_primitives(self._last_primitives, xf, at))
                self._add_item(entry, self._apply(geometry, xf, at), set_negative or void)
                for line in lines:
                    entry['follow'].append(self._apply(line, xf, at))

    @staticmethod
    def _add_item(entry, geometry, negative):
        if not geometry.is_empty:
            entry['items'].append((geometry, negative))

    # ------------------------------------------------------------------------------------------------ holes
    @staticmethod
    def _plated(node):
        status = (node.get('platingStatus') or '').strip().upper()
        if status in ('PLATED', 'VIA', 'VIA_CAPPED'):
            return True
        if status == 'NONPLATED':
            return False
        raise IPC2581ParseError('A hole has the unknown platingStatus %r' % node.get('platingStatus'))

    def _read_hole(self, node, entry, factor):
        diameter = self._number(node, 'diameter', factor)
        if diameter < 0:
            raise IPC2581ParseError('A hole has a negative diameter')
        if diameter == 0:
            return
        if (node.get('type') or 'CIRCLE').strip().upper() == 'SQUARE':
            self._count('%d square hole(s) were imported as round drills.')
        entry['holes'].append(('drill', (self._number(node, 'x', factor), self._number(node, 'y', factor)),
                               diameter, self._plated(node)))

    def _read_slot(self, node, entry, factor):
        location = next((c for c in _children(node) if _local(c) == 'Location'), None)
        xf = self._xform(next((c for c in _children(node) if _local(c) == 'Xform'), None), factor)
        at = ((self._number(location, 'x', factor), self._number(location, 'y', factor))
              if location is not None else (0.0, 0.0))
        shape = next((c for c in _children(node) if _local(c) in FEATURE_TAGS), None)
        plated = self._plated(node)
        tag = _local(shape) if shape is not None else ''
        if tag in ('Outline', 'Polygon', 'Contour'):
            # revision B: the outline of the hole in board coordinates (KiCad); fitted by a stadium
            if tag == 'Contour':
                outline = self._contour(shape, factor)[0]
            else:
                body = shape if tag == 'Polygon' else next((c for c in _children(shape) if _local(c) == 'Polygon'),
                                                           None)
                outline = self._polygon(body, factor) if body is not None else Polygon()
            self._fit_slot(self._apply(outline, xf, at), entry, plated)
            return
        if location is None:
            self._count('%d slot(s) without a Location were skipped (KiCad revision B writes none; export '
                        'revision C).')
            return
        if tag == 'Circle':
            diameter = self._number(shape, 'diameter', factor)
            centre = self._apply(Point(0, 0), xf, at)
            if diameter > 0:
                entry['holes'].append(('drill', (centre.x, centre.y), diameter, plated))
            return
        if tag == 'Oval':
            w, h = self._number(shape, 'width', factor), self._number(shape, 'height', factor)
            tool = min(w, h)
            if tool <= 0:
                return
            half = (max(w, h) - tool) / 2
            ends = [(-half, 0.0), (half, 0.0)] if w >= h else [(0.0, -half), (0.0, half)]
            placed = [self._apply(Point(p), xf, at) for p in ends]
            if half <= 1e-9:
                entry['holes'].append(('drill', (placed[0].x, placed[0].y), tool, plated))
            else:
                entry['holes'].append(('slot', ((placed[0].x, placed[0].y), (placed[1].x, placed[1].y)), tool, plated))
            return
        self._count('%d slot(s) that are not round-ended were skipped.')

    def _fit_slot(self, outline, entry, plated):
        """A hole given as a polygon: a round hole or a slot with round ends is fitted to it, else it is skipped."""
        if outline.is_empty or outline.area <= 0:
            return
        box_ = outline.minimum_rotated_rectangle
        corners = list(box_.exterior.coords)
        first = math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
        second = math.hypot(corners[2][0] - corners[1][0], corners[2][1] - corners[1][1])
        tool, length = min(first, second), max(first, second)
        if tool <= 0:
            return
        stadium = (length - tool) * tool + math.pi * tool * tool / 4
        if abs(outline.area - stadium) > 0.1 * stadium:
            self._count('%d hole outline(s) that are not round or slots with round ends were skipped.')
            return
        centre = box_.centroid
        self._note('Slots and holes given as outlines are fitted by their smallest rectangle; the diameter is read '
                   'from the outline polygon.')
        if length - tool <= 0.02 * tool:
            entry['holes'].append(('drill', (centre.x, centre.y), (tool + length) / 2, plated))
            return
        # the long axis of the rectangle
        if first >= second:
            axis = (corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
        else:
            axis = (corners[2][0] - corners[1][0], corners[2][1] - corners[1][1])
        norm = math.hypot(*axis)
        half = (length - tool) / 2
        direction = (axis[0] / norm * half, axis[1] / norm * half)
        entry['holes'].append(('slot', ((centre.x - direction[0], centre.y - direction[1]),
                                        (centre.x + direction[0], centre.y + direction[1])), tool, plated))

    def _drill_group(self, layer, holes):
        for plated, suffix in ((True, 'PTH'), (False, 'NPTH')):
            tools = {}
            for kind, position, diameter, hole_plated in holes:
                if hole_plated != plated:
                    continue
                tool = tools.setdefault(round(diameter, 6), {'tooldia': round(diameter, 6), 'drills': [],
                                                             'slots': []})
                if kind == 'drill':
                    tool['drills'].append(Point(position))
                else:
                    tool['slots'].append((Point(position[0]), Point(position[1])))
            if tools:
                group = '%s %s' % (layer, suffix)
                self.drill_groups[group] = {i: tool for i, (_dia, tool) in enumerate(sorted(tools.items()), 1)}
