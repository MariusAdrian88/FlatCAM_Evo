# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# MIT Licence                                              #
# ##########################################################
"""
ODB++ import (ODB++ Design 8.1 Update 3, docs/gerber/odb_spec_user.md).

The parser reads only the graphics of a job: one Gerber layer for each graphic layer of the matrix, Excellon drill
groups for the drill layers and the board profile. It ignores EDA, netlist, BOM, stackup, impedance and metadata.
The result has the shape of DipTraceParser (layers, drill_groups, default_layers, warnings).
"""

import datetime
import gzip
import math
import os
import re
import tarfile
import zipfile
from types import SimpleNamespace

from shapely import affinity
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import unary_union

from camlib import circle_quad_segs, gerber_circle_steps

PROFILE_WIDTH_MM = 0.01


class ODBParseError(ValueError):
    """A bad ODB++ job. The parser gives no partial output."""


# ----------------------------------------------------------------------------------------------------------------------
# UNIX compress (.Z) decoder
# ----------------------------------------------------------------------------------------------------------------------

def _unlzw(data, limit):
    """
    Decode UNIX 'compress' data (magic 1F 9D). The output must not be longer than 'limit' bytes.

    Port of the unlzw() of pigz (Mark Adler): block mode with the CLEAR code 256, code width 9..16 bits and the
    alignment of ncompress to a group of 'bits' bytes at a width change and after CLEAR.
    """
    data = bytes(data)
    size = len(data)
    if size < 3 or data[0] != 0x1F or data[1] != 0x9D:
        raise ODBParseError('Not a UNIX compress (.Z) file')
    flags = data[2]
    if flags & 0x60:
        raise ODBParseError('Unknown flags in a .Z file')
    max_bits = flags & 0x1F
    if max_bits < 9 or max_bits > 16:
        raise ODBParseError('Bad code size in a .Z file')
    if max_bits == 9:
        max_bits = 10   # 9 does not mean 9 (ncompress)
    block = flags & 0x80
    if size == 3:
        return b''
    if size == 4:
        raise ODBParseError('Truncated .Z file')

    bits = 9
    mask = 0x1FF
    end = 256 if block else 255
    buf = data[3] | (data[4] << 8)
    final = prev = buf & mask
    buf >>= bits
    left = 16 - bits
    if prev > 255:
        raise ODBParseError('Bad first code in a .Z file')
    if limit < 1:
        raise ODBParseError('A .Z member of the ODB++ job is larger than the limit (%d bytes)' % limit)
    out = bytearray([final])
    mark = 3
    nxt = 5
    prefix = [0] * 65536
    suffix = bytearray(65536)
    while nxt < size:
        if end >= mask and bits < max_bits:
            # Flush the unused bits and bytes to the next boundary of 'bits' bytes.
            rem = (nxt - mark) % bits
            if rem:
                rem = bits - rem
                if rem >= size - nxt:
                    break
                nxt += rem
            buf = left = 0
            mark = nxt
            bits += 1
            mask = (mask << 1) | 1
        buf += data[nxt] << left
        nxt += 1
        left += 8
        if left < bits:
            if nxt == size:
                raise ODBParseError('Truncated .Z file')
            buf += data[nxt] << left
            nxt += 1
            left += 8
        code = buf & mask
        buf >>= bits
        left -= bits
        if code == 256 and block:
            rem = (nxt - mark) % bits
            if rem:
                rem = bits - rem
                if rem > size - nxt:
                    break
                nxt += rem
            buf = left = 0
            mark = nxt
            bits = 9
            mask = 0x1FF
            end = 255
            continue
        current = code
        stack = bytearray()
        if code > end:
            if code != end + 1 or prev > end:
                raise ODBParseError('Bad code in a .Z file')
            stack.append(final)
            code = prev
        while code >= 256:
            stack.append(suffix[code])
            code = prefix[code]
        stack.append(code)
        final = code
        if end < mask:
            end += 1
            prefix[end] = prev
            suffix[end] = final
        prev = current
        if len(out) + len(stack) > limit:
            raise ODBParseError('A .Z member of the ODB++ job is larger than the limit (%d bytes)' % limit)
        stack.reverse()
        out += stack
    return bytes(out)


# ----------------------------------------------------------------------------------------------------------------------
# Job access
# ----------------------------------------------------------------------------------------------------------------------

# Members that the importer reads, relative to the job root, lower case. Other members are not kept.
_GRAPHICS_MEMBERS = re.compile(
    r'^(?P<prefix>(?:.*/)?)(?P<rel>(?:matrix/matrix|misc/info|fonts/standard|steps/[^/]+/stephdr'
    r'|steps/[^/]+/profile|steps/[^/]+/layers/[^/]+/(?:features|profile|tools)|symbols/[^/]+/features)'
    r'(?:\.z)?)$')

_LEGAL_NAME = re.compile(r'^[A-Za-z0-9_+\-][A-Za-z0-9_.+\-]*$')


def check_entity_name(name, what='entity'):
    """A step, layer or symbol name of the job. The names are also parts of member paths."""
    if (not name or '/' in name or '\\' in name or '..' in name or ':' in name
            or not _LEGAL_NAME.match(name) or name.endswith('.')):
        raise ODBParseError('Illegal ODB++ %s name: %r' % (what, name))
    return name


def _member_path(name):
    """The normalized path of an archive member, or ODBParseError for an unsafe one."""
    path = name.replace('\\', '/')
    if path.startswith('/') or re.match(r'^[A-Za-z]:', path):
        raise ODBParseError('Unsafe member in the ODB++ archive: %s' % name)
    parts = [part for part in path.split('/') if part not in ('', '.')]
    if any(part == '..' for part in parts):
        raise ODBParseError('Unsafe member in the ODB++ archive: %s' % name)
    return '/'.join(parts)


class _CountingReader:
    """A file object that stops the read of a decoded stream above a limit."""

    def __init__(self, raw, limit):
        self.raw = raw
        self.limit = limit
        self.count = 0

    def read(self, size=-1):
        data = self.raw.read(size)
        self.count += len(data)
        if self.count > self.limit:
            raise ODBParseError('The decoded ODB++ archive is larger than the limit (%d bytes)' % self.limit)
        return data


