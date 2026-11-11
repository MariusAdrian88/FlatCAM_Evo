# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# EasyEDA Pro board reader                                  #
# Author: Marius Stanciu (c)                                #
# Format facts also from TRACE Boardviewer (MIT)            #
# https://github.com/trace-boardviewer/trace-boardviewer    #
# MIT Licence                                               #
# ##########################################################

"""Read the manufacturing geometry of an EasyEDA Pro board (V2 ``.epro`` and V3 ``.epro2`` projects, bare PCB documents).

Independent implementation. The format facts come from the vendor documents and from the real files that were measured
against the Gerber files of EasyEDA Pro itself:

* V2 (project ZIP ``.epro`` with ``project.json``, ``PCB/``, ``FOOTPRINT/``, ``POUR/``; one JSON array per line):
  https://github.com/easyeda/easyeda-file-format-v2 (``docs/en/general/project-structure.md``, ``docs/en/pcb/*``).
* V3 (project ZIP ``.epro2`` with ``project2.json`` and ``*.epru`` logs: one ``{header}||{payload}|`` record per line, the highest
  ``ticket`` of a ``type`` and ``id`` wins, an empty payload deletes it, ``DELETE_DOC`` deletes a document):
  https://prodocs.easyeda.com/en/format/index/ , https://prodocs.easyeda.com/en/format/pcb/common/ ,
  https://prodocs.easyeda.com/en/format/pcb/pad_via/ , https://prodocs.easyeda.com/en/format/pcb/shape/ .
* Container layout, record layout of the COMPONENT / ATTR / PAD_NET records and the bottom-side placement rule (a bottom part is
  mirrored in Y and then turned by its angle; a bottom pad takes the part angle minus its own angle): TRACE Boardviewer
  ``src/lib/formats/easyeda-pro.ts`` and ``easyeda-pro-v3.ts`` (MIT, Copyright (c) 2026 TRACE Boardviewer contributors),
  https://github.com/trace-boardviewer/trace-boardviewer . TRACE reads pins, nets and the outline only; the copper reading
  here (tracks, arcs, vias, pads, fills, stored pour results, mask, paste, silkscreen, holes) is new.
* Units, measured against the Gerber files (they disagree with the vendor text, which says 0.01 inch for everything): every
  length of a record is in mil (0.0254 mm); the coordinates of the stored pour results (``POURED``) and of the text cache
  (``FONT``) are in 0.01 inch (0.254 mm). The ``CANVAS`` unit is a display unit only.
* KiCad's EasyEDA Pro importer (https://dev-docs.kicad.org/en/import-formats/easyeda/index.html) was read for the V2 record
  names only; no code was taken.

Result (same shape as ``KiCadPcbParser`` / ``ODBParser``): ``layers`` {key: {name, solid_geometry, follow_geometry, tools}},
``drill_groups`` {group: {tool: {tooldia, drills, slots}}}, ``default_layers``, ``warnings``, ``board_outline``; millimetres,
Y up. A project with more than one PCB returns a metadata result (``pcbs`` set, ``pcb`` None, no geometry); parse again with
``parse_file(path, pcb=<one of pcbs>)``. Copper that cannot be read exactly raises ``EasyEDAProParseError``; nothing is guessed.
"""

import base64
import binascii
import json
import math
import re
import zipfile
import zlib

from shapely import LineString, Point, Polygon, STRtree
from shapely.affinity import affine_transform
from shapely.affinity import rotate as shapely_rotate
from shapely.geometry import box as shapely_box
from shapely.ops import polygonize, unary_union

from camlib import circle_quad_segs, gerber_circle_steps

MIL_MM = 0.0254
POURED_MM = 0.254            # unit of the pour results and of the font cache: 0.01 inch
MAX_SOURCE = 1e8             # a coordinate beyond this is not board geometry
MAX_FILE_BYTES = 512 << 20
MAX_ENTRY_BYTES = 192 << 20
MAX_TOTAL_BYTES = 768 << 20
MAX_ENTRIES = 20000
RATIO_LIMIT = 250            # uncompressed / compressed, for an entry above RATIO_MIN_BYTES and for the archive
RATIO_MIN_BYTES = 1 << 20
MAX_RECORDS = 4_000_000
MAX_LINE_CHARS = 48 << 20
MAX_POINTS = 6_000_000       # sampled points of all arcs and circles together
MAX_PATH_TOKENS = 1_000_000
MAX_PADS = 1_000_000
OUTLINE_WIDTH_MM = 0.1
CUTOUT_WIDTH_MM = 0.0254     # drawn width of a multi-layer fill cutout in the Gerber outline of EasyEDA Pro (measured)

COPPER_TYPES = ('TOP', 'BOTTOM', 'SIGNAL')
DEFAULT_LAYER_TYPES = {1: 'TOP', 2: 'BOTTOM', 3: 'TOP_SILK', 4: 'BOT_SILK', 5: 'TOP_SOLDER_MASK', 6: 'BOT_SOLDER_MASK',
                       7: 'TOP_PASTE_MASK', 8: 'BOT_PASTE_MASK', 9: 'TOP_ASSEMBLY', 10: 'BOT_ASSEMBLY', 11: 'OUTLINE',
                       12: 'MULTI', 13: 'DOCUMENT', 14: 'MECHANICAL'}
DEFAULT_LAYER_NAMES = {'TOP': 'Top Layer', 'BOTTOM': 'Bottom Layer', 'TOP_SILK': 'Top Silkscreen Layer',
                       'BOT_SILK': 'Bottom Silkscreen Layer', 'TOP_SOLDER_MASK': 'Top Solder Mask Layer',
                       'BOT_SOLDER_MASK': 'Bottom Solder Mask Layer', 'TOP_PASTE_MASK': 'Top Paste Mask Layer',
                       'BOT_PASTE_MASK': 'Bottom Paste Mask Layer', 'OUTLINE': 'Board Outline Layer'}
FLIP_TYPE = {'TOP': 'BOTTOM', 'BOTTOM': 'TOP', 'TOP_SILK': 'BOT_SILK', 'BOT_SILK': 'TOP_SILK',
             'TOP_SOLDER_MASK': 'BOT_SOLDER_MASK', 'BOT_SOLDER_MASK': 'TOP_SOLDER_MASK',
             'TOP_PASTE_MASK': 'BOT_PASTE_MASK', 'BOT_PASTE_MASK': 'TOP_PASTE_MASK',
             'TOP_ASSEMBLY': 'BOT_ASSEMBLY', 'BOT_ASSEMBLY': 'TOP_ASSEMBLY'}
OUTPUT_TYPES = ('TOP_SILK', 'BOT_SILK', 'TOP_SOLDER_MASK', 'BOT_SOLDER_MASK', 'TOP_PASTE_MASK', 'BOT_PASTE_MASK')
# layer types that carry no manufacturing data (documentation, 3D, assembly, component helper layers)
IGNORED_LAYER_TYPES = frozenset(['TOP_ASSEMBLY', 'BOT_ASSEMBLY', 'DOCUMENT', 'MECHANICAL', 'SUBSTRATE', 'HOLE', 'SHELL',
                                 'TOP_SHELL', 'BOT_SHELL', 'COMPONENT_MODEL', 'COMPONENT_SHAPE', 'PIN_FLOATING',
                                 'COMPONENT_MARKING', 'PIN_SOLDERING', 'PIN_FLOATING', 'DRC_ERROR', 'FLYING_LINE',
                                 'DIMENSION'])

# record types the readers keep
KEPT = frozenset(['LAYER', 'COMPONENT', 'ATTR', 'PAD', 'VIA', 'LINE', 'ARC', 'CARC', 'POLY', 'FILL', 'REGION', 'POUR',
                  'POURED', 'TEARDROP', 'STRING', 'IMAGE', 'OBJ', 'FONT', 'RULE', 'DIMENSION', 'FPC_FILL', 'META', 'CANVAS'])
# record types that are known and carry no copper, mask, paste, silkscreen or hole
HARMLESS = frozenset(['DOCTYPE', 'HEAD', 'LAYER_PHYS', 'ACTIVE_LAYER', 'NET', 'RULE_TEMPLATE', 'RULE_SELECTOR', 'PRIMITIVE',
                      'GROUP', 'SILK_OPTS', 'PREFERENCE', 'PANELIZE', 'PANELIZE_STAMP', 'PANELIZE_SIDE', 'CONNECT', 'PROP',
                      'ITEM_ORDER', 'EQLEN_GRP', 'EQLEN', 'REUSE_BLOCK', 'PAD_NET', 'SHELL', 'SHELL_ENTITY', 'SHELLCUT',
                      'CREASE', 'BOSS', 'ELE_PLACEHOLDER', 'D3_ATTRIBUTE', 'ACTIVE_LAYER', 'VARIANT', 'GROUP_MEMBER',
                      'NET_CLASS', 'DIFF_PAIR', 'PARTITION', 'PANELIZE_OPT', 'CONFIG', 'LAYER_ATTR', 'DOCHEAD',
                      'DELETE_DOC', 'ORDER', 'FOOTPRINT', 'DEVICE', 'PROJECT_CONFIG', 'RULE_GROUP', 'LENGTH_RULE',
                      'AUTO_ROUTER', 'MARK', 'SNAP', 'GRID', 'ZONE', 'POINT_ORDER', 'NET_RULE', 'PCB_INFO', 'ASSET'])


class EasyEDAProParseError(ValueError):
    """Malformed archive or document, or board data that cannot be turned into exact manufacturing geometry."""


