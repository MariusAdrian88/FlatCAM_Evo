# ##########################################################
# FlatCAM Evo: native Proteus project container/version facade #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""One parsed board per embedded LYT; no CAD application or CAM exports needed.

parse_file() returns this result: boards, warnings, firmware and major_version (also
exposed as detected_major_version). Each board has name, member_name, layers, drill_groups,
warnings and default_layers. firmware lists (path, text) for program source files. Verified layer keys carry Altium filename extensions;
their display names remain in each layer's name field. Coordinates and tool diameters are millimetres.
The caller owns project creation/selection; this facade has no UI side effects.
"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET

from appParsers.ParseProteus8 import Proteus8Parser, Proteus9Parser, ProteusParseError


class ProteusParser:
    # Keep version routing here; the decoders share only verified wire primitives.
    DECODERS = {8: Proteus8Parser, 9: Proteus9Parser}
    MAX_MEMBER_BYTES = 64 * 1024 * 1024
    MAX_BOARD_BYTES = 256 * 1024 * 1024
    MAX_SOURCE_BYTES = 4 * 1024 * 1024
    # Program source only; build outputs (HEX, ELF, COF, LST) are not code.
    SOURCE_EXTENSIONS = {'.c', '.h', '.cpp', '.cc', '.cxx', '.hpp', '.hh', '.hxx', '.ino', '.pde', '.asm',
                         '.s', '.a51', '.inc', '.bas', '.pas', '.py', '.java', '.js', '.ts', '.lua', '.rs',
                         '.go', '.vhd', '.vhdl', '.v', '.sv'}

    def __init__(self, app=None):
        self.app = app
        self.boards = []
        self.warnings = []
        self.firmware = []
        self.major_version = None

    @property
    def detected_major_version(self):
        return self.major_version

    def parse_file(self, filename):
        self.boards = []
        self.warnings = []
        self.firmware = []
        self.major_version = None
        try:
            with zipfile.ZipFile(str(filename)) as archive:
                files = {}
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    key = member.filename.replace('\\', '/').upper()
                    if key in files:
                        raise ProteusParseError('Duplicate Proteus archive member %s' % member.filename)
                    files[key] = member
                if 'PROJECT.XML' not in files:
                    raise ProteusParseError('Proteus project has no PROJECT.XML version metadata')
                metadata = self._read_member(archive, files['PROJECT.XML'])
                self.major_version = self._version(metadata)
                decoder = self.DECODERS.get(self.major_version)
                if decoder is None:
                    raise ProteusParseError('Unsupported Proteus major version %d' % self.major_version)
                members = [member for key, member in files.items() if key.endswith('.LYT')]
                members.sort(key=lambda member: self._member_order(member.filename))
                if not members:
                    raise ProteusParseError('Proteus project contains no embedded LYT boards')
                if sum(member.file_size for member in members) > self.MAX_BOARD_BYTES:
                    raise ProteusParseError('Proteus boards exceed the uncompressed size limit')
                boards = []
                for member in members:
                    data = self._read_member(archive, member)
                    try:
                        board = decoder(app=self.app).parse_bytes(data, member_name=member.filename)
                    except ProteusParseError as error:
                        if member.filename in str(error):
                            raise
                        raise ProteusParseError('%s: %s' % (member.filename, error)) from error
                    except Exception as error:
                        raise ProteusParseError('%s: cannot decode board: %s' % (member.filename, error)) from error
                    boards.append(board)
                    self.warnings.extend('%s: %s' % (member.filename, warning) for warning in board.warnings)
                self.boards = boards
                self.firmware = self._firmware(archive, files, os.path.dirname(os.path.abspath(str(filename))))
        except ProteusParseError:
            self.boards = []
            self.firmware = []
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ET.ParseError) as error:
            self.boards = []
            raise ProteusParseError('Cannot read Proteus project: %s' % error) from error
        return self

    def _firmware(self, archive, files, folder):
        """Embedded source members, then FIRMWARE.XML references found beside the project file."""
        sources = {}
        for key in sorted(files, key=self._member_order):
            if os.path.splitext(key)[1].lower() in self.SOURCE_EXTENSIONS:
                member = files[key]
                if member.file_size > self.MAX_SOURCE_BYTES:
                    self.warnings.append('Firmware source %s is too large; not imported' % member.filename)
                    continue
                sources[key] = (member.filename.replace('\\', '/'), self._source_text(
                    self._read_member(archive, member)))
        if 'FIRMWARE.XML' not in files:
            return list(sources.values())
        metadata = self._read_member(archive, files['FIRMWARE.XML'])
        if b'<!ENTITY' in metadata.upper() or b'<!DOCTYPE' in metadata.upper():
            self.warnings.append('FIRMWARE.XML has entity/DOCTYPE declarations; firmware references ignored')
            return list(sources.values())
        try:
            root = ET.fromstring(metadata)
        except ET.ParseError as error:
            self.warnings.append('FIRMWARE.XML cannot be read: %s' % error)
            return list(sources.values())
        # The firmware schema is not documented: accept any attribute or text naming a source file.
        for element in root.iter():
            for value in list(element.attrib.values()) + [element.text or '']:
                value = value.strip().replace('\\', '/')
                if os.path.splitext(value)[1].lower() not in self.SOURCE_EXTENSIONS:
                    continue
                if value.upper() in sources or value.upper().lstrip('/') in sources:
                    continue
                path = os.path.abspath(os.path.join(folder, value))
                key = os.path.normcase(path)
                if key in sources:
                    continue
                # Read only beside the project: a project file must not pull in arbitrary local files.
                if os.path.isabs(value) or not key.startswith(os.path.normcase(folder) + os.sep):
                    self.warnings.append('Firmware source %s is outside the project folder; not imported' % value)
                    continue
                try:
                    if os.path.getsize(path) > self.MAX_SOURCE_BYTES:
                        self.warnings.append('Firmware source %s is too large; not imported' % value)
                        continue
                    with open(path, 'rb') as handle:
                        sources[key] = (value, self._source_text(handle.read()))
                except OSError:
                    self.warnings.append('Firmware source %s is not embedded and was not found beside the '
                                         'project' % value)
        return list(sources.values())

    @staticmethod
    def _source_text(data):
        try:
            return data.decode('utf-8-sig')
        except UnicodeDecodeError:
            return data.decode('cp1252', errors='replace')

    @staticmethod
    def _member_order(name):
        parts = re.split(r'(\d+)', name.upper())
        return tuple((1, int(part)) if part.isdigit() else (0, part) for part in parts)

    def _read_member(self, archive, member):
        if member.file_size > self.MAX_MEMBER_BYTES:
            raise ProteusParseError('%s exceeds the uncompressed member size limit' % member.filename)
        try:
            with archive.open(member) as handle:
                data = handle.read(self.MAX_MEMBER_BYTES + 1)
        except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
            raise ProteusParseError('%s: %s' % (member.filename, error)) from error
        if len(data) != member.file_size or len(data) > self.MAX_MEMBER_BYTES:
            raise ProteusParseError('%s has an invalid uncompressed size' % member.filename)
        return data

    @staticmethod
    def _version(metadata):
        if b'<!ENTITY' in metadata.upper() or b'<!DOCTYPE' in metadata.upper():
            raise ProteusParseError('XML entity/DOCTYPE declarations are not supported')
        root = ET.fromstring(metadata)
        if root.tag != 'PROJECT':
            raise ProteusParseError('Invalid Proteus PROJECT.XML root')
        timestamp = root.find('TIMESTAMP')
        release = timestamp.get('RELEASE', '') if timestamp is not None else ''
        match = re.fullmatch(r'(\d+)\.(\d+)(?:\.\d+)?', release)
        if match:
            return int(match.group(1))
        if not release.isdigit() or len(release) < 3:
            raise ProteusParseError('Missing or invalid Proteus release version')
        # Native metadata packs 8.16 as 816, not the package wire FILEVER=847.
        return int(release[:-2])