class ODBJob:
    """
    Read access to one ODB++ job: a .tgz / .tar.gz / .tar / .zip archive (found by content) or an unpacked folder.
    The reads are bounded; the limits are class attributes, so a test can make them small.
    """

    MEMBER_LIMIT = 256 << 20        # decoded size of one member
    KEPT_LIMIT = 1 << 30            # all member bytes held at one time
    STREAM_LIMIT = 4 << 30          # the decoded tar stream, skipped members included
    RATIO_LIMIT = 200               # decoded / compressed size of a zip member

    def __init__(self, kind, source, members):
        self.kind = kind                # 'tar', 'zip' or 'folder'
        self.source = source            # path of the archive or of the job root folder
        self._members = members         # lower-case relative path -> zip member name / file path / bytes (tar)
        self._cache = {}                # lower-case relative path -> bytes (decoded)
        self._held = 0
        self._zip = None
        self.name = ''                  # the job name (root folder or archive name)

    # ------------------------------------------------------------------------------------------------ detection
    @classmethod
    def open(cls, path):
        """The only detection rule for an ODB++ job. ODBParseError if the path is not an ODB++ job."""
        path = str(path)
        if os.path.isdir(path):
            return cls._open_folder(path)
        try:
            with open(path, 'rb') as handle:
                head = handle.read(4)
        except OSError as error:
            raise ODBParseError('Cannot read %s: %s' % (path, error)) from error
        if head[:2] == b'\x1f\x8b':
            return cls._open_tar(path, compressed=True)
        if zipfile.is_zipfile(path):
            return cls._open_zip(path)
        try:
            is_tar = tarfile.is_tarfile(path)
        except (OSError, tarfile.TarError):
            is_tar = False
        if is_tar:
            return cls._open_tar(path, compressed=False)
        return cls._open_folder(os.path.dirname(os.path.abspath(path)))

    @staticmethod
    def _root(prefixes):
        roots = set(prefixes)
        if not roots:
            raise ODBParseError('No ODB++ job (no matrix/matrix file)')
        if len(roots) > 1:
            raise ODBParseError('The archive holds more than one ODB++ job')
        return roots.pop()

    @staticmethod
    def _check_unique(paths):
        # Any repeated path is ambiguous, also 2 entries with the same spelling: one of them would win silently.
        seen = set()
        for path in paths:
            key = path.casefold()
            if key in seen:
                raise ODBParseError('Ambiguous member in the ODB++ archive: %s' % path)
            seen.add(key)

    @classmethod
    def _select(cls, entries):
        """entries: [(normalized path, value)] -> (root prefix, {rel lower: value}) of the graphics members."""
        found = []
        for path, value in entries:
            match = _GRAPHICS_MEMBERS.match(path.lower())
            if match:
                found.append((match.group('prefix'), match.group('rel'), value))
        root = cls._root(prefix for prefix, rel, _value in found if rel == 'matrix/matrix')
        # the original spelling of the root folder (the match ran on the lower-case path)
        spelling = next((path[:len(root)] for path, _value in entries if path.lower().startswith(root)), root)
        return spelling, {rel: value for prefix, rel, value in found if prefix == root}

    @staticmethod
    def _job_name(root, path):
        """The name of the job: its root folder, else the archive name without its extensions."""
        name = root.rstrip('/').rsplit('/', 1)[-1]
        if name:
            return name
        name = os.path.basename(path)
        for extension in ('.tar.gz', '.tgz', '.tar', '.zip'):
            if name.lower().endswith(extension):
                return name[:-len(extension)]
        return os.path.splitext(name)[0]

    @classmethod
    def _open_zip(cls, path):
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as error:
            raise ODBParseError('Bad zip file: %s' % error) from error
        entries = []
        for info in archive.infolist():
            name = _member_path(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode and mode not in (0o100000, 0o040000):
                raise ODBParseError('Link or device member in the ODB++ archive: %s' % info.filename)
            if info.is_dir():
                continue
            entries.append((name, info))
        cls._check_unique(name for name, _info in entries)
        root, members = cls._select(entries)
        job = cls('zip', path, members)
        job._zip = archive
        job.name = cls._job_name(root, path)
        return job

    @classmethod
    def _open_tar(cls, path, compressed):
        """One stream pass: discovery and the read of the graphics members, inside the limits."""
        try:
            raw = open(path, 'rb')
        except OSError as error:
            raise ODBParseError('Cannot read %s: %s' % (path, error)) from error
        job = cls('tar', path, {})
        try:
            stream = gzip.GzipFile(fileobj=raw, mode='rb') if compressed else raw
            counting = _CountingReader(stream, cls.STREAM_LIMIT)
            entries = []
            try:
                with tarfile.open(fileobj=counting, mode='r|') as archive:
                    for info in archive:
                        name = _member_path(info.name)
                        if info.issym() or info.islnk() or info.isdev() or info.isfifo():
                            raise ODBParseError('Link or device member in the ODB++ archive: %s' % info.name)
                        if not info.isfile():
                            continue
                        entries.append((name, None))
                        if not _GRAPHICS_MEMBERS.match(name.lower()):
                            continue
                        if info.size > cls.MEMBER_LIMIT:
                            raise ODBParseError('A member of the ODB++ job is larger than the limit (%d bytes): %s'
                                                % (cls.MEMBER_LIMIT, info.name))
                        # the budget is taken before the read, so a read never goes above it
                        job._hold(info.size)
                        handle = archive.extractfile(info)
                        data = handle.read(info.size) if handle is not None else b''
                        job._held -= info.size - len(data)
                        entries[-1] = (name, data)
            except (tarfile.TarError, OSError, EOFError, gzip.BadGzipFile) as error:
                raise ODBParseError('Bad tar archive: %s' % error) from error
        finally:
            raw.close()
        cls._check_unique(name for name, _data in entries)
        root, members = cls._select([(name, data) for name, data in entries if data is not None])
        job._members = members
        job.name = cls._job_name(root, path)
        # Only the members of the job root stay held.
        job._held = sum(len(data) for data in members.values())
        return job

    @classmethod
    def _open_folder(cls, start):
        folder = os.path.abspath(start)
        while True:
            if cls._find(folder, 'matrix', 'matrix'):
                break
            parent = os.path.dirname(folder)
            if parent == folder:
                raise ODBParseError('No ODB++ job (no matrix/matrix file) for %s' % start)
            folder = parent
        entries = []
        for directory, dirs, files in os.walk(folder, followlinks=False):
            for file_name in files:
                full = os.path.join(directory, file_name)
                rel = os.path.relpath(full, folder).replace(os.sep, '/')
                if not _GRAPHICS_MEMBERS.match(rel.lower()):
                    continue
                if os.path.islink(full):
                    raise ODBParseError('Link member in the ODB++ job: %s' % rel)
                entries.append((rel, full))
        cls._check_unique(rel for rel, _full in entries)
        members = {rel.lower(): full for rel, full in entries}
        job = cls('folder', folder, members)
        job.name = os.path.basename(folder) or 'odb'
        return job

    @staticmethod
    def _find(folder, *parts):
        """The path of folder/parts..., case-insensitive, or None."""
        current = folder
        for part in parts:
            try:
                names = os.listdir(current)
            except OSError:
                return None
            match = next((name for name in names if name.lower() == part.lower()), None)
            if match is None:
                return None
            current = os.path.join(current, match)
        return current

    # ------------------------------------------------------------------------------------------------ reading
    def _hold(self, count):
        self._held += count
        if self._held > self.KEPT_LIMIT:
            raise ODBParseError('The ODB++ job is larger than the memory limit (%d bytes)' % self.KEPT_LIMIT)

    def names(self):
        """The relative paths (lower case) of the graphics members, without the .z suffix."""
        return sorted({name[:-2] if name.endswith('.z') else name for name in self._members})

    def exists(self, relpath):
        key = relpath.lower()
        return key in self._members or key + '.z' in self._members

    def _raw(self, key):
        value = self._members[key]
        if self.kind == 'tar':
            return value
        if self.kind == 'zip':
            info = value
            if info.file_size > self.MEMBER_LIMIT:
                raise ODBParseError('A member of the ODB++ job is larger than the limit (%d bytes): %s'
                                    % (self.MEMBER_LIMIT, info.filename))
            if info.file_size > max(info.compress_size, 1) * self.RATIO_LIMIT:
                raise ODBParseError('A member of the ODB++ job has a compression ratio above the limit (%d): %s'
                                    % (self.RATIO_LIMIT, info.filename))
            # The budget is taken before the read, and the read goes into one buffer of the header size:
            # no chunk list, no join copy. A member that holds more bytes than its header says is refused.
            size = info.file_size
            self._hold(size)
            data = bytearray(size)
            view = memoryview(data)
            count = 0
            with self._zip.open(info) as handle:
                while count < size:
                    got = handle.readinto(view[count:min(size, count + (1 << 20))])
                    if not got:
                        break
                    count += got
                extra = handle.read(1)
            view.release()
            if count != size or extra:
                raise ODBParseError('A member of the ODB++ job does not have the size that its header says: %s'
                                    % info.filename)
            return data
        try:
            size = os.path.getsize(value)
            if size > self.MEMBER_LIMIT:
                raise ODBParseError('A file of the ODB++ job is larger than the limit (%d bytes): %s'
                                    % (self.MEMBER_LIMIT, value))
            self._hold(size)
            with open(value, 'rb') as handle:
                data = handle.read(size)
                if handle.read(1):
                    raise ODBParseError('A file of the ODB++ job changed while it was read: %s' % value)
        except OSError as error:
            raise ODBParseError('Cannot read %s: %s' % (value, error)) from error
        self._held -= size - len(data)
        return data

    def read(self, relpath):
        """The decoded bytes of a member (relative to the job root), or None. 'x' is also found as 'x.Z'."""
        key = relpath.lower()
        if key in self._cache:
            return self._cache[key]
        if key in self._members:
            # a tar member is held since the stream pass; a zip or folder member is held now
            data = self._raw(key)
        elif key + '.z' in self._members:
            packed = self._raw(key + '.z')
            # the packed bytes are held now: the output gets only the rest of the budget
            data = _unlzw(packed, max(min(self.MEMBER_LIMIT, self.KEPT_LIMIT - self._held), 0))
            self._hold(len(data))
            # the cache keeps the decoded bytes; the budget releases the packed bytes
            self._held -= len(packed)
            if self.kind == 'tar':
                self._members[key + '.z'] = b''
        else:
            return None
        self._cache[key] = data
        return data

    def read_text(self, relpath):
        data = self.read(relpath)
        if data is None:
            return None
        if data.startswith(b'\xef\xbb\xbf'):
            data = data[3:]
        return data.decode('utf-8', errors='replace')

    def close(self):
        if self._zip is not None:
            self._zip.close()
            self._zip = None


# ----------------------------------------------------------------------------------------------------------------------
# Structured text
# ----------------------------------------------------------------------------------------------------------------------

def _read_struct(text):
    """
    Structured text of ODB++ (matrix/matrix, stephdr, tools, misc/info, attrlist): 'KEY=value' lines and
    'NAME { ... }' arrays. Keys are upper case. A scalar key -> its value (str, the rest of the line, stripped);
    an array name -> a list of dicts.
    """
    result = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line == '}':
            current = None
            continue
        if line.endswith('{') and '=' not in line:
            name = line[:-1].strip().upper()
            current = {}
            result.setdefault(name, []).append(current)
            continue
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        target = current if current is not None else result
        target[key.strip().upper()] = value.strip()
    return result


# ----------------------------------------------------------------------------------------------------------------------
# Features, symbols and renderer
# ----------------------------------------------------------------------------------------------------------------------

_KEPT_ATTRS = ('.drill', '.rout_plated')
_QUOTE_CLOSE = {"'": ("'",), '“': ('”', '“', '"'), '"': ('"',)}
_INCH_MM = 25.4
_MIL_MM = 0.0254
_MICRON_MM = 0.001
_NUM = r'\d*\.?\d+'
_CORNER_NAMES = {'1': 'top_right', '2': 'top_left', '3': 'bottom_left', '4': 'bottom_right'}
_ALL_CORNERS = ('top_right', 'top_left', 'bottom_left', 'bottom_right')


def _tokenize(line):
    """
    Split a record line into tokens; a text in quotes ('...' or the curly double quotes) is one token with its
    quotes. The split of the ';attributes;ID=' suffix comes after that, so a ';' inside a text stays.
    Returns (tokens, attribute string after the first ';').
    """
    tokens, cur, quote, attrs = [], [], None, ''
    for pos, char in enumerate(line):
        if quote is not None:
            cur.append(char)
            if char in quote:
                quote = None
            continue
        if char == ';':
            attrs = line[pos + 1:]
            break
        if not cur and char in _QUOTE_CLOSE:
            quote = _QUOTE_CLOSE[char]
            cur.append(char)
            continue
        if char.isspace():
            if cur:
                tokens.append(''.join(cur))
                cur = []
            continue
        cur.append(char)
    if cur:
        tokens.append(''.join(cur))
    return tokens, attrs


def _orient(tokens, index):
    """orient_def at tokens[index] (one or two tokens) -> (clockwise angle, mirror, next index)."""
    code = int(tokens[index])
    if code in (8, 9):
        return float(tokens[index + 1]), code == 9, index + 2
    if 0 <= code <= 7:
        return 90.0 * (code % 4), code >= 4, index + 1
    raise ValueError('bad orient_def')


class _FeatureFile:
    """
    The line records of a features or profile file (also the features of a user symbol). The coordinates of the
    records are in mm. Records are SimpleNamespace objects with the field 'kind': P, L, A, S, T.
    """

    def __init__(self, text, default_units='INCH', path='features'):
        """A malformed record or an unfinished surface is a bad job: ODBParseError with the path and the line."""
        self.units = 'MM' if str(default_units).upper() == 'MM' else 'INCH'
        self.symbols = {}           # index -> (name, size unit suffix)
        self.attr_names = {}
        self.texts = {}
        self.records = []
        self.barcodes = 0
        self._surface = None
        self._poly = None
        number = 0
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            tokens, attrs = _tokenize(line)
            if not tokens:
                continue
            try:
                self._record(tokens, attrs)
            except (ValueError, IndexError, AttributeError) as error:
                raise ODBParseError('Malformed record in %s, line %d: %s' % (path, number, line)) from error
        if self._surface is not None:
            raise ODBParseError('Unfinished surface in %s, line %d: the file ends before SE' % (path, number))

    @property
    def scale(self):
        """mm of one coordinate unit."""
        return _INCH_MM if self.units == 'INCH' else 1.0

    @property
    def size_unit(self):
        """mm of one symbol size unit (mil or micron)."""
        return _MIL_MM if self.units == 'INCH' else _MICRON_MM

    def symbol_factor(self, index):
        """(name, mm per size unit) of a symbol index, or None."""
        entry = self.symbols.get(index)
        if entry is None:
            return None
        name, suffix = entry
        if suffix == 'I':
            return name, _MIL_MM
        if suffix == 'M':
            return name, _MICRON_MM
        return name, self.size_unit

    def _attrs(self, text):
        found = {}
        for part in text.split(';')[0].split(','):
            part = part.strip()
            if not part:
                continue
            key, _sep, value = part.partition('=')
            try:
                name = self.attr_names.get(int(key))
            except ValueError:
                continue
            if name in _KEPT_ATTRS:
                found[name] = value if value != '' else True
        return found

    def _record(self, tokens, attrs):
        head = tokens[0]
        scale = self.scale
        if self._surface is not None:
            if (head in ('OS', 'OC', 'OE')) != (self._poly is not None):
                raise ValueError('surface contour out of order')
            if head == 'OB':
                self._poly = SimpleNamespace(start=(float(tokens[1]) * scale, float(tokens[2]) * scale),
                                             hole=tokens[3].upper() == 'H', segs=[])
            elif head == 'OS':
                self._poly.segs.append(('S', float(tokens[1]) * scale, float(tokens[2]) * scale))
            elif head == 'OC':
                self._poly.segs.append(('C', float(tokens[1]) * scale, float(tokens[2]) * scale,
                                        float(tokens[3]) * scale, float(tokens[4]) * scale,
                                        tokens[5].upper() == 'Y'))
            elif head == 'OE':
                self._surface.polys.append(self._poly)
                self._poly = None
            elif head == 'SE':
                self.records.append(self._surface)
                self._surface = None
            else:
                raise ValueError('bad surface line')
            return
        if head.upper().startswith('UNITS='):
            self.units = 'MM' if head[6:].strip().upper() == 'MM' else 'INCH'
        elif head[0] == '$':
            name = tokens[1]
            suffix = tokens[2].upper() if len(tokens) > 2 and tokens[2].upper() in ('I', 'M') else ''
            if not suffix and name[-1:] in ('I', 'M') and _standard_split(name[:-1]) is not None:
                # the suffix attached to the name: '$0 oval210x170I' (units example of the spec)
                name, suffix = name[:-1], name[-1]
            self.symbols[int(head[1:])] = (name, suffix)
        elif head[0] == '@':
            self.attr_names[int(head[1:])] = tokens[1]
        elif head[0] == '&':
            self.texts[int(head[1:])] = tokens[1] if len(tokens) > 1 else ''
        elif head == 'P':
            x, y = float(tokens[1]) * scale, float(tokens[2]) * scale
            if tokens[3] == '-1':
                symbol, resize, pos = int(tokens[4]), float(tokens[5]), 6
            else:
                symbol, resize, pos = int(tokens[3]), 0.0, 4
            polarity = tokens[pos].upper()
            dcode = int(tokens[pos + 1])
            angle, mirror = 0.0, False
            if len(tokens) > pos + 2:
                angle, mirror, _next = _orient(tokens, pos + 2)
            self.records.append(SimpleNamespace(kind='P', x=x, y=y, symbol=symbol, resize=resize, pol=polarity,
                                                dcode=dcode, angle=angle, mirror=mirror, attrs=self._attrs(attrs)))
        elif head == 'L':
            self.records.append(SimpleNamespace(
                kind='L', xs=float(tokens[1]) * scale, ys=float(tokens[2]) * scale, xe=float(tokens[3]) * scale,
                ye=float(tokens[4]) * scale, symbol=int(tokens[5]), pol=tokens[6].upper(), dcode=int(tokens[7]),
                attrs=self._attrs(attrs)))
        elif head == 'A':
            self.records.append(SimpleNamespace(
                kind='A', xs=float(tokens[1]) * scale, ys=float(tokens[2]) * scale, xe=float(tokens[3]) * scale,
                ye=float(tokens[4]) * scale, xc=float(tokens[5]) * scale, yc=float(tokens[6]) * scale,
                symbol=int(tokens[7]), pol=tokens[8].upper(), dcode=int(tokens[9]), cw=tokens[10].upper() == 'Y',
                attrs=self._attrs(attrs)))
        elif head == 'T':
            x, y = float(tokens[1]) * scale, float(tokens[2]) * scale
            font, polarity = tokens[3], tokens[4].upper()
            angle, mirror, pos = _orient(tokens, 5)
            text = tokens[pos + 3]
            if text[:1] in _QUOTE_CLOSE and len(text) >= 2:
                text = text[1:-1]
            self.records.append(SimpleNamespace(
                kind='T', x=x, y=y, font=font, pol=polarity, angle=angle, mirror=mirror,
                xsize=float(tokens[pos]) * scale, ysize=float(tokens[pos + 1]) * scale,
                width_factor=float(tokens[pos + 2]), text=text, version=int(tokens[pos + 4]),
                attrs=self._attrs(attrs)))
        elif head == 'B':
            self.barcodes += 1
        elif head == 'S':
            self._surface = SimpleNamespace(kind='S', pol=tokens[1].upper(), dcode=int(tokens[2]), polys=[],
                                            attrs=self._attrs(attrs))
        # ID=, F, and the other header lines are not needed


def _rect_poly(w, h, radius=0.0, chamfer=0.0, chamfered=(), square=(), quad=4):
    """Rectangle centred at the origin; corners may be chamfered, rounded or square (logic of KiCadPcbParser)."""
    if w <= 0 or h <= 0:
        return Polygon()
    radius = max(0.0, min(radius, w / 2, h / 2))
    chamfer = max(0.0, min(chamfer, w, h))
    hx, hy = w / 2, h / 2
    corners = (('top_left', (-hx, hy), (1, -1)), ('top_right', (hx, hy), (-1, -1)),
               ('bottom_right', (hx, -hy), (-1, 1)), ('bottom_left', (-hx, -hy), (1, 1)))
    points = []
    for name, (cx, cy), (sx, sy) in corners:
        if name in chamfered and chamfer > 0:
            before, after = {'top_left': ((cx, cy + sy * chamfer), (cx + sx * chamfer, cy)),
                             'top_right': ((cx + sx * chamfer, cy), (cx, cy + sy * chamfer)),
                             'bottom_right': ((cx, cy + sy * chamfer), (cx + sx * chamfer, cy)),
                             'bottom_left': ((cx + sx * chamfer, cy), (cx, cy + sy * chamfer))}[name]
            points.extend([before, after])
        elif radius > 0 and name not in square:
            centre = (cx + sx * radius, cy + sy * radius)
            start_angle = {'top_left': 180, 'top_right': 90, 'bottom_right': 0, 'bottom_left': 270}[name]
            steps = max(2, quad)
            for i in range(steps + 1):
                angle = math.radians(start_angle - 90 * i / steps)
                points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
        else:
            points.append((cx, cy))
    return Polygon(points)


def _split_params(rest):
    """'100x50xr8x12' -> numbers [100, 50], corner mode 'r' / 'c', radius 8, corners '12', letter flags []."""
    nums, mode, radius, corners, flags = [], None, 0.0, None, []
    for token in rest.split('x'):
        if not token:
            continue
        if re.fullmatch(r'[rc]' + _NUM, token):
            mode, radius = token[0], float(token[1:])
        elif token in ('r', 's', 'p', 'n', 'v'):
            flags.append(token)
        elif token.startswith(('ra', 'ro')):
            continue
        elif mode is not None and corners is None and re.fullmatch(r'\d+', token):
            corners = token
        else:
            nums.append(float(token))
    return nums, mode, radius, corners, flags


_STD_PREFIXES = ('donut_rc', 'donut_sr', 'donut_s', 'donut_r', 'donut_o', 'oblong_ths', 'rc_ths', 'rc_tho', 's_ths',
                 's_tho', 's_thr', 'sr_ths', 'o_ths', 'hex_l', 'hex_s', 'oval_h', 'radhplate', 'rhplate', 'fhplate',
                 'hplate', 'dshape', 'dogbone', 'dpack', 'cross', 'moire', 'rect', 'oval', 'oct', 'bfr', 'bfs', 'tri',
                 'thr', 'ths', 'hole', 'null', 'el', 'di', 'r', 's')
_UNSUPPORTED = ('dogbone', 'radhplate')


def _standard_split(name):
    """(prefix, parameter text) of a standard symbol name, or None."""
    for prefix in _STD_PREFIXES:
        if name.startswith(prefix):
            rest = name[len(prefix):]
            if rest == '' or rest[0].isdigit() or rest[0] == '.':
                return prefix, rest
    return None


class SymbolLib:
    """Symbol name -> shape at the origin (mm, unrotated), cached per name and size unit."""

    def __init__(self, renderer):
        self.renderer = renderer
        self._cache = {}
        self._failed = {}
        self._active = set()

    # ---------------------------------------------------------------------------------------------- public
    def shape(self, name, factor):
        """The shape of a symbol (mm per size unit = factor). None for an unknown symbol (one warning)."""
        split = _standard_split(name)
        key = (name, factor) if split else (name, None)
        parser = self.renderer.parser
        if key in self._cache:
            if key in self._failed:
                parser._count(self._failed[key], name)
            return self._cache[key]
        result = None
        if split is not None:
            try:
                result = self._standard(split[0], split[1], factor)
            except (ValueError, IndexError, ZeroDivisionError):
                self._failed[key] = 'Symbol %s is not valid and is skipped (%d features)'
            except NotImplementedError:
                self._failed[key] = 'Symbol %s is not supported and is skipped (%d features)'
        else:
            result = self._user(name)
            if result is None:
                self._failed[key] = 'Symbol %s is unknown or refers to itself and is skipped (%d features)'
        if key in self._failed:
            parser._count(self._failed[key], name)
        if result is not None and not result.is_valid:
            result = result.buffer(0)
        self._cache[key] = result
        return result

    def sweep_kind(self, name, factor):
        """('r', d) or ('s', side) in mm for the symbols that can draw a line or an arc, else None."""
        match = re.fullmatch(r'([rs])(' + _NUM + ')', name)
        if match:
            return match.group(1), float(match.group(2)) * factor
        return None

    def drill_symbol(self, name, factor):
        """
        (diameter mm, plating hint or None, half axis or None) of a drill symbol, 'null' for a placeholder, else
        None. An oval is a slot (KiCad writes the oblong holes so): the diameter is the short side and the half axis
        (dx, dy) in mm goes from the center to the center of one end, in the frame of the symbol.
        """
        match = re.fullmatch(r'r(' + _NUM + ')', name)
        if match:
            return float(match.group(1)) * factor, None, None
        match = re.fullmatch(r'oval(' + _NUM + ')x(' + _NUM + ')', name)
        if match:
            w, h = float(match.group(1)) * factor, float(match.group(2)) * factor
            if abs(w - h) < 1e-9:
                return w, None, None
            return (h, None, ((w - h) / 2, 0.0)) if w > h else (w, None, (0.0, (h - w) / 2))
        match = re.fullmatch(r'hole(' + _NUM + r')(?:x([pnv]))?(?:x.*)?', name)
        if match:
            hint = {'p': 'PTH', 'v': 'PTH', 'n': 'NPTH'}.get(match.group(2))
            return float(match.group(1)) * factor, hint, None
        if name.startswith('null'):
            return 'null'
        return None

    # ---------------------------------------------------------------------------------------------- user symbols
    def _user(self, name):
        parser = self.renderer.parser
        try:
            check_entity_name(name, 'symbol')
        except ODBParseError:
            return None
        if name in self._active:
            return None
        relpath = 'symbols/%s/features' % name
        text = parser.job.read_text(relpath)
        if text is None:
            return None
        self._active.add(name)
        try:
            items = self.renderer.file_items(_FeatureFile(text, parser.units, relpath), None, None)
            return self.renderer.compose(items)
        finally:
            self._active.discard(name)

    # ---------------------------------------------------------------------------------------------- standard
    def _standard(self, prefix, rest, factor):
        q = self.renderer.quad
        if prefix == 'cross':
            return self._cross(rest, factor)
        raw, mode, radius, corners, flags = _split_params(rest)
        v = [n * factor for n in raw]
        radius *= factor

        def circ(d):
            return Point(0, 0).buffer(d / 2, quad_segs=q(d / 2)) if d > 0 else Polygon()

        def sq(s):
            return box(-s / 2, -s / 2, s / 2, s / 2) if s > 0 else Polygon()

        def oval(w, h):
            if w <= 0 or h <= 0:
                return Polygon()
            if abs(w - h) < 1e-12:
                return circ(w)
            if w > h:
                return LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=q(h / 2))
            return LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=q(w / 2))

        def rect(w, h):
            if mode is None:
                return box(-w / 2, -h / 2, w / 2, h / 2) if w > 0 and h > 0 else Polygon()
            chosen = tuple(_CORNER_NAMES[c] for c in corners) if corners else _ALL_CORNERS
            if mode == 'r':
                return _rect_poly(w, h, radius=radius, square=tuple(c for c in _ALL_CORNERS if c not in chosen),
                                  quad=q(radius))
            return _rect_poly(w, h, chamfer=radius, chamfered=chosen)

        def spokes(count, angle, gap, reach):
            bars = []
            g = gap / 2
            for k in range(count):
                a = math.radians(angle + k * 360.0 / count)
                c, s = math.cos(a), math.sin(a)
                bars.append(Polygon([(-s * g, c * g), (reach * c - s * g, reach * s + c * g),
                                     (reach * c + s * g, reach * s - c * g), (s * g, -c * g)]))
            return unary_union(bars)

        def thermal(outer, inner, angle, count, gap):
            reach = max(abs(b) for b in outer.bounds) * 2 if not outer.is_empty else 0
            return outer.difference(inner).difference(spokes(int(count), angle, gap, reach))

        def rounded_thermal(centre, width, angle, count, gap):
            """
            The ring pieces of a thermal with rounded ends: the centre line of the ring (a closed line) minus the
            gaps, drawn with a round line of the ring width. The end caps touch the gap sides on the centre line.
            """
            if width <= 0:
                return Polygon()
            reach = max(abs(b) for b in centre.bounds) * 2 + width
            cuts = spokes(int(count), angle, gap + width, reach) if int(count) > 0 else Polygon()
            return centre.difference(cuts).buffer(width / 2, quad_segs=q(width / 2))

        def circle_line(d):
            return circ(d).exterior

        if prefix == 'r':
            return circ(v[0])
        if prefix == 's':
            return sq(v[0])
        if prefix == 'rect':
            return rect(v[0], v[1])
        if prefix == 'oval':
            return oval(v[0], v[1])
        if prefix == 'di':
            w, h = v[0], v[1]
            return Polygon([(-w / 2, 0), (0, h / 2), (w / 2, 0), (0, -h / 2)])
        if prefix == 'oct':
            w, h, r = v[0], v[1], min(v[2], v[0] / 2, v[1] / 2)
            return Polygon([(-w / 2 + r, h / 2), (w / 2 - r, h / 2), (w / 2, h / 2 - r), (w / 2, -h / 2 + r),
                            (w / 2 - r, -h / 2), (-w / 2 + r, -h / 2), (-w / 2, -h / 2 + r), (-w / 2, h / 2 - r)])
        if prefix == 'donut_r':
            return circ(v[0]).difference(circ(v[1]))
        if prefix == 'donut_s':
            return rect(v[0], v[0]).difference(sq(v[1]))
        if prefix == 'donut_sr':
            return sq(v[0]).difference(circ(v[1]))
        if prefix == 'donut_rc':
            return rect(v[0], v[1]).difference(box(-v[0] / 2 + v[2], -v[1] / 2 + v[2], v[0] / 2 - v[2],
                                                   v[1] / 2 - v[2]))
        if prefix == 'donut_o':
            return oval(v[0], v[1]).difference(oval(v[0] - 2 * v[2], v[1] - 2 * v[2]))
        if prefix in ('hex_l', 'hex_s'):
            w, h, r = v[0], v[1], v[2]
            if prefix == 'hex_l':
                return Polygon([(-w / 2 + r, h / 2), (w / 2 - r, h / 2), (w / 2, 0), (w / 2 - r, -h / 2),
                                (-w / 2 + r, -h / 2), (-w / 2, 0)])
            return Polygon([(0, h / 2), (w / 2, h / 2 - r), (w / 2, -h / 2 + r), (0, -h / 2),
                            (-w / 2, -h / 2 + r), (-w / 2, h / 2 - r)])
        if prefix == 'bfr':
            # two opposite quadrants (top-left and bottom-right); the spec picture shows which ones
            big = circ(v[0])
            return big.intersection(unary_union([box(-v[0], 0, 0, v[0]), box(0, -v[0], v[0], 0)]))
        if prefix == 'bfs':
            s = v[0]
            return unary_union([box(-s / 2, 0, 0, s / 2), box(0, -s / 2, s / 2, 0)])
        if prefix == 'tri':
            return Polygon([(-v[0] / 2, -v[1] / 2), (v[0] / 2, -v[1] / 2), (0, v[1] / 2)])
        if prefix == 'oval_h':
            w, h = v[0], v[1]
            body = box(-w / 2, -h / 2, w / 2 - h / 2, h / 2) if w > h / 2 else Polygon()
            return unary_union([body, Point(w / 2 - h / 2, 0).buffer(h / 2, quad_segs=q(h / 2))]).intersection(
                box(-w / 2, -h / 2, w / 2, h / 2))
        if prefix == 'thr':
            # Round thermal (rounded): the ring pieces have round ends.
            return rounded_thermal(circle_line((v[0] + v[1]) / 2), (v[0] - v[1]) / 2, raw[2], raw[3], v[4])
        if prefix == 'ths':
            # Round thermal (squared): the gaps cut the ring straight.
            return thermal(circ(v[0]), circ(v[1]), raw[2], raw[3], v[4])
        if prefix in ('s_ths', 's_tho'):
            return thermal(rect(v[0], v[0]), sq(v[1]), raw[2], raw[3], v[4])
        if prefix == 's_thr':
            # Line thermal: 4 lines along the sides, gaps at 45 degrees, ends rounded with diameter (os - is) / 2.
            return rounded_thermal(sq((v[0] + v[1]) / 2).exterior, (v[0] - v[1]) / 2, 45.0, 4, v[4])
        if prefix == 'sr_ths':
            return thermal(sq(v[0]), circ(v[1]), raw[2], raw[3], v[4])
        if prefix in ('rc_ths', 'rc_tho'):
            air = v[5]
            return thermal(rect(v[0], v[1]), box(-v[0] / 2 + air, -v[1] / 2 + air, v[0] / 2 - air, v[1] / 2 - air),
                           raw[2], raw[3], v[4])
        if prefix in ('o_ths', 'oblong_ths'):
            return thermal(oval(v[0], v[1]), oval(v[0] - 2 * v[5], v[1] - 2 * v[5]), raw[2], raw[3], v[4])
        if prefix == 'el':
            return affinity.scale(Point(0, 0).buffer(0.5, quad_segs=q(max(v[0], v[1]) / 2)), v[0], v[1])
        if prefix == 'moire':
            rw, rg, count, lw, ll, la = v[0], v[1], int(raw[2]), v[3], v[4], raw[5]
            parts = []
            for i in range(count):
                inner = i * (rw + rg)
                parts.append(circ(2 * (inner + rw)).difference(circ(2 * inner)))
            line = box(-ll / 2, -lw / 2, ll / 2, lw / 2)
            parts.append(affinity.rotate(line, la, origin=(0, 0)))
            parts.append(affinity.rotate(line, la + 90, origin=(0, 0)))
            return unary_union(parts)
        if prefix == 'hole':
            return circ(v[0])
        if prefix == 'null':
            return Polygon()
        if prefix in ('hplate', 'rhplate'):
            w, h, c = v[0], v[1], v[2]
            plate = Polygon([(-w / 2, -h / 2), (w / 2 - c, -h / 2), (w / 2, 0), (w / 2 - c, h / 2), (-w / 2, h / 2)])
            return plate if prefix == 'hplate' else affinity.scale(plate, -1, 1, origin=(0, 0))
        if prefix == 'fhplate':
            w, h, vc, hc = v[0], v[1], v[2], v[3]
            return Polygon([(-w / 2, -h / 2), (w / 2 - hc, -h / 2), (w / 2, -h / 2 + vc), (w / 2, h / 2 - vc),
                            (w / 2 - hc, h / 2), (-w / 2, h / 2)])
        if prefix == 'dshape':
            w, h, r = v[0], v[1], min(v[2], v[0], v[1] / 2)
            return _rect_poly(w, h, radius=r, square=('top_left', 'bottom_left'), quad=q(r))
        if prefix == 'dpack':
            w, h, hg, vg = v[:4]
            hn, vn = int(raw[4]), int(raw[5])
            pw, ph = (w - hg * (hn - 1)) / hn, (h - vg * (vn - 1)) / vn
            if pw <= 0 or ph <= 0:
                raise ValueError('bad dpack')
            return unary_union([box(-w / 2 + i * (pw + hg), -h / 2 + j * (ph + vg),
                                    -w / 2 + i * (pw + hg) + pw, -h / 2 + j * (ph + vg) + ph)
                                for i in range(hn) for j in range(vn)])
        if prefix in _UNSUPPORTED:
            raise NotImplementedError(prefix)
        raise ValueError(prefix)

    def _cross(self, rest, factor):
        """
        cross<w>x<h>x<hs>x<vs>x<hc>x<vc>x[r|s][<ra>]: a horizontal line (width hs) and a vertical line (width vs)
        that meet at (hc, vc) from the lower-left corner. 'r' lines have round ends, 's' lines square ends; ra
        rounds the inside corners.
        """
        q = self.renderer.quad
        tokens = rest.split('x')
        if len(tokens) not in (6, 7):
            raise ValueError('cross')
        w, h, hs, vs, hc, vc = [float(t) * factor for t in tokens[:6]]
        style, ra = 's', 0.0
        if len(tokens) == 7:
            match = re.fullmatch(r'([rs])(' + _NUM + ')?', tokens[6])
            if match is None:
                raise ValueError('cross style')
            style, ra = match.group(1), float(match.group(2) or 0) * factor
        if min(w, h, hs, vs) <= 0:
            raise ValueError('cross size')
        y = vc - h / 2
        x = hc - w / 2
        if style == 'r':
            horizontal = LineString([(-w / 2 + hs / 2, y), (w / 2 - hs / 2, y)]).buffer(hs / 2, quad_segs=q(hs / 2))
            vertical = LineString([(x, -h / 2 + vs / 2), (x, h / 2 - vs / 2)]).buffer(vs / 2, quad_segs=q(vs / 2))
        else:
            horizontal = box(-w / 2, y - hs / 2, w / 2, y + hs / 2)
            vertical = box(x - vs / 2, -h / 2, x + vs / 2, h / 2)
        shape = unary_union([horizontal, vertical])
        if ra > 0:
            # a closing rounds only the concave (inside) corners
            shape = shape.buffer(ra, quad_segs=q(ra), join_style=1).buffer(-ra, quad_segs=q(ra), join_style=1)
        return shape