def _number(value, what='value'):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str) and value.strip():
            try:
                value = float(value)
            except ValueError:
                raise EasyEDAProParseError('EasyEDA Pro: %s is not a number' % what) from None
        else:
            raise EasyEDAProParseError('EasyEDA Pro: %s is missing or not a number' % what)
    if not math.isfinite(value) or abs(value) > MAX_SOURCE:
        raise EasyEDAProParseError('EasyEDA Pro: %s is outside the supported range' % what)
    return float(value)


def _opt(value, default=0.0, what='value'):
    return default if value is None else _number(value, what)


def _text(value):
    return value if isinstance(value, str) else ('' if value is None else str(value))


# --------------------------------------------------------------------------------------------------------------------
# archive
# --------------------------------------------------------------------------------------------------------------------

def _entry_name(name):
    """The normalized member name of an archive, or EasyEDAProParseError for an unsafe one."""
    norm = name.replace('\\', '/')
    if norm.startswith('/') or re.match(r'^[A-Za-z]:', norm) or '\x00' in norm:
        raise EasyEDAProParseError('EasyEDA Pro: unsafe path in the archive: %r' % name[:80])
    parts = norm.split('/')
    if any(part == '..' for part in parts):
        raise EasyEDAProParseError('EasyEDA Pro: unsafe path in the archive: %r' % name[:80])
    return '/'.join(part for part in parts if part not in ('', '.'))


class _Package:
    """Bounded access to the members of an EasyEDA Pro ZIP, or to one bare document."""

    def __init__(self, path):
        self.path = str(path)
        self.zip = None
        self.entries = {}
        self.bare = None
        try:
            with open(self.path, 'rb') as handle:
                head = handle.read(4)
                handle.seek(0, 2)
                size = handle.tell()
        except OSError as error:
            raise EasyEDAProParseError('EasyEDA Pro: the file cannot be read: %s' % error.strerror) from error
        if size > MAX_FILE_BYTES:
            raise EasyEDAProParseError('EasyEDA Pro: the file is larger than %d MB' % (MAX_FILE_BYTES >> 20))
        if size == 0:
            raise EasyEDAProParseError('EasyEDA Pro: the file is empty')
        if head[:2] == b'PK':
            self._open_zip(size)
        elif head[:2] in (b'\x1f\x8b', b'7z', b'Ra') or head == b'SQLi':
            raise EasyEDAProParseError('EasyEDA Pro: the file is not an EasyEDA Pro project (offline .eprj databases must '
                                       'be exported from EasyEDA Pro as .epro / .epro2)')
        else:
            with open(self.path, 'rb') as handle:
                self.bare = handle.read(MAX_ENTRY_BYTES + 1)
            if len(self.bare) > MAX_ENTRY_BYTES:
                raise EasyEDAProParseError('EasyEDA Pro: the document is larger than %d MB' % (MAX_ENTRY_BYTES >> 20))

    def _open_zip(self, size):
        try:
            self.zip = zipfile.ZipFile(self.path)
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, OSError, EOFError, zipfile.LargeZipFile, NotImplementedError) as error:
            raise EasyEDAProParseError('EasyEDA Pro: the archive is damaged: %s' % error) from error
        if len(infos) > MAX_ENTRIES:
            raise EasyEDAProParseError('EasyEDA Pro: the archive has more than %d entries' % MAX_ENTRIES)
        total = packed = 0
        seen = set()
        for info in infos:
            name = _entry_name(info.filename)
            if info.is_dir() or not name:
                continue
            key = name.lower()
            if key in seen:
                raise EasyEDAProParseError('EasyEDA Pro: the archive lists %r twice' % name[:80])
            seen.add(key)
            if info.flag_bits & 0x1:
                raise EasyEDAProParseError('EasyEDA Pro: the archive is encrypted')
            if info.file_size > MAX_ENTRY_BYTES:
                raise EasyEDAProParseError('EasyEDA Pro: %r is larger than %d MB' % (name[:80], MAX_ENTRY_BYTES >> 20))
            if info.file_size > RATIO_MIN_BYTES and info.file_size > RATIO_LIMIT * max(info.compress_size, 1):
                raise EasyEDAProParseError('EasyEDA Pro: %r has an implausible compression ratio' % name[:80])
            total += info.file_size
            packed += info.compress_size
            self.entries[name] = info
        if total > MAX_TOTAL_BYTES or (total > RATIO_MIN_BYTES and total > RATIO_LIMIT * max(packed, 1)):
            raise EasyEDAProParseError('EasyEDA Pro: the archive expands to an implausible size')

    def names(self):
        return list(self.entries)

    def read(self, name, limit=MAX_ENTRY_BYTES):
        """The bytes of a member; the decoded size is counted, not the size the header declares."""
        if self.zip is None:
            return self.bare
        info = self.entries[name]
        cap = min(limit, info.file_size)
        try:
            with self.zip.open(info) as handle:
                data = handle.read(cap + 1)
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, OSError, EOFError) as error:
            raise EasyEDAProParseError('EasyEDA Pro: %r cannot be read: %s' % (name[:80], error)) from error
        if len(data) > cap:
            raise EasyEDAProParseError('EasyEDA Pro: %r is larger than its header declares' % name[:80])
        return data

    def read_text(self, name, limit=MAX_ENTRY_BYTES):
        data = self.read(name, limit)
        try:
            return data.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise EasyEDAProParseError('EasyEDA Pro: %r is not UTF-8 text' % name[:80]) from error

    def close(self):
        if self.zip is not None:
            self.zip.close()


# --------------------------------------------------------------------------------------------------------------------
# documents: V2 line arrays and V3 logs both become a _Doc with V3 style record dicts
# --------------------------------------------------------------------------------------------------------------------

class _Doc:
    def __init__(self, doc_type='', uuid=''):
        self.doc_type = doc_type
        self.uuid = uuid
        self.recs = {}             # type -> [dict]
        self.title = ''
        self.version = ''

    def add(self, kind, record):
        self.recs.setdefault(kind, []).append(record)

    def get(self, kind):
        return self.recs.get(kind, [])

    def content(self):
        return any(self.recs.get(kind) for kind in ('COMPONENT', 'LINE', 'ARC', 'CARC', 'VIA', 'POUR', 'POLY', 'PAD', 'FILL',
                                                    'REGION', 'TEARDROP', 'STRING'))


def _at(array, index, default=None):
    return array[index] if index < len(array) else default


def _hole(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) < 3:
        raise EasyEDAProParseError('EasyEDA Pro: a hole definition is malformed')
    return {'holeType': _text(value[0]).upper(), 'width': value[1], 'height': value[2]}


def _pad_shape(value):
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        return None
    kind = value[0].upper()
    if kind in ('ROUND', 'ELLIPSE'):
        return {'padType': 'ELLIPSE', 'width': _at(value, 1), 'height': _at(value, 2, _at(value, 1))}
    if kind == 'RECT':
        return {'padType': 'RECT', 'width': _at(value, 1), 'height': _at(value, 2, _at(value, 1)), 'radius': _at(value, 3, 0)}
    if kind == 'OVAL':
        return {'padType': 'OVAL', 'width': _at(value, 1), 'height': _at(value, 2, _at(value, 1))}
    if kind == 'NGON':
        return {'padType': 'NGON', 'diameter': _at(value, 1), 'sides': _at(value, 2)}
    if kind in ('POLY', 'POLYGON'):
        return {'padType': 'POLYGON', 'path': _at(value, 1)}
    return {'padType': kind}


def _v2_record(name, a):
    """A V2 line array -> V3 style payload dict (key names of the V3 documents), or None for a record that is not kept."""
    if name == 'LAYER':
        state = int(_at(a, 4, 3) or 0)
        return {'layerId': _at(a, 1), 'layerType': _text(_at(a, 2)), 'layerName': _text(_at(a, 3)), 'use': bool(state & 1)}
    if name == 'COMPONENT':
        return {'id': _at(a, 1), 'layerId': _at(a, 3), 'x': _at(a, 4), 'y': _at(a, 5), 'angle': _at(a, 6), 'attrs': _at(a, 7) or {}}
    if name == 'ATTR':
        return {'id': _at(a, 1), 'parentId': _at(a, 3, ''), 'layerId': _at(a, 4), 'x': _at(a, 5), 'y': _at(a, 6),
                'key': _text(_at(a, 7)), 'value': _at(a, 8), 'keyVisible': bool(_at(a, 9)), 'valueVisible': bool(_at(a, 10))}
    if name == 'LINE':
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'layerId': _at(a, 4), 'startX': _at(a, 5), 'startY': _at(a, 6),
                'endX': _at(a, 7), 'endY': _at(a, 8), 'width': _at(a, 9)}
    if name in ('ARC', 'CARC'):
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'layerId': _at(a, 4), 'startX': _at(a, 5), 'startY': _at(a, 6),
                'endX': _at(a, 7), 'endY': _at(a, 8), 'angle': _at(a, 9), 'width': _at(a, 10)}
    if name == 'VIA':
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'ruleName': _at(a, 4), 'centerX': _at(a, 5), 'centerY': _at(a, 6),
                'holeDiameter': _at(a, 7), 'viaDiameter': _at(a, 8), 'topSolderExpansion': _at(a, 10),
                'bottomSolderExpansion': _at(a, 11), 'unusedInnerLayers': _at(a, 13) or []}
    if name == 'PAD':
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'layerId': _at(a, 4), 'num': _text(_at(a, 5)), 'centerX': _at(a, 6),
                'centerY': _at(a, 7), 'padAngle': _at(a, 8), 'hole': _hole(_at(a, 9)), 'defaultPad': _pad_shape(_at(a, 10)),
                'specialPad': _at(a, 11) or [], 'padOffsetX': _at(a, 12), 'padOffsetY': _at(a, 13),
                'relativeAngle': _at(a, 14), 'plated': _at(a, 15), 'padType': _at(a, 16),
                'topSolderExpansion': _at(a, 17), 'bottomSolderExpansion': _at(a, 18),
                'topPasteExpansion': _at(a, 19), 'bottomPasteExpansion': _at(a, 20)}
    if name == 'POLY':
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'layerId': _at(a, 4), 'width': _at(a, 5), 'path': _at(a, 6)}
    if name == 'FILL':
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'layerId': _at(a, 4), 'width': _at(a, 5), 'fillStyle': _at(a, 6),
                'path': _at(a, 7)}
    if name == 'REGION':
        return {'id': _at(a, 1), 'layerId': _at(a, 3), 'width': _at(a, 4), 'prohibitType': _at(a, 5) or [], 'path': _at(a, 6)}
    if name == 'POUR':
        return {'id': _at(a, 1), 'netName': _at(a, 3), 'layerId': _at(a, 4), 'width': _at(a, 5), 'name': _at(a, 6),
                'path': _at(a, 8)}
    if name == 'POURED':
        return {'id': _at(a, 1), 'parent': _at(a, 2), 'pourFill': [{'strokeWidth': _at(a, 3), 'fill': _at(a, 4), 'path': _at(a, 5)}]}
    if name == 'TEARDROP':
        return {'id': _at(a, 1), 'netName': _at(a, 2), 'layerId': _at(a, 3), 'path': _at(a, 4)}
    if name == 'STRING':
        return {'id': _at(a, 1), 'layerId': _at(a, 3), 'x': _at(a, 4), 'y': _at(a, 5), 'text': _at(a, 6)}
    if name == 'IMAGE':
        return {'id': _at(a, 1), 'layerId': _at(a, 3), 'path': _at(a, 10)}
    if name == 'OBJ':
        return {'id': _at(a, 1), 'layerId': _at(a, 3)}
    if name == 'FONT':
        return {'text': _at(a, 1), 'font': _at(a, 2), 'size': _at(a, 3), 'path': _at(a, 11)}
    if name == 'RULE':
        return {'rule': _text(_at(a, 2)), 'state': _at(a, 3), 'values': _at(a, 4)}
    if name == 'DIMENSION':
        return {'id': _at(a, 1), 'layerId': _at(a, 3)}
    if name == 'FPC_FILL':
        return {'id': _at(a, 1)}
    if name == 'CANVAS':
        return {}
    return None


