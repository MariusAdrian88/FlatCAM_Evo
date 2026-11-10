# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Native Altium CircuitStudio / CircuitMaker board reader  #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                             #
# ##########################################################

"""Read the .CSPcbDoc boards of Altium CircuitStudio and Altium CircuitMaker.

A .CSPcbDoc is the PCB6 binary format that ParsePcbDoc reads, but the storages have GUID names in place of
'Board6', 'Tracks6', ... This reader finds the role of each stream from its content; ParsePcbDoc does the rest.
"""

import struct

from appParsers.ParsePcbDoc import PcbDocParser, PcbDocParseError, _Reader


class CSPcbDocParser(PcbDocParser):
    # Stream roles found by content: a key of the first property record that only this stream has.
    ROLE_KEYS = (('Board6', 'ORIGINX'), ('Polygons6', 'POURINDEX'), ('Nets6', 'LOOPREMOVAL'),
                 ('Components6', 'SOURCEDESIGNATOR'), ('ExtendedPrimitiveInformation', 'TYPE'))
    # The same for the property streams that have a 2-byte prefix before each record.
    ROLE_PREFIXED_KEYS = (('Rules6', 'RULEKIND'), ('Dimensions6', 'DIMENSIONKIND'))
    ROLE_TAGS = {1: 'Arcs6', 2: 'Pads6', 3: 'Vias6', 4: 'Tracks6', 5: 'Texts6', 6: 'Fills6'}
    # The 'Texts' stream holds only this note for old readers; its records have the tag of Texts6.
    LEGACY_TEXT_NOTE = b'This is a version 6.0 file'

    def parse_file(self, filename):
        import olefile

        self._reset()
        try:
            with olefile.OleFileIO(str(filename), raise_defects=olefile.DEFECT_INCORRECT) as container:
                roles = {}
                if not container.exists(['Board6', 'Data']):
                    roles = self._stream_roles({entry[0]: container.openstream(entry).read()
                                                for entry in container.listdir()
                                                if len(entry) == 2 and entry[1] == 'Data'})
                streams = {}
                counts = {}
                for role in (*self.PROPERTIES, *self.BINARY, 'WideStrings6', 'EmbeddedFonts6'):
                    name = roles.get(role, role)
                    if not container.exists([name, 'Data']):
                        continue
                    streams[role] = container.openstream([name, 'Data']).read()
                    if role != 'WideStrings6' or container.exists([name, 'Header']):
                        if not container.exists([name, 'Header']):
                            raise PcbDocParseError('%s: missing Header' % role)
                        header = container.openstream([name, 'Header']).read()
                        if len(header) != 4:
                            raise PcbDocParseError('%s: invalid Header length' % role)
                        counts[role] = struct.unpack('<I', header)[0]
                return self._parse_streams(streams, counts)
        except PcbDocParseError:
            self.layers = {}
            self.drill_groups = {}
            raise
        except Exception as error:
            self.layers = {}
            self.drill_groups = {}
            raise PcbDocParseError('Cannot read PCB6 container: %s' % error) from error

    @classmethod
    def _stream_roles(cls, storages):
        """
        Find the stream of each role (Board6, Tracks6, ...) from the content only: storage name -> Data bytes in,
        role -> storage name out. Empty streams and streams the parser does not read get no role.
        """
        roles = {}
        for name, data in storages.items():
            role = cls._stream_role(data)
            if role is None:
                continue
            if role in roles:
                raise PcbDocParseError('Two streams look like %s: %s and %s' % (role, roles[role], name))
            roles[role] = name
        return roles

    @classmethod
    def _stream_role(cls, data):
        def first_block(offset):
            if len(data) < offset + 4:
                return None
            length = struct.unpack_from('<I', data, offset)[0]
            if offset + 4 + length > len(data):
                return None
            return data[offset + 4:offset + 4 + length]

        for skip, keys in ((0, cls.ROLE_KEYS), (2, cls.ROLE_PREFIXED_KEYS)):
            record = first_block(skip)
            if record and record[:1] == b'|':
                props = cls._props(record)
                for role, key in keys:
                    if key in props and (role != 'ExtendedPrimitiveInformation' or 'PRIMITIVEOBJECTID' in props):
                        return role
                return None
        if cls._looks_like_fonts(data):
            return 'EmbeddedFonts6'
        if data[:4] == b'\0\0\0\0':
            text = first_block(4)
            return 'WideStrings6' if text is not None and len(text) % 2 == 0 else None
        record = first_block(1)
        if record is None:
            return None
        tag = data[0]
        if tag == 5 and cls.LEGACY_TEXT_NOTE in data[5 + len(record):5 + len(record) + 200]:
            return None
        if tag in cls.ROLE_TAGS:
            return cls.ROLE_TAGS[tag]
        if tag == 11:
            return cls._region_role(record)
        return None

    @staticmethod
    def _looks_like_fonts(data):
        # family, full name, style (UTF-16), 3 bytes, zlib data of the font
        reader = _Reader(data, 'EmbeddedFonts6')
        try:
            for _ in range(3):
                if len(reader.block()) % 2:
                    return False
            reader.take(3)
            return reader.block()[:1] == b'\x78'
        except PcbDocParseError:
            return False

    @classmethod
    def _region_role(cls, record):
        """BoardRegions, Regions6 (plain vertices) or ShapeBasedRegions6 (vertices with arcs) from the record size."""
        try:
            reader = _Reader(record, 'Region')
            header = reader.take(18)
            props = cls._props(reader.block())
            if props.get('OBJECTKIND') == 'BoardRegion':
                return 'BoardRegions'
            count = reader.unpack('I')[0]
            start = reader.pos
        except PcbDocParseError:
            return None
        holes = struct.unpack_from('<H', header, 14)[0]
        # the outline vertices: 16 bytes each (Regions6), or 37 bytes each and one more (ShapeBasedRegions6);
        # the holes have 16-byte vertices in both streams
        for role, outline in (('Regions6', count * 16), ('ShapeBasedRegions6', (count + 1) * 37)):
            position = start + outline
            for _ in range(holes):
                if position + 4 > len(record):
                    break
                position += 4 + struct.unpack_from('<I', record, position)[0] * 16
            if position == len(record):
                return role
        return None