_DATE_VARIABLES = ('DATE-MMDDYYYY', 'DATE-DDMMYYYY', 'DATE-MMDDYY', 'DATE-DDMMYY', 'WEEK-DAY', 'TIME', 'YYYY',
                   'DD', 'MM', 'YY', 'WW')
_TEXT_VARIABLE = re.compile(r'\$\$(DATE-MMDDYYYY|DATE-DDMMYYYY|DATE-MMDDYY|DATE-DDMMYY|WEEK-DAY|X_MM|Y_MM|STEP|LAYER'
                            r'|TIME|JOB|YYYY|DD|MM|YY|WW|X|Y)')


class StrokeFont:
    """
    A stroke font: the file fonts/standard (header XSIZE, YSIZE, OFFSET; blocks CHAR c, LINE xs ys xe ye P|N R|S width,
    ECHAR) or the built-in fallback. Font units are free: the glyphs are scaled by xsize / XSIZE and ysize / YSIZE.
    glyphs: char -> [(xs, ys, xe, ye, polarity, 'R' or 'S')]; origin = the lower-left corner of the font cell,
    widest = the width of the widest glyph (centre lines).
    """

    def __init__(self, xsize, ysize, offset, glyphs, origin=None):
        self.xsize, self.ysize, self.offset = xsize, ysize, offset
        self.glyphs = glyphs
        self.bounds = {}
        for char, strokes in glyphs.items():
            xs = [v for stroke in strokes for v in (stroke[0], stroke[2])]
            ys = [v for stroke in strokes for v in (stroke[1], stroke[3])]
            if xs:
                self.bounds[char] = (min(xs), min(ys), max(xs), max(ys))
        if self.bounds:
            boxes = self.bounds.values()
            self.origin = origin or (min(b[0] for b in boxes), min(b[1] for b in boxes))
            self.widest = max(b[2] - b[0] for b in boxes)
        else:
            self.origin, self.widest = origin or (0.0, 0.0), 0.0

    @classmethod
    def parse(cls, text):
        """The font of a fonts/standard text, or None when it has no usable header or glyph."""
        tokens = [word for line in text.splitlines() if not line.lstrip().startswith('#') for word in line.split()]
        head, glyphs, current, i = {}, {}, None, 0
        try:
            while i < len(tokens):
                word = tokens[i].upper()
                if word in ('XSIZE', 'YSIZE', 'OFFSET'):
                    head[word] = float(tokens[i + 1])
                    i += 2
                elif word.startswith('CHAR'):
                    if len(word) > 4:
                        char, i = tokens[i][4:], i + 1
                    else:
                        char, i = tokens[i + 1], i + 2
                    current = glyphs[char] = []
                elif word == 'LINE':
                    if current is None:
                        raise ValueError('LINE outside of a character')
                    xs, ys, xe, ye = (float(v) for v in tokens[i + 1:i + 5])
                    current.append((xs, ys, xe, ye, 'N' if tokens[i + 5].upper() == 'N' else 'P',
                                    'S' if tokens[i + 6].upper() == 'S' else 'R'))
                    float(tokens[i + 7])
                    i += 8
                else:
                    current = None if word == 'ECHAR' else current
                    i += 1
        except (ValueError, IndexError):
            return None
        if head.get('XSIZE', 0) <= 0 or head.get('YSIZE', 0) <= 0 or not any(glyphs.values()):
            return None
        return cls(head['XSIZE'], head['YSIZE'], head.get('OFFSET', 0.0), glyphs)

    @classmethod
    def fallback(cls):
        """The stroke glyphs of the PcbDoc and KiCad readers (6-cell cap height, 6 cells of advance)."""
        from appParsers.ParseKiCadPcb import KiCadPcbParser
        from appParsers.ParsePcbDoc import PcbDocParser
        paths = dict(PcbDocParser.STROKE_GLYPHS)
        paths.update(KiCadPcbParser._LOWER_GLYPHS)
        glyphs = {}
        for char, polylines in paths.items():
            strokes = []
            for path in polylines:
                pairs = list(zip(path, path[1:])) or [(path[0], path[0])]
                strokes.extend((a[0], a[1], b[0], b[1], 'P', 'R') for a, b in pairs)
            glyphs[char] = strokes
        return cls(6.0, 6.0, 0.0, glyphs, origin=(0.0, 0.0))