def _read_v2(text, label, wanted=KEPT):
    """A V2 document (``["TYPE", ...]`` per line) -> _Doc."""
    doc = _Doc()
    count = 0
    first = True
    for number, raw in enumerate(text.split('\n'), 1):
        line = raw.strip()
        if not line:
            continue
        count += 1
        if count > MAX_RECORDS:
            raise EasyEDAProParseError('EasyEDA Pro: the %s has more than %d records' % (label, MAX_RECORDS))
        if line[0] != '[' or line[-1] != ']':
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is not a JSON array' % (number, label))
        if len(line) > MAX_LINE_CHARS:
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is too long' % (number, label))
        match = re.match(r'\[\s*"([A-Z_0-9]{1,24})"', line)
        name = match.group(1) if match else ''
        if first:
            first = False
            if name != 'DOCTYPE':
                raise EasyEDAProParseError('EasyEDA Pro: the %s does not start with DOCTYPE' % label)
            try:
                head = json.loads(line)
            except (ValueError, RecursionError):
                raise EasyEDAProParseError('EasyEDA Pro: the %s header is not valid JSON' % label) from None
            doc.doc_type = _text(_at(head, 1))
            doc.version = _text(_at(head, 2))
            continue
        if name not in wanted:
            if name and name not in HARMLESS:
                doc.recs.setdefault('?' + name, []).append(None)
            continue
        try:
            array = json.loads(line)
        except (ValueError, RecursionError):
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is not valid JSON' % (number, label)) from None
        if not isinstance(array, list):
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is not a JSON array' % (number, label))
        record = _v2_record(name, array)
        if record is not None:
            doc.add(name, record)
    if first:
        raise EasyEDAProParseError('EasyEDA Pro: the %s is empty' % label)
    if doc.doc_type not in ('PCB', 'FOOTPRINT', 'POURED'):
        raise EasyEDAProParseError('EasyEDA Pro: the %s is a %s document, not a PCB' % (label, doc.doc_type[:20] or 'unknown'))
    if doc.version and not re.match(r'^1(\.|$)', doc.version):
        raise EasyEDAProParseError('EasyEDA Pro: PCB format version %s is not supported' % doc.version[:12])
    return doc


def _read_v3(text, label):
    """A V3 log -> {uuid: _Doc} of the live documents. Highest ticket of a type and id wins, an empty payload deletes it."""
    docs = {}
    state = {}                  # uuid -> {(type, id): (ticket, payload or None)}
    deleted = set()
    current = None
    count = 0
    for number, raw in enumerate(text.split('\n'), 1):
        line = raw.strip()
        if not line:
            continue
        count += 1
        if count > MAX_RECORDS:
            raise EasyEDAProParseError('EasyEDA Pro: the %s has more than %d records' % (label, MAX_RECORDS))
        if len(line) > MAX_LINE_CHARS:
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is too long' % (number, label))
        cut = line.find('}||')
        if not line.startswith('{') or cut < 0:
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is not a "header||payload" record' % (number, label))
        header_text = line[:cut + 1]
        match = re.match(r'\{\s*"type"\s*:\s*"([A-Z_0-9]{1,32})"', header_text)
        kind = match.group(1) if match else ''
        if kind != 'DOCHEAD' and kind != 'DELETE_DOC' and (current is None or kind not in KEPT):
            continue
        body = line[cut + 3:]
        if body.endswith('|'):
            body = body[:-1]
        try:
            header = json.loads(header_text)
            payload = json.loads(body) if body.strip() not in ('', '""') else None
        except (ValueError, RecursionError):
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s is not valid JSON' % (number, label)) from None
        if not isinstance(header, dict):
            raise EasyEDAProParseError('EasyEDA Pro: line %d of the %s has no header' % (number, label))
        if kind == 'DOCHEAD':
            if not isinstance(payload, dict) or not isinstance(payload.get('docType'), str):
                raise EasyEDAProParseError('EasyEDA Pro: a DOCHEAD record has no document type')
            current = (payload['docType'], _text(payload.get('uuid')))
            state.setdefault(current, {})
            continue
        if kind == 'DELETE_DOC':
            if current is not None and isinstance(payload, dict) and payload.get('isDelete') is True:
                deleted.add(current)
            continue
        if payload is not None and not isinstance(payload, dict):
            raise EasyEDAProParseError('EasyEDA Pro: a %s payload is not an object' % kind)
        key = (kind, _text(header.get('id')))
        ticket = header.get('ticket') if isinstance(header.get('ticket'), (int, float)) else 0
        bucket = state[current]
        prior = bucket.get(key)
        if prior is None or ticket >= prior[0]:
            bucket[key] = (ticket, payload)
    for ident, bucket in state.items():
        if ident in deleted:
            continue
        doc = _Doc(ident[0], ident[1])
        for (kind, rid), (_ticket, payload) in bucket.items():
            if payload is None:
                continue
            record = dict(payload)
            if kind == 'LAYER' and 'layerId' not in record:
                record['layerId'] = _json_id(rid, 1)
            elif kind == 'POURED':
                record['parent'] = _json_id(rid, 1)
            elif kind == 'META':
                doc.title = _text(record.get('title'))
            elif kind == 'RULE':
                parts = _json_id(rid, None)
                record = {'rule': parts[2] if isinstance(parts, list) and len(parts) > 2 else '',
                          'state': record.get('ruleState'), 'values': record.get('ruleContext')}
            record.setdefault('id', rid)
            doc.add(kind, record)
        docs[ident[1]] = doc
        docs[ident[1]].doc_type = ident[0]
    return docs


def _json_id(text, index):
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if index is None:
        return value
    return value[index] if isinstance(value, list) and len(value) > index else None


# --------------------------------------------------------------------------------------------------------------------
# the project
# --------------------------------------------------------------------------------------------------------------------