class _Item:
    """One feature of a stream: polarity 'P' or 'N', its shape and the centre line (L, A) for follow geometry."""
    __slots__ = ('pol', 'shape', 'follow')

    def __init__(self, pol, shape, follow=None):
        self.pol = pol
        self.shape = shape
        self.follow = follow


class _Drill:
    """A hole (one point) or a slot (two points) of a drill layer stream; mm."""
    __slots__ = ('pts', 'dia', 'plating')

    def __init__(self, pts, dia, plating):
        self.pts = pts
        self.dia = dia
        self.plating = plating


def _polygons(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, 'geoms'):
        found = []
        for part in geom.geoms:
            found.extend(_polygons(part))
        return found
    return []


def _arc_points(start, end, centre, cw, steps_full):
    """Points of a circular arc (start, end, centre in mm); start == end is a full circle."""
    sx, sy = start
    ex, ey = end
    cx, cy = centre
    r0, r1 = math.hypot(sx - cx, sy - cy), math.hypot(ex - cx, ey - cy)
    a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(ey - cy, ex - cx)
    full = 2 * math.pi
    if math.hypot(sx - ex, sy - ey) < 1e-9:
        span = full
    else:
        span = ((a0 - a1) if cw else (a1 - a0)) % full
        if span < 1e-12:
            span = full
    count = max(2, int(math.ceil(steps_full * span / full)))
    points = []
    for i in range(count + 1):
        t = i / count
        angle = a0 - span * t if cw else a0 + span * t
        radius = r0 + (r1 - r0) * t
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    points[0], points[-1] = (sx, sy), (ex, ey)
    return points


class _Renderer:
    """
    Stage A: layer_stream() / full_stream() build the ordered feature stream of a step layer.
    Stage B: compose() draws a stream: positive items add, negative items erase what came before.
    """

    def __init__(self, parser):
        self.parser = parser
        self.job = parser.job
        self.symbols = SymbolLib(self)
        self._files = {}
        self._profiles = {}
        self._boundaries = {}
        self._streams = {}
        self._full = {}
        self._drill_streams = {}
        self._drill_full = {}
        self._tools = {}
        self._copy_cache = {}
        self._font = None
        self._font_fallback = False
        self._date_variables = set()

    # ---------------------------------------------------------------------------------------------- helpers
    def quad(self, radius):
        return circle_quad_segs(self.parser._steps(radius))

    def feature_file(self, relpath):
        key = relpath.lower()
        if key not in self._files:
            text = self.job.read_text(relpath)
            self._files[key] = None if text is None else _FeatureFile(text, self.parser.units, relpath)
        return self._files[key]

    def _stroke_font(self):
        if self._font is None:
            text = self.job.read_text('fonts/standard')
            font = StrokeFont.parse(text) if text else None
            self._font_fallback = font is None
            self._font = font or StrokeFont.fallback()
        return self._font

    def _dynamic_text(self, rec, layer_name, step):
        """The text of a T record with its $$ variables resolved (date and time variables get the import date)."""
        if '$$' not in rec.text:
            return rec.text
        now = datetime.datetime.now()
        values = {'DATE-MMDDYY': now.strftime('%m/%d/%y'), 'DATE-DDMMYY': now.strftime('%d/%m/%y'),
                  'DATE-MMDDYYYY': now.strftime('%m/%d/%Y'), 'DATE-DDMMYYYY': now.strftime('%d/%m/%Y'),
                  'DD': now.strftime('%d'), 'WEEK-DAY': now.strftime('%A'), 'MM': now.strftime('%m'),
                  'YY': now.strftime('%y'), 'YYYY': now.strftime('%Y'), 'WW': '%02d' % now.isocalendar()[1],
                  'TIME': now.strftime('%H:%M:%S'), 'JOB': self.parser.name, 'STEP': step, 'LAYER': layer_name,
                  'X': '%.4f' % (rec.x / _INCH_MM), 'Y': '%.4f' % (rec.y / _INCH_MM),
                  'X_MM': '%.4f' % rec.x, 'Y_MM': '%.4f' % rec.y}

        def resolve(match):
            name = match.group(1)
            if name in _DATE_VARIABLES:
                self._date_variables.add(name)
            return values[name]

        return _TEXT_VARIABLE.sub(resolve, rec.text)

    def _glyph_shape(self, font, char, sx, sy, width):
        """One character at the pen position 0: the P lines add, the N lines erase inside the glyph."""
        ox, oy = font.origin
        items = []
        for xs, ys, xe, ye, pol, kind in font.glyphs[char]:
            points = [((xs - ox) * sx, (ys - oy) * sy), ((xe - ox) * sx, (ye - oy) * sy)]
            shape = self._sweep('r' if kind == 'R' else 's', width, points)
            if not shape.is_empty:
                items.append(_Item(pol, shape))
        return self.compose(items)

    def _text_shape(self, record, layer_name, step):
        """The geometry of a T record in mm: glyphs in the text frame, then the pad transform and the insertion."""
        font = self._stroke_font()
        width = record.width_factor * 12 * _MIL_MM
        if width <= 0 or record.xsize <= 0 or record.ysize <= 0:
            self.parser._count('Text records with a zero size or stroke width were skipped: %d')
            return None
        sx, sy = record.xsize / font.xsize, record.ysize / font.ysize
        advance = (font.xsize + font.offset) * sx
        text = self._dynamic_text(record, layer_name, step)
        shift = 0.0
        if record.version == 1 and text[:1] in font.bounds:
            box_ = font.bounds[text[0]]
            shift = (font.widest / 2 - ((box_[0] + box_[2]) / 2 - font.origin[0])) * sx
        cache, parts = {}, []
        for index, char in enumerate(text):
            if char in font.glyphs:
                if char not in cache:
                    cache[char] = self._glyph_shape(font, char, sx, sy, width)
                if not cache[char].is_empty:
                    parts.append(affinity.translate(cache[char], index * advance + shift, 0))
        if not parts:
            return None
        shape = unary_union(parts)
        if record.angle:
            shape = affinity.rotate(shape, -record.angle, origin=(0, 0))
        if record.mirror:
            shape = affinity.scale(shape, xfact=-1, yfact=1, origin=(0, 0))
        return affinity.translate(shape, record.x, record.y)

    def text_warnings(self):
        """The warnings of the text records of the build (call once, after all streams)."""
        warnings = self.parser.warnings
        if self._font_fallback:
            warnings.append('The job has no usable fonts/standard file: text is drawn with a built-in stroke font')
        if self._date_variables:
            warnings.append('Text with the variables %s shows the import date and time (the fab can print a '
                            'different date)' % ', '.join('$$' + name for name in sorted(self._date_variables)))

    # ---------------------------------------------------------------------------------------------- shapes
    def _surface_shape(self, rec):
        steps = self.parser._steps
        result = Polygon()
        group, hole = [], None

        def flush():
            nonlocal result, group
            if group:
                merged = unary_union(group)
                result = result.union(merged) if not hole else result.difference(merged)
                group = []

        for poly in rec.polys:
            points = [poly.start]
            for seg in poly.segs:
                if seg[0] == 'S':
                    points.append((seg[1], seg[2]))
                else:
                    _tag, ex, ey, cx, cy, cw = seg
                    radius = math.hypot(points[-1][0] - cx, points[-1][1] - cy)
                    points.extend(_arc_points(points[-1], (ex, ey), (cx, cy), cw, steps(radius))[1:])
            if len(points) < 3:
                continue
            shape = Polygon(points)
            if not shape.is_valid:
                self.parser._count('Invalid polygons were repaired: %d')
                shape = shape.buffer(0)
            if shape.is_empty:
                continue
            if hole is not None and poly.hole != hole:
                flush()
            hole = poly.hole
            group.append(shape)
        flush()
        return result

    def _sweep(self, kind, size, points):
        if size <= 0:
            return Polygon()
        half = size / 2
        if kind == 'r':
            if len(set(points)) == 1:
                return Point(points[0]).buffer(half, quad_segs=self.quad(half))
            return LineString(points).buffer(half, quad_segs=self.quad(half))
        corners = [(-half, -half), (half, -half), (half, half), (-half, half)]

        def square(p):
            return [(p[0] + dx, p[1] + dy) for dx, dy in corners]

        if len(set(points)) == 1:
            return Polygon(square(points[0]))
        return unary_union([MultiPoint(square(a) + square(b)).convex_hull for a, b in zip(points, points[1:])])

    def _pad_shape(self, rec, ff):
        entry = ff.symbol_factor(rec.symbol)
        if entry is None:
            self.parser._count('Features with an undefined symbol index were skipped: %d')
            return None
        name, factor = entry
        shape = self.symbols.shape(name, factor)
        if shape is None or shape.is_empty:
            return None
        if rec.resize:
            delta = rec.resize * ff.size_unit
            shape = shape.buffer(delta / 2, join_style=2, quad_segs=self.quad(abs(delta) / 2 or 1e-3))
            if shape.is_empty:
                return None
        if rec.angle:
            shape = affinity.rotate(shape, -rec.angle, origin=(0, 0))
        if rec.mirror:
            shape = affinity.scale(shape, xfact=-1, yfact=1, origin=(0, 0))
        return affinity.translate(shape, rec.x, rec.y)

    def _line_points(self, rec):
        if rec.kind == 'L':
            return [(rec.xs, rec.ys), (rec.xe, rec.ye)]
        radius = math.hypot(rec.xs - rec.xc, rec.ys - rec.yc)
        return _arc_points((rec.xs, rec.ys), (rec.xe, rec.ye), (rec.xc, rec.yc), rec.cw, self.parser._steps(radius))

    def file_items(self, ff, step, layer_name):
        """Stage A of one feature file: the ordered items (polarity, shape) in mm, in the file's own coordinates."""
        parser = self.parser
        items = []
        if ff.barcodes:
            parser._count('Barcode records are not drawn: %d', None, ff.barcodes)
        for rec in ff.records:
            pol = 'N' if rec.pol == 'N' else 'P'
            if rec.kind == 'P':
                shape = self._pad_shape(rec, ff)
                if shape is not None:
                    items.append(_Item(pol, shape))
            elif rec.kind in ('L', 'A'):
                entry = ff.symbol_factor(rec.symbol)
                if entry is None:
                    parser._count('Features with an undefined symbol index were skipped: %d')
                    continue
                sweep = self.symbols.sweep_kind(*entry)
                if sweep is None:
                    parser._count('Lines and arcs drawn with the symbol %s were skipped (only round and square '
                                  'symbols are allowed): %d', entry[0])
                    continue
                points = self._line_points(rec)
                shape = self._sweep(sweep[0], sweep[1], points)
                if shape.is_empty:
                    continue
                follow = LineString(points) if len(set(points)) > 1 else None
                items.append(_Item(pol, shape, follow))
            elif rec.kind == 'S':
                shape = self._surface_shape(rec)
                if not shape.is_empty:
                    items.append(_Item(pol, shape))
            elif rec.kind == 'T':
                shape = self._text_shape(rec, layer_name, step)
                if shape is not None and not shape.is_empty:
                    items.append(_Item('N' if rec.pol == 'N' else 'P', shape))
        return items

    def compose(self, items):
        """Stage B: consecutive items of one polarity are united once, then added or erased."""
        result = Polygon()
        group, polarity = [], None

        def flush():
            nonlocal result, group
            if group:
                merged = unary_union(group)
                result = result.union(merged) if polarity == 'P' else result.difference(merged)
                group = []

        for item in items:
            if item.pol != polarity:
                flush()
                polarity = item.pol
            group.append(item.shape)
        flush()
        if not result.is_valid:
            result = result.buffer(0)
        return result

    # ---------------------------------------------------------------------------------------------- profiles
    def step_profile(self, step):
        if step not in self._profiles:
            ff = self.feature_file('steps/%s/profile' % step)
            shape = Polygon()
            if ff is not None:
                shape = unary_union([self._surface_shape(rec) for rec in ff.records if rec.kind == 'S'] or [Polygon()])
            self._profiles[step] = shape
        return self._profiles[step]

    def boundary(self, step, layer_name):
        """The effective boundary of a layer: the layer profile minus the step profile holes, else the step profile."""
        key = (step, layer_name)
        if key not in self._boundaries:
            step_shape = self.step_profile(step)
            ff = self.feature_file('steps/%s/layers/%s/profile' % (step, layer_name))
            if ff is None:
                shape = step_shape
            else:
                shape = unary_union([self._surface_shape(rec) for rec in ff.records if rec.kind == 'S']
                                    or [Polygon()])
                holes = [Polygon(ring) for poly in _polygons(step_shape) for ring in poly.interiors]
                if holes and not shape.is_empty:
                    shape = shape.difference(unary_union(holes))
            if not shape.is_valid:
                shape = shape.buffer(0)
            self._boundaries[key] = shape
        return self._boundaries[key]

    # ---------------------------------------------------------------------------------------------- streams
    def layer_stream(self, step, layer):
        """Stage A: the own items of a step layer (negative layers: boundary first, polarity inverted)."""
        key = (step, layer['name'])
        if key in self._streams:
            return self._streams[key]
        ff = self.feature_file('steps/%s/layers/%s/features' % (step, layer['name']))
        items = [] if ff is None else self.file_items(ff, step, layer['name'])
        if ff is not None and layer['polarity'] == 'NEGATIVE':
            boundary = self.boundary(step, layer['name'])
            if boundary.is_empty or boundary.area <= 0:
                self.parser.warnings.append('Layer %s (step %s) is a negative layer without a usable profile and is '
                                            'not imported' % (layer['name'], step))
                items = []
            else:
                inverted = [_Item('P', boundary)]
                for item in items:
                    shape = item.shape.intersection(boundary)
                    if not shape.is_empty:
                        inverted.append(_Item('N' if item.pol == 'P' else 'P', shape))
                items = inverted
        self._streams[key] = items
        return items

    def _copies(self, step, stack):
        """[(child step, [matrix of each copy] in fill order)] for the STEP-REPEAT blocks of a step."""
        cache_key = (step, stack)
        if cache_key in self._copy_cache:
            return self._copy_cache[cache_key]
        parser = self.parser
        out = []
        self._copy_cache[cache_key] = out
        for rep in parser.step_repeats.get(step, []):
            child = next((name for name in parser.steps if name.lower() == rep.get('NAME', '').lower()), None)
            if child is None:
                parser._count('Step-repeat of the unknown step %s was skipped: %d', rep.get('NAME', ''))
                continue
            if child == step or child in stack:
                parser._count('Step-repeat loop with the step %s was skipped: %d', child)
                continue
            if rep.get('FLIP', 'NO').upper() == 'YES':
                parser._count('Step-repeat of %s with FLIP=YES is not supported and was skipped: %d', child)
                continue
            try:
                scale = parser._step_header(step).scale
                x, y = float(rep.get('X', '0')) * scale, float(rep.get('Y', '0')) * scale
                dx, dy = float(rep.get('DX', '0')) * scale, float(rep.get('DY', '0')) * scale
                nx, ny = int(float(rep.get('NX', '1'))), int(float(rep.get('NY', '1')))
                angle = math.radians(float(rep.get('ANGLE', '0') or 0))
            except ValueError:
                parser._count('Bad step-repeat of %s was skipped: %d', child)
                continue
            header = parser._step_header(child)
            mirror = -1.0 if rep.get('MIRROR', 'NO').upper() == 'YES' else 1.0
            cos, sin = math.cos(angle), math.sin(angle)
            a, b, d, e = mirror * cos, mirror * sin, -sin, cos
            matrices = []
            for j in range(max(ny, 0)):
                for i in range(max(nx, 0)):
                    tx, ty = x + i * dx, y + j * dy
                    matrices.append((a, b, d, e, tx - (a * header.xd + b * header.yd),
                                     ty - (d * header.xd + e * header.yd)))
            out.append((child, matrices))
        return out

    def full_stream(self, step, layer, stack=()):
        """The own items of the step, then the transformed streams of the child steps in file order."""
        key = (step, layer['name'])
        if key in self._full:
            return self._full[key]
        items = list(self.layer_stream(step, layer))
        for child, matrices in self._copies(step, stack + (step,)):
            child_items = self.full_stream(child, layer, stack + (step,))
            for matrix in matrices:
                for item in child_items:
                    items.append(_Item(item.pol, affinity.affine_transform(item.shape, matrix),
                                       None if item.follow is None else affinity.affine_transform(item.follow, matrix)))
        self._full[key] = items
        return items

    # ---------------------------------------------------------------------------------------------- drills
    def _tools_table(self, step, layer_name):
        key = (step, layer_name)
        if key not in self._tools:
            table = {}
            text = self.job.read_text('steps/%s/layers/%s/tools' % (step, layer_name))
            if text:
                data = _read_struct(text)
                factor = _MIL_MM if data.get('UNITS', self.parser.units).upper() != 'MM' else _MICRON_MM
                for tool in data.get('TOOLS', []):
                    try:
                        num = int(float(tool.get('NUM', '')))
                    except ValueError:
                        continue
                    size = 0.0
                    for field in ('DRILL_SIZE', 'FINISH_SIZE'):
                        try:
                            size = float(tool.get(field, '')) * factor
                        except ValueError:
                            size = 0.0
                        if size > 0:
                            break
                    kind = tool.get('TYPE', '').upper()
                    plating = {'PLATED': 'PTH', 'VIA': 'PTH', 'NON_PLATED': 'NPTH'}.get(kind)
                    table[num] = (plating, size if size > 0 else None)
            self._tools[key] = table
        return self._tools[key]

    @staticmethod
    def _attr_plating(attrs):
        value = attrs.get('.drill')
        if value is None or value is True:
            return None
        text = str(value).strip().lower()
        return {'0': 'PTH', '1': 'NPTH', '2': 'PTH', 'plated': 'PTH', 'via': 'PTH', 'non_plated': 'NPTH'}.get(text)

    def drill_layer_stream(self, step, layer):
        key = (step, layer['name'])
        if key in self._drill_streams:
            return self._drill_streams[key]
        parser = self.parser
        ff = self.feature_file('steps/%s/layers/%s/features' % (step, layer['name']))
        out = []
        if ff is not None:
            tools = self._tools_table(step, layer['name'])
            for rec in ff.records:
                if rec.kind not in ('P', 'L') or rec.pol == 'N':
                    if rec.kind in ('A', 'S'):
                        parser._count('Arcs and surfaces in the drill layer %s were skipped: %d', layer['name'])
                    continue
                entry = ff.symbol_factor(rec.symbol)
                info = None if entry is None else self.symbols.drill_symbol(*entry)
                if info == 'null':
                    continue
                if info is None:
                    parser._count('Drill features with the symbol %s were skipped: %d', entry[0] if entry else '?')
                    continue
                dia, hint, half = info
                if half is not None and rec.kind != 'P':
                    parser._count('Drill features with the symbol %s were skipped: %d', entry[0])
                    continue
                tool_plating, tool_size = tools.get(rec.dcode, (None, None))
                if dia <= 0 and tool_size:
                    dia = tool_size
                if rec.kind == 'P' and rec.resize and dia > 0:
                    # Same as the pad shape: the resize grows the symbol by 'resize' size units (an oval slot
                    # gets wider and longer by the same amount, so its half axis stays).
                    dia += rec.resize * ff.size_unit
                    if dia <= 0:
                        parser._count('Resized drill features without a size were skipped: %d')
                        continue
                if dia <= 0:
                    parser._count('Drill features without a size were skipped: %d')
                    continue
                plating = tool_plating or self._attr_plating(rec.attrs) or hint or 'PTH'
                if rec.kind == 'P' and half is not None:
                    # Same transform as a pad: clockwise rotation, then mirror in X.
                    rad = math.radians(rec.angle or 0.0)
                    hx = half[0] * math.cos(rad) + half[1] * math.sin(rad)
                    hy = -half[0] * math.sin(rad) + half[1] * math.cos(rad)
                    if rec.mirror:
                        hx = -hx
                    out.append(_Drill([(rec.x - hx, rec.y - hy), (rec.x + hx, rec.y + hy)], dia, plating))
                elif rec.kind == 'P':
                    out.append(_Drill([(rec.x, rec.y)], dia, plating))
                else:
                    # A drill line is a slot. The spec does not say it; this is common practice for ODB++ drills.
                    points = [(rec.xs, rec.ys), (rec.xe, rec.ye)]
                    out.append(_Drill(points if points[0] != points[1] else points[:1], dia, plating))
        self._drill_streams[key] = out
        return out

    def drill_full_stream(self, step, layer, stack=()):
        key = (step, layer['name'])
        if key in self._drill_full:
            return self._drill_full[key]
        items = list(self.drill_layer_stream(step, layer))
        for child, matrices in self._copies(step, stack + (step,)):
            child_items = self.drill_full_stream(child, layer, stack + (step,))
            for a, b, d, e, xoff, yoff in matrices:
                for item in child_items:
                    items.append(_Drill([(a * px + b * py + xoff, d * px + e * py + yoff) for px, py in item.pts],
                                        item.dia, item.plating))
        self._drill_full[key] = items
        return items