class _Project:
    """The PCB documents of a file and a lookup of their footprints."""

    def __init__(self, path):
        self.package = _Package(path)
        self.v3 = False
        self.pcbs = {}               # id -> (title, _Doc)
        self.footprints = {}         # uuid -> _Doc (already read) or callable
        self._foot_entries = {}
        self._pour_docs = {}
        self.devices = {}
        try:
            self._open()
        except Exception:
            self.package.close()
            raise

    def _open(self):
        package = self.package
        if package.zip is None:
            head = package.bare[:4096].decode('utf-8', 'ignore').lstrip('﻿ \t\r\n')
            try:
                text = package.bare.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise EasyEDAProParseError('EasyEDA Pro: the file is not UTF-8 text') from None
            if head.startswith('{') and '"DOCHEAD"' in head[:200]:
                self.v3 = True
                self._add_v3(_read_v3(text, 'document'))
            elif re.match(r'\[\s*"DOCTYPE"\s*,\s*"PCB"', head):
                doc = _read_v2(text, 'PCB document')
                self.pcbs['pcb'] = ('', doc)
            elif re.match(r'\[\s*"DOCTYPE"', head):
                raise EasyEDAProParseError('EasyEDA Pro: the document is not a PCB')
            else:
                raise EasyEDAProParseError('EasyEDA Pro: the file is not an EasyEDA Pro document')
            return
        names = package.names()
        lower = {name.lower(): name for name in names}
        if 'project2.json' in lower or any(name.lower().endswith('.epru') for name in names):
            self.v3 = True
            logs = [name for name in names if name.lower().endswith('.epru')]
            if not logs:
                raise EasyEDAProParseError('EasyEDA Pro: the V3 project has no .epru document')
            for name in sorted(logs):
                self._add_v3(_read_v3(package.read_text(name), name))
            return
        if 'project.json' in lower or any(re.match(r'^pcb/[^/]+$', name, re.I) for name in names):
            self._open_v2(names, lower)
            return
        raise EasyEDAProParseError('EasyEDA Pro: the archive holds no EasyEDA Pro project (no project.json or project2.json)')

    def _add_v3(self, docs):
        for uuid, doc in docs.items():
            if doc.doc_type == 'PCB':
                if doc.content():
                    self.pcbs[uuid] = (doc.title, doc)
                else:
                    self.pcbs.setdefault('~' + uuid, (doc.title, doc))
            elif doc.doc_type == 'FOOTPRINT':
                self.footprints[uuid] = doc
        for key in [k for k in self.pcbs if k.startswith('~')]:
            if any(not k.startswith('~') for k in self.pcbs):
                del self.pcbs[key]

    def _open_v2(self, names, lower):
        package = self.package
        titles = {}
        if 'project.json' in lower:
            try:
                project = json.loads(package.read_text(lower['project.json'], 32 << 20))
            except (ValueError, RecursionError):
                raise EasyEDAProParseError('EasyEDA Pro: project.json is not valid JSON') from None
            if isinstance(project, dict):
                if isinstance(project.get('pcbs'), dict):
                    titles = {_text(k).lower(): _text(v) for k, v in project['pcbs'].items()}
                if isinstance(project.get('devices'), dict):
                    for uuid, value in project['devices'].items():
                        attributes = value.get('attributes') if isinstance(value, dict) else None
                        if isinstance(attributes, dict):
                            self.devices[_text(uuid)] = _text(attributes.get('Footprint') or attributes.get('footprint_uuid'))
        for name in names:
            match = re.match(r'^footprint/([^/]+?)(\.[^./]*)?$', name, re.I)
            if match:
                self._foot_entries[match.group(1).lower()] = name
            match = re.match(r'^pour/([^/]+?)(\.[^./]*)?$', name, re.I)
            if match:
                self._pour_docs[self._pour_key(match.group(1))] = name
        order = list(titles)
        pcb_names = [name for name in names if re.match(r'^pcb/[^/]+$', name, re.I)]
        pcb_names.sort(key=lambda n: (order.index(self._base(n).lower()) if self._base(n).lower() in order else len(order), n))
        for name in pcb_names:
            data = package.read(name)
            if len(data) < 16:
                continue
            uuid = self._base(name)
            doc = _read_v2(data.decode('utf-8-sig'), 'PCB document %s' % uuid[:12])
            if doc.doc_type != 'PCB':
                continue
            self._attach_pours(doc, uuid)
            if doc.content():
                self.pcbs[uuid] = (titles.get(uuid.lower(), ''), doc)
            else:
                self.pcbs.setdefault('~' + uuid, (titles.get(uuid.lower(), ''), doc))
        for key in [k for k in self.pcbs if k.startswith('~')]:
            if any(not k.startswith('~') for k in self.pcbs):
                del self.pcbs[key]

    @staticmethod
    def _base(name):
        leaf = name.rsplit('/', 1)[-1]
        return leaf.rsplit('.', 1)[0] if '.' in leaf else leaf

    @staticmethod
    def _pour_key(stem):
        """File names of the POUR folder are the PCB id and a suffix, also stored as base64 text."""
        for candidate in (stem,):
            if re.match(r'^[0-9A-Za-z]{8,64}_[0-9A-Za-z]+$', candidate):
                return candidate.lower()
        try:
            return base64.b64decode(stem + '=' * (-len(stem) % 4), validate=True).decode('ascii').lower()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return stem.lower()

    def _attach_pours(self, doc, uuid):
        prefix = uuid.lower() + '_'
        for key, name in self._pour_docs.items():
            if key.startswith(prefix):
                text = self.package.read_text(name)
                poured = _read_v2(text, 'pour result %s' % key[:24], wanted=frozenset(['POURED']))
                for record in poured.get('POURED'):
                    doc.add('POURED', record)

    def footprint(self, uuid):
        if not uuid:
            return None
        key = uuid.lower()
        found = self.footprints.get(uuid)
        if found is not None:
            return found
        entry = self._foot_entries.get(key)
        if entry is None:
            footprint_id = self.devices.get(uuid)
            if footprint_id:
                return self.footprint(footprint_id) if footprint_id != uuid else None
            return None
        doc = _read_v2(self.package.read_text(entry), 'footprint %s' % uuid[:12])
        self.footprints[uuid] = doc
        return doc

    def close(self):
        self.package.close()


# --------------------------------------------------------------------------------------------------------------------
# geometry parser
# --------------------------------------------------------------------------------------------------------------------