# ----------------------------------------------------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------------------------------------------------

class ODBParser:
    """
    parse_file(path, step=None) -> self, with the attributes of DipTraceParser plus source_kind, steps,
    default_step and step. More than one step and step=None: a metadata result (no geometry).
    """

    GERBER_TYPES = ('SIGNAL', 'POWER_GROUND', 'MIXED', 'SOLDER_MASK', 'SOLDER_PASTE', 'SILK_SCREEN')
    LISTED_TYPES = ('DOCUMENT', 'COMPONENT', 'MISC')

    def __init__(self, app=None, circle_steps=None):
        if circle_steps is not None and (not isinstance(circle_steps, int) or circle_steps < 16):
            raise ValueError('circle_steps must be an integer of at least 16')
        self.app = app
        self._options = getattr(app, 'options', None)
        self.circle_steps = circle_steps
        self._reset()

    def _reset(self):
        self.source_kind = 'odb'
        self.layers = {}
        self.drill_groups = {}
        self.default_layers = []
        self.default_drill_groups = None
        self.warnings = []
        self.board_profile = Polygon()
        self.board_outline = self.board_profile
        self.steps = []
        self.default_step = None
        self.step = None
        self.name = ''
        self.job = None
        self.units = 'INCH'
        self.matrix_layers = []
        self.step_repeats = {}

    # ------------------------------------------------------------------------------------------------ entry
    def parse_file(self, path, step=None):
        job = ODBJob.open(path)
        try:
            return self.parse_job(job, step=step)
        finally:
            job.close()

    def parse_job(self, job, step=None):
        self._reset()
        self.job = job
        text = job.read_text('matrix/matrix')
        if text is None:
            raise ODBParseError('No matrix/matrix file in the ODB++ job')
        matrix = _read_struct(text)
        info = _read_struct(job.read_text('misc/info') or '')
        self.units = 'MM' if info.get('UNITS', '').upper() == 'MM' else 'INCH'
        # KiCad writes JOB_NAME=job for each board; then the archive or folder name tells the boards apart.
        job_name = info.get('JOB_NAME', '')
        if job_name.lower() == 'job':
            job_name = ''
        self.name = (job_name or info.get('PRODUCT_MODEL_NAME', '') or getattr(job, 'name', '') or 'odb')

        steps = []
        for entry in matrix.get('STEP', []):
            name = check_entity_name(entry.get('NAME', ''), 'step')
            try:
                column = int(entry.get('COL', '0'))
            except ValueError:
                column = 0
            steps.append((column, name))
        steps = [name for _column, name in sorted(steps, key=lambda item: item[0])]
        if not steps:
            raise ODBParseError('The ODB++ matrix has no step')
        self.steps = steps

        layers = []
        for entry in matrix.get('LAYER', []):
            name = check_entity_name(entry.get('NAME', ''), 'layer')
            try:
                row = int(entry.get('ROW', '0'))
            except ValueError:
                row = 0
            layers.append({'row': row, 'name': name,
                           'context': entry.get('CONTEXT', '').upper(),
                           'type': entry.get('TYPE', '').upper(),
                           'polarity': entry.get('POLARITY', 'POSITIVE').upper() or 'POSITIVE',
                           'start': entry.get('START_NAME', ''),
                           'end': entry.get('END_NAME', ''),
                           'add_type': entry.get('ADD_TYPE', '').upper()})
        layers.sort(key=lambda item: item['row'])
        self.matrix_layers = layers

        for name in steps:
            header = _read_struct(job.read_text('steps/%s/stephdr' % name) or '')
            self.step_repeats[name] = header.get('STEP-REPEAT', [])
        plain = [name for name in steps if not self.step_repeats[name]]
        self.default_step = next((name for name in plain if name.lower() == 'pcb'), plain[0] if plain else steps[0])

        if step is None:
            if len(steps) > 1:
                return self        # metadata result: the app asks for the step
            step = steps[0]
        chosen = next((name for name in steps if name.lower() == str(step).lower()), None)
        if chosen is None:
            raise ODBParseError('No step %r in the ODB++ job' % step)
        self.step = chosen
        self._build_step(chosen)
        self.warnings = list(dict.fromkeys(self.warnings))
        return self

    # ------------------------------------------------------------------------------------------------ helpers
    def _steps(self, radius):
        """Segments of a whole circle of this radius (mm): the fixed override or the Gerber preference."""
        if self.circle_steps is not None:
            return self.circle_steps
        return gerber_circle_steps(self._options, radius, 'MM')

    def _quad(self, radius):
        return circle_quad_segs(self._steps(radius))

    def _count(self, fmt, arg=None, amount=1):
        """Count a skipped feature; one warning line with the total per kind is made at the end of the build."""
        key = (fmt, arg)
        self._counts[key] = self._counts.get(key, 0) + amount

    def _flush_counts(self):
        for (fmt, arg), total in self._counts.items():
            self.warnings.append(fmt % ((arg, total) if arg is not None else (total,)))
        self._counts = {}

    def _step_header(self, step):
        """Datum of a step in mm and the mm of one unit of its stephdr."""
        if step not in self._headers:
            header = _read_struct(self.job.read_text('steps/%s/stephdr' % step) or '')
            units = header.get('UNITS', self.units).upper()
            scale = 1.0 if units == 'MM' else _INCH_MM
            try:
                xd, yd = float(header.get('X_DATUM', '0') or 0) * scale, float(header.get('Y_DATUM', '0') or 0) * scale
            except ValueError:
                xd = yd = 0.0
            self._headers[step] = SimpleNamespace(scale=scale, xd=xd, yd=yd)
        return self._headers[step]

    # ------------------------------------------------------------------------------------------------ build
    def _build_step(self, step):
        """Fill layers, drill_groups, default_layers and board_profile for one step."""
        self._counts = {}
        self._headers = {}
        renderer = _Renderer(self)
        self.board_profile = renderer.step_profile(step)
        self.board_outline = self.board_profile
        default_layers = []
        for entry in self.matrix_layers:
            kind, on = self._layer_kind(entry)
            if kind is None:
                continue
            if kind == 'drill':
                self._build_drill(renderer, step, entry)
                continue
            items = renderer.full_stream(step, entry)
            solid = _polygons(renderer.compose(items))
            follow = [item.follow for item in items if item.pol == 'P' and item.follow is not None]
            if entry['polarity'] == 'NEGATIVE':
                follow = []
            if not solid and not follow:
                continue
            self._add_layer(entry['name'], solid, follow)
            if on:
                default_layers.append(entry['name'])
        self._build_profile(step, default_layers)
        self.default_layers = default_layers
        self.default_drill_groups = list(self.drill_groups)
        renderer.text_warnings()
        self._flush_counts()

    def _layer_kind(self, entry):
        """(kind, default on) of a matrix layer: kind is 'gerber', 'drill' or None (not imported)."""
        context, kind = entry['context'], entry['type']
        if kind == 'DIELECTRIC':
            return None, False
        if kind == 'DRILL' and context != 'MISC':
            return 'drill', True
        if context == 'BOARD' and kind in self.GERBER_TYPES:
            return 'gerber', True
        return 'gerber', False      # ROUT, DOCUMENT, COMPONENT, MISC and the types without a rule

    def _add_layer(self, name, polygons, follow):
        self.layers[name] = {'name': name, 'solid_geometry': polygons, 'follow_geometry': follow,
                             'tools': {0: {'type': 'REG', 'size': 0.0,
                                           'geometry': [{'solid': polygon} for polygon in polygons]}}}

    def _build_profile(self, step, default_layers):
        """The layer 'profile': a thin outline of the step profile rings."""
        rings = []
        for polygon in _polygons(self.board_profile):
            rings.append(polygon.exterior)
            rings.extend(polygon.interiors)
        if not rings:
            return
        name = 'profile'
        while name in self.layers or any(entry['name'] == name for entry in self.matrix_layers):
            name += ' (outline)'
        solid = _polygons(unary_union([ring.buffer(PROFILE_WIDTH_MM / 2, join_style=2) for ring in rings]))
        self._add_layer(name, solid, [LineString(ring.coords) for ring in rings])
        default_layers.append(name)

    def _build_drill(self, renderer, step, entry):
        drills = renderer.drill_full_stream(step, entry)
        span = ''
        if entry['start'] and entry['end']:
            span = ' %s-%s' % (entry['start'], entry['end'])
        for plating in ('PTH', 'NPTH'):
            tools = {}
            for drill in drills:
                if drill.plating != plating:
                    continue
                tool = tools.setdefault(round(drill.dia, 6), {'tooldia': round(drill.dia, 6), 'drills': [],
                                                              'slots': []})
                if len(drill.pts) == 1:
                    tool['drills'].append(Point(drill.pts[0]))
                else:
                    tool['slots'].append((Point(drill.pts[0]), Point(drill.pts[1])))
            if tools:
                self.drill_groups['%s%s %s' % (entry['name'], span, plating)] = {
                    i: tool for i, (_dia, tool) in enumerate(sorted(tools.items()), 1)}