class EasyEDAProParser:
    """``parse_file(path, pcb=None)`` -> self (see the module docstring for the result)."""

    source_kind = 'easyeda_pro'

    def __init__(self, app=None, circle_steps=None):
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        self._reset()

    def _reset(self):
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []
        self.default_drill_groups = None
        self.warnings = []
        self.board_outline = Polygon()
        self.board_profile = self.board_outline
        self.pcbs = []
        self.default_pcb = None
        self.pcb = None
        self.name = ''
        self.version = ''
        self.record_counts = {}
        self._solids = {}
        self._follows = {}
        self._points = 0
        self._notes = {}
        self._outline_lines = []
        self._doc = None

    # ------------------------------------------------------------------ public

    def parse_file(self, filename, pcb=None):
        self._reset()
        project = _Project(filename)
        try:
            ids = [key for key in project.pcbs if not key.startswith('~')]
            if not ids:
                if project.pcbs:
                    raise EasyEDAProParseError('EasyEDA Pro: the PCB of the project is empty (no components, tracks or pours)')
                raise EasyEDAProParseError('EasyEDA Pro: the project has no PCB document')
            labels = {}
            seen = {}
            for key in ids:
                title = project.pcbs[key][0] or ('PCB %s' % key[:8])
                seen[title] = seen.get(title, 0) + 1
                labels[key] = title if seen[title] == 1 else '%s (%d)' % (title, seen[title])
            self.pcbs = [labels[key] for key in ids]
            self.default_pcb = self.pcbs[0]
            self.version = 'V3' if project.v3 else 'V2'
            if pcb is None:
                if len(ids) > 1:
                    return self                  # metadata result: the app asks for the PCB
                chosen = ids[0]
            else:
                chosen = next((key for key in ids if labels[key].lower() == str(pcb).lower() or key.lower() == str(pcb).lower()),
                              None)
                if chosen is None:
                    raise EasyEDAProParseError('EasyEDA Pro: no PCB %r in the project' % str(pcb)[:60])
            self.pcb = labels[chosen]
            self.name = project.pcbs[chosen][0] or ''
            self._build(project, project.pcbs[chosen][1])
        finally:
            project.close()
        return self

    # ------------------------------------------------------------------ small helpers

    def _warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def _note(self, key, text, amount=1):
        entry = self._notes.setdefault(key, [text, 0])
        entry[1] += amount

    def _steps(self, radius):
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _segs(self, radius):
        return circle_quad_segs(self._steps(radius))

    def _take(self, count):
        self._points += count
        if self._points > MAX_POINTS:
            raise EasyEDAProParseError('EasyEDA Pro: the board needs more than %d sampled arc points' % MAX_POINTS)

    def _circle(self, cx, cy, radius):
        if radius <= 0:
            return Polygon()
        self._take(self._steps(radius) + 1)
        return Point(cx, cy).buffer(radius, quad_segs=self._segs(radius))

    # ------------------------------------------------------------------ paths

    def _arc_points(self, start, end, degrees):
        """Points from start to end of the arc that sweeps ``degrees`` (positive: counter-clockwise, Y up), mm."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        chord = math.hypot(dx, dy)
        half = math.radians(degrees) / 2
        if chord == 0 or abs(math.sin(half)) < 1e-9:
            if chord == 0 and abs(degrees) > 1e-9:
                raise EasyEDAProParseError('EasyEDA Pro: an arc with equal ends and a sweep of %g degrees' % degrees)
            return [start, end]
        radius = chord / (2 * abs(math.sin(half)))
        offset = chord / 2 / math.tan(half)
        cx = (start[0] + end[0]) / 2 - dy / chord * offset
        cy = (start[1] + end[1]) / 2 + dx / chord * offset
        first = math.atan2(start[1] - cy, start[0] - cx)
        sweep = math.radians(degrees)
        count = max(2, math.ceil(self._steps(radius) * abs(degrees) / 360))
        self._take(count + 1)
        points = [start]
        for step in range(1, count):
            angle = first + sweep * step / count
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        points.append(end)
        return points

    def _bezier(self, p0, p1, p2, p3):
        length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
        count = max(4, min(64, math.ceil(self._steps(max(length, 1e-6)) / 4)))
        self._take(count)
        points = []
        for step in range(1, count + 1):
            t = step / count
            u = 1 - t
            points.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                           u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
        return points

    def _single(self, path, unit):
        """One single polygon (modes L, ARC/CARC, C, R, CIRCLE) -> (points in mm, closed shape)."""
        if not isinstance(path, list) or not path:
            raise EasyEDAProParseError('EasyEDA Pro: a polygon is empty or malformed')
        if len(path) > MAX_PATH_TOKENS:
            raise EasyEDAProParseError('EasyEDA Pro: a polygon has more than %d elements' % MAX_PATH_TOKENS)
        head = path[0]
        if head == 'R':
            values = [_number(v, 'rectangle value') for v in path[1:]]
            if len(values) < 4 or len(values) > 7:
                raise EasyEDAProParseError('EasyEDA Pro: a rectangle polygon has %d values' % len(values))
            x, y, w, h = values[:4]
            rotation = values[4] if len(values) > 4 else 0.0
            radius = values[6] if len(values) == 7 else (values[5] if len(values) == 6 else 0.0)
            if rotation % 360:
                raise EasyEDAProParseError('EasyEDA Pro: a rotated rectangle polygon is not supported')
            x, y, w, h, radius = x * unit, y * unit, abs(w) * unit, abs(h) * unit, abs(radius) * unit
            radius = min(radius, w / 2, h / 2)
            if radius <= 0:
                return [(x, y), (x + w, y), (x + w, y - h), (x, y - h), (x, y)], True
            return self._rounded_rect(x, y - h, x + w, y, radius), True
        if head == 'CIRCLE':
            cx, cy, r = (_number(path[i], 'circle value') * unit for i in (1, 2, 3)) if len(path) >= 4 else (None,) * 3
            if cx is None:
                raise EasyEDAProParseError('EasyEDA Pro: a circle polygon is malformed')
            polygon = self._circle(cx, cy, abs(r))
            return list(polygon.exterior.coords), True
        if isinstance(head, str) or len(path) < 2:
            raise EasyEDAProParseError('EasyEDA Pro: a polygon does not start with a coordinate pair')
        points = [(_number(path[0], 'polygon x') * unit, _number(path[1], 'polygon y') * unit)]
        mode = 'L'
        i = 2
        n = len(path)

        def take(count):
            nonlocal i
            if i + count > n:
                raise EasyEDAProParseError('EasyEDA Pro: a polygon is cut short')
            values = path[i:i + count]
            i += count
            return [_number(v, 'polygon value') for v in values]

        while i < n:
            token = path[i]
            if isinstance(token, str):
                mode = token.upper()
                i += 1
                if mode not in ('L', 'ARC', 'CARC', 'C'):
                    raise EasyEDAProParseError('EasyEDA Pro: a polygon uses the unsupported mode %r' % token[:12])
                continue
            if mode == 'L':
                x, y = take(2)
                points.append((x * unit, y * unit))
            elif mode in ('ARC', 'CARC'):
                angle, x, y = take(3)
                points.extend(self._arc_points(points[-1], (x * unit, y * unit), angle)[1:])
            else:
                x2, y2, x3, y3, x4, y4 = take(6)
                points.extend(self._bezier(points[-1], (x2 * unit, y2 * unit), (x3 * unit, y3 * unit), (x4 * unit, y4 * unit)))
        return points, False

    def _rounded_rect(self, x0, y0, x1, y1, radius):
        count = max(2, math.ceil(self._steps(radius) / 4))
        self._take(4 * (count + 1))
        points = []
        for cx, cy, a0 in ((x1 - radius, y1 - radius, 0), (x0 + radius, y1 - radius, 90),
                           (x0 + radius, y0 + radius, 180), (x1 - radius, y0 + radius, 270)):
            for step in range(count + 1):
                angle = math.radians(a0 + 90 * step / count)
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        points.append(points[0])
        return points

    def _paths(self, value, unit):
        """A single or a complex polygon -> [(points, closed)] in mm."""
        if not isinstance(value, list):
            raise EasyEDAProParseError('EasyEDA Pro: a polygon is not a list')
        if not value:
            return []
        if isinstance(value[0], list):
            return [self._single(single, unit) for single in value]
        return [self._single(value, unit)]

    @staticmethod
    def _valid(geo):
        if geo.is_valid:
            return geo
        fixed = geo.buffer(0)
        if not fixed.is_valid:
            raise EasyEDAProParseError('EasyEDA Pro: invalid polygon topology')
        return fixed

    def _area(self, paths):
        """The filled area of a complex polygon: a ring inside an odd number of other rings is a hole."""
        rings = []
        for points, _closed in paths:
            if len(points) < 3:
                continue
            ring = Polygon(points)
            if ring.is_empty or ring.area < 1e-12:
                continue
            rings.append(self._valid(ring))
        if len(rings) <= 1:
            return unary_union(rings) if rings else Polygon()
        order = sorted(range(len(rings)), key=lambda k: -rings[k].area)
        ranked = [rings[k] for k in order]
        tree = STRtree(ranked)
        probes = [ring.representative_point() for ring in ranked]
        shells = {}
        parent = []
        for index, probe in enumerate(probes):
            container = [j for j in tree.query(probe, predicate='within') if j < index and ranked[j].area > ranked[index].area]
            container.sort()
            parent.append(container[-1] if container else None)
        depth = []
        for index in range(len(ranked)):
            d, cursor = 0, parent[index]
            while cursor is not None:
                d += 1
                cursor = parent[cursor]
            depth.append(d)
        for index, ring in enumerate(ranked):
            if depth[index] % 2 == 0:
                shells[index] = [ring.exterior.coords, []]
        for index, ring in enumerate(ranked):
            if depth[index] % 2 == 1 and parent[index] in shells:
                shells[parent[index]][1].append(ring.exterior.coords)
        polygons = []
        for coords, holes in shells.values():
            polygons.append(self._valid(Polygon(coords, holes)))
        return unary_union(polygons)

    def _stroke(self, points, width):
        """A round-capped stroke of a polyline, width in mm."""
        if len(points) < 2 or width <= 0:
            return Polygon(), None
        line = LineString(points)
        if line.length == 0:
            return self._circle(points[0][0], points[0][1], width / 2), None
        return line.buffer(width / 2, quad_segs=self._segs(width / 2)), line

    # ------------------------------------------------------------------ layers

    def _setup_layers(self, doc):
        table = {}
        for record in doc.get('LAYER'):
            layer_id = int(_number(record.get('layerId'), 'layer id'))
            table[layer_id] = {'type': _text(record.get('layerType')).upper(), 'name': _text(record.get('layerName')),
                               'use': bool(record.get('use', True))}
        if not table:
            table = {k: {'type': v, 'name': DEFAULT_LAYER_NAMES.get(v, v), 'use': True} for k, v in DEFAULT_LAYER_TYPES.items()}
        self._layer_table = table
        copper = [k for k, v in table.items() if v['type'] in COPPER_TYPES and v['use']]
        top = next((k for k in copper if table[k]['type'] == 'TOP'), None)
        bottom = next((k for k in copper if table[k]['type'] == 'BOTTOM'), None)
        if top is None or bottom is None:
            raise EasyEDAProParseError('EasyEDA Pro: the PCB has no top and bottom copper layer')
        inner = [k for k in copper if table[k]['type'] == 'SIGNAL']
        self.copper_ids = [top] + inner + [bottom]
        planes = [k for k, v in table.items() if v['type'] == 'PLANE' and v['use']]
        if planes:
            raise EasyEDAProParseError('EasyEDA Pro: the layer %r is an internal plane, which is not supported'
                                       % table[planes[0]]['name'])
        self._keys = {}
        used = set()
        for layer_id, entry in table.items():
            name = entry['name'] or DEFAULT_LAYER_NAMES.get(entry['type'], 'Layer %d' % layer_id)
            if name in used:
                name = '%s (%d)' % (name, layer_id)
            used.add(name)
            self._keys[layer_id] = name
        self._type_ids = {}
        for layer_id, entry in table.items():
            if entry['type'] not in ('SIGNAL',):
                self._type_ids.setdefault(entry['type'], layer_id)

    def _layer_type(self, layer_id, table=None):
        table = self._layer_table if table is None else table
        try:
            number = int(layer_id)
        except (TypeError, ValueError):
            raise EasyEDAProParseError('EasyEDA Pro: a primitive has an invalid layer') from None
        entry = table.get(number)
        if entry is not None:
            return entry['type']
        return DEFAULT_LAYER_TYPES.get(number, '')

    def _layer_id_of(self, layer_type):
        return self._type_ids.get(layer_type)

    def _targets(self, layer_type, inner=True):
        """The ids of the PCB layers that a primitive of this layer type draws on."""
        if layer_type == 'MULTI':
            return list(self.copper_ids) if inner else [self.copper_ids[0], self.copper_ids[-1]]
        if layer_type in ('TOP', 'BOTTOM'):
            return [self._type_ids[layer_type]]
        if layer_type in OUTPUT_TYPES or layer_type == 'OUTLINE':
            found = self._type_ids.get(layer_type)
            return [found] if found is not None else []
        return []

    def _add(self, layer_id, geo, follow=None):
        if geo is None or geo.is_empty:
            return
        key = self._keys[layer_id]
        self._solids.setdefault(key, []).append(geo)
        if follow is not None:
            self._follows.setdefault(key, []).extend(follow if isinstance(follow, list) else [follow])
        self._layer_ids_used.add(layer_id)

    # ------------------------------------------------------------------ rules

    def _setup_rules(self, doc):
        mask = {'padTop': None, 'padBot': None, 'viaTop': None, 'viaBot': None, 'tpTop': None, 'tpBot': None}
        paste = {'padTop': None, 'padBot': None, 'tpTop': None, 'tpBot': None}
        for record in doc.get('RULE'):
            if record.get('rule') not in ('solderMaskExpansion', 'pasteMaskExpansion'):
                continue
            state = record.get('state')
            if state not in (1, 'DEFAULT', 'default'):
                continue
            values = record.get('values')
            target = mask if record['rule'] == 'solderMaskExpansion' else paste
            if isinstance(values, dict):
                mapping = {'padTop': 'padTopExpan', 'padBot': 'padBotExpan', 'viaTop': 'viaTopExpan', 'viaBot': 'viaBotExpan',
                           'tpTop': 'testPointTopExpan', 'tpBot': 'testPointBotExpan'}
                for short, long_ in mapping.items():
                    if short in target and values.get(long_) is not None:
                        target[short] = _number(values[long_], 'mask expansion')
            elif isinstance(values, list):
                numbers = [v for v in values[1:] if isinstance(v, (int, float))]
                order = ['padTop', 'padBot', 'viaTop', 'viaBot', 'tpTop', 'tpBot'] if target is mask else \
                    ['padTop', 'padBot', 'tpTop', 'tpBot']
                for short, number in zip(order, numbers):
                    target[short] = _number(number, 'mask expansion')
        self._mask_rule = mask
        self._paste_rule = paste
        if mask['padTop'] is None:
            self._warn('The design rules have no solder mask expansion: pad openings use 0 and vias are covered.')
        for short in mask:
            if mask[short] is None:
                mask[short] = -1000.0 if short.startswith('via') else 0.0
        for short in paste:
            if paste[short] is None:
                paste[short] = 0.0

    # ------------------------------------------------------------------ the board

    def _build(self, project, doc):
        self._layer_ids_used = set()
        self._setup_layers(doc)
        self._setup_rules(doc)
        self._project = project
        for kind, items in doc.recs.items():
            if kind.startswith('?'):
                self._note('unknown:' + kind, 'Records of the unknown type %s were ignored.' % kind[1:], len(items))
        for kind in ('FPC_FILL',):
            if doc.get(kind):
                raise EasyEDAProParseError('EasyEDA Pro: the PCB has %s records (flex copper), which are not supported' % kind)
        identity = _Placement()
        attrs = {}
        for record in doc.get('ATTR'):
            attrs.setdefault(_text(record.get('parentId')), {}).setdefault(_text(record.get('key')), record)
            if record.get('valueVisible') and record.get('x') is not None and \
                    self._layer_type(record.get('layerId')) in ('TOP_SILK', 'BOT_SILK'):
                self._note('text', 'Text is not stored as polygons and was skipped (%s).', 1)
        self._board_primitives(doc, identity, self._layer_table, False, 'the board')
        # parts
        pad_ids = {}
        parts = doc.get('COMPONENT')
        if len(parts) > MAX_PADS:
            raise EasyEDAProParseError('EasyEDA Pro: the board has more than %d parts' % MAX_PADS)
        cache = {}
        total_pads = 0
        for comp in parts:
            comp_id = _text(comp.get('id'))
            entries = attrs.get(comp_id, {})
            designator = _text(entries.get('Designator', {}).get('value')) or comp_id
            uuid = _text(entries.get('Footprint', {}).get('value'))
            if not uuid:
                device = _text(entries.get('Device', {}).get('value'))
                uuid = project.devices.get(device, '') if device else ''
            foot = project.footprint(uuid)
            if foot is None:
                raise EasyEDAProParseError(
                    'EasyEDA Pro: part %s has no footprint in the file (%s); open the project (.epro / .epro2), not a bare '
                    'PCB document, so that the footprint pads are included' % (designator, uuid[:40] or 'no footprint reference'))
            if uuid not in cache:
                cache[uuid] = self._footprint(foot, uuid)
            info = cache[uuid]
            total_pads += len(info['pads'])
            if total_pads > MAX_PADS:
                raise EasyEDAProParseError('EasyEDA Pro: the board expands to more than %d pads' % MAX_PADS)
            layer_type = self._layer_type(comp.get('layerId'))
            if layer_type not in ('TOP', 'BOTTOM'):
                raise EasyEDAProParseError('EasyEDA Pro: part %s is on the layer type %r, not on the top or bottom side'
                                           % (designator, layer_type))
            bottom = layer_type == 'BOTTOM'
            place = _Placement(_number(comp.get('x'), 'part x') * MIL_MM, _number(comp.get('y'), 'part y') * MIL_MM,
                               _opt(comp.get('angle'), 0.0, 'part angle'), bottom)
            for pad in info['pads']:
                pad_ids[comp_id + pad['id']] = (designator, pad)
                self._emit_pad(pad, place)
            self._board_primitives(foot, place, info['table'], bottom, 'the footprint of %s' % designator)
            if info['texts']:
                self._note('text', 'Text is not stored as polygons and was skipped (%s).', info['texts'])
        # pads of the board itself, and the overrides of footprint pads
        for record in doc.get('PAD'):
            rid = _text(record.get('id'))
            if rid in pad_ids:
                designator, pad = pad_ids[rid]
                signature = self._pad_signature(record, self._layer_table)
                if signature != pad['signature']:
                    raise EasyEDAProParseError('EasyEDA Pro: the pad %s of %s is changed in the PCB (a footprint pad override '
                                               'with other geometry), which is not supported' % (pad['num'], designator))
                continue
            self._emit_pad(self._pad_local(record, self._layer_table, rid), identity)
        self._pours(doc)
        self._finish()

    # ------------------------------------------------------------------ placement of footprints

    def _footprint(self, foot, uuid):
        table = {}
        for record in foot.get('LAYER'):
            table[int(_number(record.get('layerId'), 'layer id'))] = {'type': _text(record.get('layerType')).upper()}
        if not table:
            table = {k: {'type': v} for k, v in DEFAULT_LAYER_TYPES.items()}
        pads = [self._pad_local(record, table, _text(record.get('id'))) for record in foot.get('PAD')]
        texts = len(foot.get('STRING'))
        for record in foot.get('ATTR'):
            if record.get('valueVisible') and record.get('x') is not None and \
                    self._layer_type(record.get('layerId'), table) in ('TOP_SILK', 'BOT_SILK'):
                texts += 1
        return {'pads': pads, 'table': table, 'texts': texts}

    # ------------------------------------------------------------------ pads

    def _pad_signature(self, record, table):
        def clean(value):
            if isinstance(value, float):
                return round(value, 4)
            if isinstance(value, dict):
                return {k: clean(v) for k, v in sorted(value.items())}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value
        keys = ('centerX', 'centerY', 'padAngle', 'hole', 'defaultPad', 'specialPad', 'padOffsetX', 'padOffsetY',
                'relativeAngle')
        data = {k: clean(record.get(k)) for k in keys}
        data['layer'] = self._layer_type(record.get('layerId'), table)
        data['plated'] = bool(record.get('plated'))
        return json.dumps(data, sort_keys=True)

    def _shape_geo(self, shape, what):
        """The pad shape at the origin, unrotated, mm."""
        if not isinstance(shape, dict):
            raise EasyEDAProParseError('EasyEDA Pro: %s has no shape definition' % what)
        kind = _text(shape.get('padType')).upper()
        if kind in ('ELLIPSE', 'ROUND', 'OVAL', 'RECT'):
            w = abs(_number(shape.get('width'), what + ' width')) * MIL_MM
            h = abs(_number(shape.get('height', shape.get('width')), what + ' height')) * MIL_MM
            if w == 0 or h == 0:
                return Polygon()
            if kind in ('ELLIPSE', 'ROUND'):
                if abs(w - h) < 1e-9:
                    return self._circle(0, 0, w / 2)
                from shapely.affinity import scale as shapely_scale
                return shapely_scale(self._circle(0, 0, 0.5), w, h, origin=(0, 0))
            if kind == 'OVAL':
                diameter, length = min(w, h), max(w, h)
                if length - diameter < 1e-9:
                    return self._circle(0, 0, diameter / 2)
                half = (length - diameter) / 2
                self._take(self._steps(diameter / 2) + 2)
                line = LineString([(-half, 0), (half, 0)] if w >= h else [(0, -half), (0, half)])
                return line.buffer(diameter / 2, quad_segs=self._segs(diameter / 2))
            radius = min(abs(_opt(shape.get('radius'), 0.0, what + ' radius')) * MIL_MM, w / 2, h / 2)
            if radius <= 0:
                return shapely_box(-w / 2, -h / 2, w / 2, h / 2)
            return Polygon(self._rounded_rect(-w / 2, -h / 2, w / 2, h / 2, radius))
        if kind in ('POLYGON', 'POLY'):
            geo = self._area(self._paths(shape.get('path'), MIL_MM))
            if geo.is_empty:
                raise EasyEDAProParseError('EasyEDA Pro: %s has an empty polygon' % what)
            return geo
        raise EasyEDAProParseError('EasyEDA Pro: %s has the unsupported shape %r' % (what, kind[:20] or 'none'))

    def _pad_local(self, record, table, rid):
        """A pad in its footprint (or board) coordinates: copper, mask and paste shapes and the hole, mm."""
        number = _text(record.get('num'))
        what = 'pad %s' % (number or rid)
        layer = self._layer_type(record.get('layerId'), table)
        if layer not in ('TOP', 'BOTTOM', 'MULTI'):
            raise EasyEDAProParseError('EasyEDA Pro: %s is on the layer type %r; only top, bottom and multi-layer pads exist'
                                       % (what, layer or 'unknown'))
        if record.get('specialPad'):
            raise EasyEDAProParseError('EasyEDA Pro: %s has layer specific pad shapes (special pad), which are not supported'
                                       % what)
        cx = _number(record.get('centerX'), what + ' x') * MIL_MM
        cy = _number(record.get('centerY'), what + ' y') * MIL_MM
        angle = _opt(record.get('padAngle'), 0.0, what + ' angle')
        shape = self._shape_geo(record.get('defaultPad'), what)
        place = _Placement(cx, cy, angle)
        shape_def = record['defaultPad']
        if _text(shape_def.get('padType')).upper() in ('POLYGON', 'POLY'):
            # the polygon of a pad is in footprint coordinates (measured against Gerber files, see TRACE easyeda-pro.ts)
            copper = shapely_rotate(shape, angle, origin=(cx, cy)) if angle % 360 else shape
        else:
            copper = place.geo(shape) if not shape.is_empty else shape
        sharp = _text(shape_def.get('padType')).upper() == 'RECT' and not _opt(shape_def.get('radius'), 0.0) > 0
        kind = _text(record.get('padType')).upper()
        test = kind in ('1', 'TEST_POINT', 'TESTPOINT', 'TEST')
        side = ('tp' if test else 'pad')
        mask = {}
        paste = {}
        for name, key, rule in (('Top', 'topSolderExpansion', self._mask_rule[side + 'Top']),
                                ('Bot', 'bottomSolderExpansion', self._mask_rule[side + 'Bot'])):
            if (name == 'Top' and layer == 'BOTTOM') or (name == 'Bot' and layer == 'TOP') or copper.is_empty:
                continue
            expansion = _number(record[key], what + ' mask expansion') if record.get(key) is not None else rule
            mask[name] = self._grow(copper, expansion * MIL_MM, round_=sharp is False)
        if layer in ('TOP', 'BOTTOM') and not copper.is_empty:
            name = 'Top' if layer == 'TOP' else 'Bot'
            key = 'topPasteExpansion' if layer == 'TOP' else 'bottomPasteExpansion'
            rule = self._paste_rule[side + name]
            expansion = _number(record[key], what + ' paste expansion') if record.get(key) is not None else rule
            paste[name] = self._grow(copper, expansion * MIL_MM, round_=sharp is False)
        hole = None
        spec = record.get('hole')
        if spec:
            hole_type = _text(spec.get('holeType')).upper()
            hw = abs(_number(spec.get('width'), what + ' hole width')) * MIL_MM
            hh = abs(_number(spec.get('height', spec.get('width')), what + ' hole height')) * MIL_MM
            if hole_type not in ('ROUND', 'SLOT'):
                raise EasyEDAProParseError('EasyEDA Pro: %s has the unsupported hole shape %r' % (what, hole_type[:20]))
            if hw > 0 and hh > 0:
                diameter, length = min(hw, hh), max(hw, hh)
                offset = _Placement(0, 0, angle).pt((_opt(record.get('padOffsetX'), 0.0) * MIL_MM,
                                                     _opt(record.get('padOffsetY'), 0.0) * MIL_MM))
                centre = (cx + offset[0], cy + offset[1])
                axis = (0.0 if hw >= hh else 90.0) + _opt(record.get('relativeAngle'), 0.0) + angle
                half = (length - diameter) / 2
                ends = None
                if half > 1e-9:
                    ends = ((centre[0] - half * math.cos(math.radians(axis)), centre[1] - half * math.sin(math.radians(axis))),
                            (centre[0] + half * math.cos(math.radians(axis)), centre[1] + half * math.sin(math.radians(axis))))
                plated = record.get('plated')
                hole = {'centre': centre, 'diameter': diameter, 'ends': ends,
                        'plated': plated is None or plated not in (False, 0, '0', 'false')}
        return {'id': rid, 'num': number, 'layer': layer, 'copper': copper, 'mask': mask, 'paste': paste, 'hole': hole,
                'unused': [int(v) for v in (record.get('unusedInnerLayers') or []) if isinstance(v, (int, float))],
                'signature': self._pad_signature(record, table)}

    def _grow(self, geo, distance, round_):
        if distance == 0:
            return geo
        if distance < 0:
            result = geo.buffer(distance, join_style='round' if round_ else 'mitre')
        else:
            result = geo.buffer(distance, join_style='round' if round_ else 'mitre', mitre_limit=2.0,
                                quad_segs=self._segs(max(distance, 0.05)))
        return result

    def _emit_pad(self, pad, place):
        flip = place.flip
        layer = FLIP_TYPE.get(pad['layer'], pad['layer']) if flip else pad['layer']
        if not pad['copper'].is_empty:
            geo = place.geo(pad['copper'])
            for layer_id in self._targets(layer):
                if layer_id in pad['unused'] and layer_id not in (self.copper_ids[0], self.copper_ids[-1]):
                    continue
                self._add(layer_id, geo)
        for name, geo in pad['mask'].items():
            side = ('TOP_SOLDER_MASK' if name == 'Top' else 'BOT_SOLDER_MASK')
            side = FLIP_TYPE.get(side, side) if flip else side
            if not geo.is_empty:
                for layer_id in self._targets(side):
                    self._add(layer_id, place.geo(geo))
        for name, geo in pad['paste'].items():
            side = ('TOP_PASTE_MASK' if name == 'Top' else 'BOT_PASTE_MASK')
            side = FLIP_TYPE.get(side, side) if flip else side
            if not geo.is_empty:
                for layer_id in self._targets(side):
                    self._add(layer_id, place.geo(geo))
        hole = pad['hole']
        if hole is not None:
            group = 'PTH_Through' if hole['plated'] else 'NPTH_Through'
            centre = place.pt(hole['centre'])
            ends = None if hole['ends'] is None else (place.pt(hole['ends'][0]), place.pt(hole['ends'][1]))
            self._drill(group, hole['diameter'], centre, ends)

    def _drill(self, group, diameter, centre, ends=None):
        diameter = round(diameter, 5)
        tools = self.drill_groups.setdefault(group, {})
        tool_id = next((key for key, tool in tools.items() if tool['tooldia'] == diameter), len(tools) + 1)
        tool = tools.setdefault(tool_id, {'tooldia': diameter, 'drills': [], 'slots': []})
        if ends is None:
            tool['drills'].append(Point(centre))
        else:
            tool['slots'].append((Point(ends[0]), Point(ends[1])))

    # ------------------------------------------------------------------ vias

    def _via(self, record, place):
        what = 'via %s' % _text(record.get('id'))
        if _text(record.get('ruleName')):
            raise EasyEDAProParseError('EasyEDA Pro: %s uses the layer rule %r (blind or buried via), which is not supported'
                                       % (what, _text(record.get('ruleName'))[:40]))
        x = _number(record.get('centerX'), what + ' x') * MIL_MM
        y = _number(record.get('centerY'), what + ' y') * MIL_MM
        hole = _number(record.get('holeDiameter'), what + ' hole') * MIL_MM
        diameter = _number(record.get('viaDiameter'), what + ' diameter') * MIL_MM
        if hole < 0 or diameter <= 0:
            raise EasyEDAProParseError('EasyEDA Pro: %s has no size' % what)
        centre = place.pt((x, y))
        unused = [int(v) for v in (record.get('unusedInnerLayers') or []) if isinstance(v, (int, float))]
        pad = self._circle(centre[0], centre[1], diameter / 2)
        for layer_id in self.copper_ids:
            if layer_id in unused and layer_id not in (self.copper_ids[0], self.copper_ids[-1]):
                continue
            self._add(layer_id, pad)
        for name, key, rule, mask_type in (('Top', 'topSolderExpansion', self._mask_rule['viaTop'], 'TOP_SOLDER_MASK'),
                                           ('Bot', 'bottomSolderExpansion', self._mask_rule['viaBot'], 'BOT_SOLDER_MASK')):
            expansion = _number(record[key], what + ' mask expansion') if record.get(key) is not None else rule
            radius = diameter / 2 + expansion * MIL_MM
            if radius > 0:
                side = FLIP_TYPE.get(mask_type, mask_type) if place.flip else mask_type
                for layer_id in self._targets(side):
                    self._add(layer_id, self._circle(centre[0], centre[1], radius))
        if hole > 0:
            self._drill('PTH_Through_Via', hole, centre)

    # ------------------------------------------------------------------ line work, fills, regions

    def _board_primitives(self, doc, place, table, flip, owner):
        for record in doc.get('VIA'):
            self._via(record, place)
        for kind in ('LINE', 'ARC', 'CARC', 'POLY', 'FILL', 'TEARDROP', 'REGION', 'IMAGE', 'OBJ', 'STRING', 'DIMENSION'):
            for record in doc.get(kind):
                layer = self._layer_type(record.get('layerId'), table)
                if flip:
                    layer = FLIP_TYPE.get(layer, layer)
                self._primitive(kind, record, layer, place, owner)

    def _primitive(self, kind, record, layer, place, owner):
        if kind == 'STRING':
            self._note('text', 'Text is not stored as polygons and was skipped (%s).', 1)
            return
        if kind == 'DIMENSION':
            self._note('dimension', 'Dimension drawings were skipped.', 1)
            return
        if layer in IGNORED_LAYER_TYPES or layer == '':
            return
        if layer in ('PLANE',):
            raise EasyEDAProParseError('EasyEDA Pro: %s has a primitive on an internal plane layer, which is not supported' % owner)
        if layer == 'MULTI':
            if kind == 'FILL':
                self._multi_fill(record, place)
            elif kind != 'REGION':
                self._note('multi', 'Lines and polygons on the multi-layer were skipped.', 1)
            return
        copper = layer in ('TOP', 'BOTTOM', 'MULTI')
        if kind in ('IMAGE', 'OBJ'):
            if copper or layer == 'OUTLINE':
                raise EasyEDAProParseError('EasyEDA Pro: %s has an embedded image on a copper or outline layer, which is not '
                                           'supported' % owner)
            self._note('image', 'Embedded images and logos were skipped.', 1)
            return
        if kind == 'REGION':
            if layer in ('TOP_SOLDER_MASK', 'BOT_SOLDER_MASK', 'TOP_PASTE_MASK', 'BOT_PASTE_MASK'):
                geo = self._area(self._paths(record.get('path'), MIL_MM))
                for layer_id in self._targets(layer):
                    self._add(layer_id, place.geo(geo))
            else:
                self._note('keepout', 'Keepout and other regions that are not copper were skipped.', 1)
            return
        if layer == 'OUTLINE':
            self._outline_item(kind, record, place)
            return
        if not (copper or layer in OUTPUT_TYPES):
            return
        width = abs(_opt(record.get('width'), 0.0, 'line width')) * MIL_MM
        solid, follow = None, None
        if kind == 'LINE':
            points = [(_number(record.get('startX'), 'line x') * MIL_MM, _number(record.get('startY'), 'line y') * MIL_MM),
                      (_number(record.get('endX'), 'line x') * MIL_MM, _number(record.get('endY'), 'line y') * MIL_MM)]
            solid, follow = self._stroke(points, width)
        elif kind in ('ARC', 'CARC'):
            start = (_number(record.get('startX'), 'arc x') * MIL_MM, _number(record.get('startY'), 'arc y') * MIL_MM)
            end = (_number(record.get('endX'), 'arc x') * MIL_MM, _number(record.get('endY'), 'arc y') * MIL_MM)
            solid, follow = self._stroke(self._arc_points(start, end, _number(record.get('angle'), 'arc angle')), width)
        elif kind == 'POLY':
            parts, lines = [], []
            for points, _closed in self._paths(record.get('path'), MIL_MM):
                geo, line = self._stroke(points, width)
                parts.append(geo)
                if line is not None:
                    lines.append(line)
            solid = unary_union(parts) if parts else Polygon()
            follow = lines
        elif kind in ('FILL', 'TEARDROP'):
            style = record.get('fillStyle', 0)
            if kind == 'FILL' and style not in (0, 'SOLID', 'solid', None):
                raise EasyEDAProParseError('EasyEDA Pro: %s has a %r fill (grid or internal plane), which is not supported'
                                           % (owner, style))
            solid = self._area(self._paths(record.get('path'), MIL_MM))
            if width > 0 and not solid.is_empty:
                solid = solid.buffer(width / 2, quad_segs=self._segs(max(width / 2, 0.01)))
        if solid is None or solid.is_empty:
            return
        solid = place.geo(solid)
        if isinstance(follow, list):
            follow = [place.geo(line) for line in follow]
        elif follow is not None:
            follow = [place.geo(follow)]
        for layer_id in self._targets(layer):
            self._add(layer_id, solid, follow)

    def _multi_fill(self, record, place):
        """A fill on the multi-layer is a hole of the board: a circle is a non-plated drill, any other shape a cutout."""
        raw = record.get('path')
        single = raw[0] if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], list) else raw
        if isinstance(single, list) and single and single[0] == 'CIRCLE':
            if len(single) < 4:
                raise EasyEDAProParseError('EasyEDA Pro: a multi-layer circle is malformed')
            centre = place.pt((_number(single[1], 'hole x') * MIL_MM, _number(single[2], 'hole y') * MIL_MM))
            diameter = 2 * abs(_number(single[3], 'hole radius')) * MIL_MM
            if diameter > 0:
                self._drill('NPTH_Through', diameter, centre)
            return
        geo = place.geo(self._area(self._paths(raw, MIL_MM)))
        if not geo.is_empty:
            for polygon in self._polygons_of(geo):
                self._outline_lines.append((LineString(polygon.exterior.coords), CUTOUT_WIDTH_MM))
                for ring in polygon.interiors:
                    self._outline_lines.append((LineString(ring.coords), CUTOUT_WIDTH_MM))
            self._note('cutout', 'Non-circular multi-layer fills were read as board cutouts.', 1)

    def _outline_item(self, kind, record, place):
        if kind == 'LINE':
            points = [(_number(record.get('startX'), 'line x') * MIL_MM, _number(record.get('startY'), 'line y') * MIL_MM),
                      (_number(record.get('endX'), 'line x') * MIL_MM, _number(record.get('endY'), 'line y') * MIL_MM)]
            self._outline_lines.append((LineString(points), abs(_opt(record.get('width'), 0.0)) * MIL_MM))
        elif kind in ('ARC', 'CARC'):
            start = (_number(record.get('startX'), 'arc x') * MIL_MM, _number(record.get('startY'), 'arc y') * MIL_MM)
            end = (_number(record.get('endX'), 'arc x') * MIL_MM, _number(record.get('endY'), 'arc y') * MIL_MM)
            points = self._arc_points(start, end, _number(record.get('angle'), 'arc angle'))
            self._outline_lines.append((LineString(points), abs(_opt(record.get('width'), 0.0)) * MIL_MM))
        elif kind == 'POLY':
            for points, _closed in self._paths(record.get('path'), MIL_MM):
                if len(points) >= 2:
                    self._outline_lines.append((LineString(points), abs(_opt(record.get('width'), 0.0)) * MIL_MM))
        else:
            self._note('outline', 'Fills on the board outline layer were ignored.', 1)

    # ------------------------------------------------------------------ pours

    def _pours(self, doc):
        poured = {}
        for record in doc.get('POURED'):
            poured.setdefault(_text(record.get('parent')), []).extend(record.get('pourFill') or [])
        for pour in doc.get('POUR'):
            pid = _text(pour.get('id'))
            layer_id = int(_number(pour.get('layerId'), 'pour layer'))
            label = '%s (net %s)' % (_text(pour.get('name')) or pid, _text(pour.get('netName')) or '-')
            if layer_id not in self.copper_ids:
                raise EasyEDAProParseError('EasyEDA Pro: the copper pour %s is on a layer that is not a copper layer' % label)
            if pid not in poured:
                raise EasyEDAProParseError(
                    'EasyEDA Pro: the copper pour %s has no stored fill. Rebuild the copper in EasyEDA Pro and save the project '
                    'again; the stored result is read, the pour is not recomputed' % label)
            edge = _opt(pour.get('width'), 0.0, 'pour width') * POUR_EDGE_MM / 2
            for item in poured[pid]:
                if not isinstance(item, dict):
                    raise EasyEDAProParseError('EasyEDA Pro: the stored fill of the copper pour %s is malformed' % label)
                paths = self._paths(item.get('path'), POURED_MM)
                stroke = _opt(item.get('strokeWidth'), 0.0, 'pour stroke') * STROKE_MM
                if item.get('fill') not in (False, 0, None, '0', 'false'):
                    geo = self._area(paths)
                    if edge > 0 and not geo.is_empty:
                        geo = geo.buffer(edge, quad_segs=self._segs(max(edge, 0.01)))
                    self._add(layer_id, geo)
                if stroke > 0:
                    for points, _closed in paths:
                        geo, line = self._stroke(points, stroke)
                        self._add(layer_id, geo)

    # ------------------------------------------------------------------ result

    def _outline(self):
        if not self._outline_lines:
            self._warn('The PCB has no board outline.')
            return
        lines = [shapely_set_precision(line, 1e-5) for line, _w in self._outline_lines]
        faces = list(polygonize(unary_union(lines)))
        if not faces:
            self._warn('The board outline does not form a closed contour; the outline was left empty.')
        else:
            kept = []
            for face in faces:
                probe = face.representative_point()
                if not any(other is not face and other.interiors and any(Polygon(ring).contains(probe)
                                                                         for ring in other.interiors) for other in faces):
                    kept.append(face)
            self.board_outline = unary_union(kept)
            self.board_profile = self.board_outline
        outline_id = self._type_ids.get('OUTLINE')
        if outline_id is not None:
            for line, width in self._outline_lines:
                solid, follow = self._stroke(list(line.coords), width or OUTLINE_WIDTH_MM)
                self._add(outline_id, solid, follow)

    def _finish(self):
        self._outline()
        for text, count in self._notes.values():
            self._warn(text % count if '%s' in text else '%s (%d)' % (text, count))
        order = list(self.copper_ids)
        for layer_type in OUTPUT_TYPES + ('OUTLINE',):
            if layer_type in self._type_ids:
                order.append(self._type_ids[layer_type])
        for layer_id in order:
            key = self._keys[layer_id]
            polygons = self._polygons_of(unary_union(self._solids.get(key, [])))
            if not polygons:
                continue
            # Enclosing polygons MUST precede islands, otherwise an enclosing hole erases the island.
            polygons.sort(key=lambda polygon: Polygon(polygon.exterior).area, reverse=True)
            self.layers[key] = {'name': key, 'solid_geometry': polygons, 'follow_geometry': list(self._follows.get(key, [])),
                                'tools': {0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': p} for p in polygons]}}}
        self.default_layers = [key for key in self.layers]
        self.default_drill_groups = None
        if not self.layers and not self.drill_groups:
            raise EasyEDAProParseError('EasyEDA Pro: the PCB has no manufacturing geometry')

    @staticmethod
    def _polygons_of(geo):
        if geo.is_empty:
            return []
        if isinstance(geo, Polygon):
            return [geo]
        if geo.geom_type in ('MultiPolygon', 'GeometryCollection'):
            return [p for child in geo.geoms for p in EasyEDAProParser._polygons_of(child)]
        return []


STROKE_MM = POURED_MM
POUR_EDGE_MM = 1.0


def shapely_set_precision(geometry, grid):
    import shapely
    return shapely.set_precision(geometry, grid)


class _Placement:
    """Footprint coordinates -> board: a bottom part is mirrored in Y, then turned by its angle, then moved."""

    def __init__(self, x=0.0, y=0.0, angle=0.0, flip=False):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        self.flip = flip
        if flip:
            self.m = (c, s, s, -c, x, y)
        else:
            self.m = (c, -s, s, c, x, y)

    def pt(self, p):
        a, b, d, e, xo, yo = self.m
        return (a * p[0] + b * p[1] + xo, d * p[0] + e * p[1] + yo)

    def geo(self, g):
        a, b, d, e, xo, yo = self.m
        return affine_transform(g, [a, b, d, e, xo, yo])
